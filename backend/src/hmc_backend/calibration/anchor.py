"""Shared-marker anchoring: solve and validate a persisted frame tree."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import numpy as np

from hmc_backend.calibration.charuco import (
    BoardObservation,
    BoardSpec,
    CharucoError,
    build_board,
    observe_frame,
    solve_camera,
    stage_from_board,
    validate_solution,
)
from hmc_backend.calibration.frame_tree import RigFrameTree
from hmc_backend.calibration.model import CalibrationError, RigCalibration
from hmc_backend.capture.rgbd_ingest import DecodedRgbd
from hmc_backend.contracts.internal import CameraCalibration
from hmc_backend.settings import Settings
from hmc_backend.transforms import StaticTransform, TransformBuffer, optical_frame


class AnchorError(CalibrationError):
    """Anchoring could not produce an acceptable frame tree."""


@dataclass
class DeviceAccumulator:
    """Board observations and raster metadata for one device during anchoring."""

    device_id: str
    observations: list[BoardObservation] = field(default_factory=list)
    rejections: dict[str, int] = field(default_factory=dict)
    k_rgb: np.ndarray | None = None
    rgb_size: tuple[int, int] | None = None
    depth_size: tuple[int, int] | None = None
    image_orientation: str | None = None

    def note_rejection(self, reason: str) -> None:
        self.rejections[reason] = self.rejections.get(reason, 0) + 1


def split_held_out(
    observations: list[BoardObservation], fraction: float
) -> tuple[list[BoardObservation], list[BoardObservation]]:
    """Deterministically hold out a fraction of frames for validation."""
    if fraction <= 0 or len(observations) < 4:
        return observations, []
    ordered = sorted(observations, key=lambda o: o.sequence)
    step = max(2, round(1.0 / fraction))
    held = [o for i, o in enumerate(ordered) if i % step == step - 1]
    train = [o for o in ordered if o not in held]
    if len(train) < 2:
        return ordered, []
    return train, held


def observe_decoded_frame(
    decoded: DecodedRgbd,
    *,
    board,
    detector,
    accumulator: DeviceAccumulator,
) -> bool:
    """Detect the board in one decoded frame and append to ``accumulator``."""
    header = decoded.header
    rgb_size = (header.rgb.width, header.rgb.height)
    depth_size = (header.depth.width, header.depth.height)
    orientation = header.image_orientation.value
    if accumulator.rgb_size is not None and accumulator.rgb_size != rgb_size:
        accumulator.note_rejection("rgb_size_changed")
        return False
    if accumulator.depth_size is not None and accumulator.depth_size != depth_size:
        accumulator.note_rejection("depth_size_changed")
        return False
    if accumulator.image_orientation is not None and accumulator.image_orientation != orientation:
        accumulator.note_rejection("image_orientation_changed")
        return False
    accumulator.k_rgb = decoded.k_rgb
    accumulator.rgb_size = rgb_size
    accumulator.depth_size = depth_size
    accumulator.image_orientation = orientation

    obs, reason = observe_frame(
        decoded.rgb,
        decoded.k_rgb,
        board,
        detector,
        device_id=accumulator.device_id,
        sequence=header.sequence,
    )
    if obs is None:
        accumulator.note_rejection(reason)
        return False
    accumulator.observations.append(obs)
    return True


def solve_frame_tree(
    accumulators: dict[str, DeviceAccumulator],
    settings: Settings,
    *,
    board_spec: BoardSpec | None = None,
    board_origin_stage_m: tuple[float, float, float] = (0.0, 0.0, 0.0),
    held_out_fraction: float = 0.25,
    min_held_out: int = 2,
) -> RigFrameTree:
    """Aggregate observations into a validated ``RigFrameTree``."""
    expected = set(settings.expected_device_ids)
    if set(accumulators) != expected:
        raise AnchorError(
            f"device mismatch: expected {sorted(expected)}, found {sorted(accumulators)}"
        )

    spec = board_spec or BoardSpec()
    t_stage_from_board = stage_from_board(board_origin_stage_m)
    calibration_id = uuid4()
    created = datetime.now(UTC)
    cameras: dict[str, CameraCalibration] = {}
    validation: dict[str, dict] = {}

    for device_id in sorted(accumulators):
        state = accumulators[device_id]
        if len(state.observations) < settings.anchor_sample_count:
            raise AnchorError(
                f"{device_id}: need {settings.anchor_sample_count} board frames, "
                f"got {len(state.observations)}"
            )
        if state.k_rgb is None or state.rgb_size is None or state.depth_size is None:
            raise AnchorError(f"{device_id}: missing raster metadata")

        train, held = split_held_out(state.observations, held_out_fraction)
        try:
            solution = solve_camera(
                train,
                device_id=device_id,
                t_stage_from_board=t_stage_from_board,
                rejection_reasons=state.rejections,
            )
        except CharucoError as exc:
            raise AnchorError(f"{device_id}: solve failed: {exc}") from exc

        report = validate_solution(solution, held, t_stage_from_board=t_stage_from_board)
        if solution.max_reprojection_error_px > settings.calibration_max_reprojection_error_px:
            raise AnchorError(
                f"{device_id}: reprojection {solution.max_reprojection_error_px:.2f}px exceeds "
                f"{settings.calibration_max_reprojection_error_px:.2f}px"
            )
        if report.get("held_out_frames", 0) < min_held_out:
            raise AnchorError(f"{device_id}: need at least {min_held_out} held-out frames")
        if report.get("max_position_error_m", float("inf")) > settings.calibration_max_position_error_m:
            raise AnchorError(
                f"{device_id}: held-out position error "
                f"{report.get('max_position_error_m', float('inf')):.3f}m exceeds "
                f"{settings.calibration_max_position_error_m:.3f}m"
            )
        pos = solution.camera_position_stage_m
        if pos[1] <= 0:
            raise AnchorError(f"{device_id}: camera solved at/below floor level")

        cameras[device_id] = CameraCalibration(
            calibration_id=calibration_id,
            device_id=device_id,
            rgb_size=state.rgb_size,
            depth_size=state.depth_size,
            K_rgb=state.k_rgb,
            T_stage_from_optical=solution.T_stage_from_optical,
            reprojection_error_px=solution.median_reprojection_error_px,
            created_at_utc=created,
            image_orientation=state.image_orientation or "landscape_right",
        )
        validation[device_id] = {
            "accepted_frames": solution.accepted_count,
            "median_reprojection_error_px": solution.median_reprojection_error_px,
            "max_reprojection_error_px": solution.max_reprojection_error_px,
            "rejection_reasons": solution.rejection_reasons,
            **report,
        }

    rig = RigCalibration(
        calibration_id=calibration_id,
        created_at_utc=created,
        stage_definition={
            "unit": "meter",
            "x": "front_camera_image_right",
            "y": "up",
            "z": "toward_front_camera",
        },
        board=spec.to_json(),
        cameras=cameras,
        validation=validation,
    )
    edges = [
        StaticTransform("stage", optical_frame(device_id), cameras[device_id].T_stage_from_optical, "shared_marker")
        for device_id in sorted(cameras)
    ]
    return RigFrameTree(rig=rig, transforms=TransformBuffer(edges))


def anchor_status_payload(
    *,
    request_id: UUID,
    state: str,
    accepted_sample_count: int,
    required_sample_count: int,
    rig_id: UUID | None = None,
    failure_code: str | None = None,
) -> dict:
    return {
        "type": "anchor_status",
        "protocol_version": 1,
        "request_id": str(request_id),
        "state": state,
        "accepted_sample_count": accepted_sample_count,
        "required_sample_count": required_sample_count,
        "rig_id": str(rig_id) if rig_id is not None else None,
        "failure_code": failure_code,
    }


def build_board_detector(spec: BoardSpec | None = None):
    spec = spec or BoardSpec()
    return spec, *build_board(spec)
