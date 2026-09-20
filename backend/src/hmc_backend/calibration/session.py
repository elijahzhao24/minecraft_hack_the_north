"""Bounded board calibration and fixed-rig freshness checks.

No body matching: independent PnP solves observe one stationary printed board.
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from uuid import uuid4

import numpy as np

from hmc_backend.calibration.charuco import (
    BoardSpec,
    CharucoError,
    build_board,
    compose_stage_from_optical,
    geodesic_angle_rad,
    observe_frame,
    solve_camera,
    stage_from_board,
    validate_solution,
)
from hmc_backend.calibration.model import RigCalibration, valid_rigid_transform
from hmc_backend.contracts.internal import CameraCalibration, CapturedFrame
from hmc_backend.reconstruction.registration import optical_up_from_arkit_pose

REQUIRED_OBSERVATIONS = 12

# Median angle between the solved camera's up direction and the phone's own
# gravity measurement. ARKit pitch/roll is accurate to ~1 deg, so a solution
# this far off is physically impossible for a correctly observed board.
MAX_GRAVITY_ERROR_DEG = 12.0


def frame_metadata(frame: CapturedFrame) -> dict:
    return {
        "session_id": str(frame.session_id),
        "rgb_size": [frame.rgb.shape[1], frame.rgb.shape[0]],
        "depth_size": [frame.depth_m.shape[1], frame.depth_m.shape[0]],
        "image_orientation": frame.image_orientation,
        "arkit_pose": frame.arkit_pose.reshape(-1).tolist(),
    }


def changed_frame(frame: CapturedFrame, baseline: dict) -> str | None:
    current = frame_metadata(frame)
    for key in ("session_id", "rgb_size", "depth_size", "image_orientation"):
        if current[key] != baseline.get(key):
            return f"{key}_changed"
    if not valid_rigid_transform(frame.arkit_pose):
        return "invalid_pose"
    pose = np.asarray(baseline["arkit_pose"], np.float64).reshape(4, 4)
    if not valid_rigid_transform(pose):
        return "invalid_baseline"
    if (np.linalg.norm(frame.arkit_pose[:3, 3] - pose[:3, 3]) > 0.02
            or np.degrees(geodesic_angle_rad(frame.arkit_pose[:3, :3], pose[:3, :3])) > 2):
        return "camera_moved"
    return None


class BoardCalibrationSession:
    def __init__(self, device_ids: tuple[str, ...]):
        self.device_ids = device_ids
        self.spec = BoardSpec()
        self.board, self.detector = build_board(self.spec)
        self.observations = {dev: [] for dev in device_ids}
        self.rejections = {dev: Counter() for dev in device_ids}
        self.baselines: dict[str, dict] = {}
        self.frames: dict[str, CapturedFrame] = {}
        self.seen = {dev: set() for dev in device_ids}
        self.state = "collecting"
        self.error: str | None = None
        self.result: RigCalibration | None = None

    def fail(self, reason: str) -> None:
        self.state, self.error = "failed", reason

    def status(self) -> dict:
        return {
            "state": self.state, "error": self.error,
            "instruction": "Place the stationary board face up on the floor; keep both phones still and step out.",
            "devices": {dev: {"accepted": len(self.observations[dev]), "required": REQUIRED_OBSERVATIONS,
                              "rejections": dict(self.rejections[dev])} for dev in self.device_ids},
            "calibration_id": str(self.result.calibration_id) if self.result else None,
        }

    def offer(self, frame: CapturedFrame) -> None:
        if self.state != "collecting" or frame.device_id not in self.observations:
            return
        dev = frame.device_id
        frame_key = (frame.session_id, frame.sequence)
        if frame_key in self.seen[dev]:
            self.rejections[dev]["duplicate_frame"] += 1
            return
        if frame.tracking_state != "normal":
            self.rejections[dev]["tracking_not_normal"] += 1
            return
        if not valid_rigid_transform(frame.arkit_pose):
            self.rejections[dev]["invalid_pose"] += 1
            return
        if dev in self.baselines and (reason := changed_frame(frame, self.baselines[dev])):
            self.fail(f"{dev}: {reason}; restart calibration without moving phones")
            return
        if len(self.observations[dev]) >= REQUIRED_OBSERVATIONS:
            return
        k = frame.K_rgb
        h, w = frame.rgb.shape[:2]
        if not (np.isfinite(k).all() and k[0, 0] > 0 and k[1, 1] > 0
                and 0 < k[0, 2] < w and 0 < k[1, 2] < h):
            self.rejections[dev]["invalid_intrinsics"] += 1
            return
        observation, reason = observe_frame(
            frame.rgb, k, self.board, self.detector, device_id=dev,
            sequence=frame.sequence, max_reprojection_px=2.0,
            up_optical=optical_up_from_arkit_pose(frame.arkit_pose),
        )
        if observation is None:
            self.rejections[dev][reason] += 1
            return
        self.baselines.setdefault(dev, frame_metadata(frame))
        self.frames[dev] = frame
        self.observations[dev].append(observation)
        self.seen[dev].add(frame_key)
        if all(len(obs) >= REQUIRED_OBSERVATIONS for obs in self.observations.values()):
            self.state = "validating"

    def solve(self) -> RigCalibration:
        if self.state != "validating":
            raise CharucoError("both phones need 12 accepted observations")
        calibration_id, created = uuid4(), datetime.now(UTC)
        cameras, validation = {}, {}
        for dev in self.device_ids:
            obs = self.observations[dev]
            train = [o for i, o in enumerate(obs) if i % 4 != 3]
            held = [o for i, o in enumerate(obs) if i % 4 == 3]
            solution = solve_camera(train, device_id=dev, max_angle_deg=2,
                                    rejection_reasons=dict(self.rejections[dev]))
            report = validate_solution(solution, held)
            # Check ALL observations, including rejected training outliers. A moved
            # board must not yield a plausible median pose and pass by coincidence.
            for o in obs:
                t = compose_stage_from_optical(o.R_camera_from_board, o.t_camera_from_board, stage_from_board())
                if (np.linalg.norm(t[:3, 3] - solution.T_stage_from_optical[:3, 3]) > 0.03
                        or np.degrees(geodesic_angle_rad(t[:3, :3], solution.T_stage_from_optical[:3, :3])) > 2):
                    raise CharucoError(f"{dev}: board observations moved or disagree; keep board stationary")
            if (report["max_position_error_m"] > 0.03 or report["max_rotation_error_deg"] > 2
                    or solution.max_reprojection_error_px > 2 or solution.camera_position_stage_m[1] <= 0
                    or not valid_rigid_transform(solution.T_stage_from_optical)):
                raise CharucoError(f"{dev}: board validation failed")
            # An identical pose bias in every frame is invisible to the
            # agreement checks above, but the phone measures gravity
            # independently of the board image, so it catches it.
            ups = [o.up_optical for o in obs if o.up_optical is not None]
            gravity_error_deg = None
            if ups:
                r_sol = solution.T_stage_from_optical[:3, :3]
                gravity_error_deg = float(np.median([
                    np.degrees(np.arccos(np.clip((r_sol @ u)[1], -1.0, 1.0))) for u in ups
                ]))
                if gravity_error_deg > MAX_GRAVITY_ERROR_DEG:
                    raise CharucoError(
                        f"{dev}: solved pose contradicts phone gravity by {gravity_error_deg:.0f} deg; "
                        "the board was probably seen too edge-on. Tip the phones toward the "
                        "board and recalibrate"
                    )
            frame = self.frames[dev]
            cameras[dev] = CameraCalibration(
                calibration_id, dev, (frame.rgb.shape[1], frame.rgb.shape[0]),
                (frame.depth_m.shape[1], frame.depth_m.shape[0]), frame.K_rgb.copy(),
                solution.T_stage_from_optical, solution.median_reprojection_error_px, created,
            )
            validation[dev] = {
                **report,
                "accepted_frames": len(obs),
                "baseline": self.baselines[dev],
                "camera_position_stage_m": solution.camera_position_stage_m.tolist(),
                **({"gravity_error_deg": gravity_error_deg} if gravity_error_deg is not None else {}),
            }
        return RigCalibration(calibration_id, created,
                              {"unit": "meter", "x": "board_right", "y": "up", "z": "toward_front_camera"},
                              self.spec.to_json(), cameras, validation)


class RigFreshness:
    def __init__(self, rig: RigCalibration):
        self.rig = rig
        self.movement_counts: dict[str, int] = {}
        self.invalid_reason: str | None = None
        if rig.board is not None and any("baseline" not in rig.validation.get(dev, {}) for dev in rig.device_ids()):
            self.invalid_reason = "calibration_missing_session_baseline"

    def observe(self, frame: CapturedFrame) -> str | None:
        if self.invalid_reason:
            return self.invalid_reason
        if self.rig.board is None:  # synthetic fixtures retain their explicit geometry
            return None
        baseline = self.rig.validation.get(frame.device_id, {}).get("baseline")
        if baseline is None:
            self.invalid_reason = "calibration_missing_session_baseline"
            return self.invalid_reason
        reason = changed_frame(frame, baseline)
        if reason == "camera_moved":
            if frame.tracking_state != "normal":
                self.movement_counts[frame.device_id] = 0
                return None
            count = self.movement_counts.get(frame.device_id, 0) + 1
            self.movement_counts[frame.device_id] = count
            if count < 3:
                return None
        else:
            self.movement_counts[frame.device_id] = 0
        if reason:
            self.invalid_reason = f"{frame.device_id}:{reason}"
        return self.invalid_reason
