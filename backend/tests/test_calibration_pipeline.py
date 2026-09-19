"""End-to-end test of the offline calibration pipeline.

Renders a ChArUco board as two cameras at *known stage poses* would see it,
encodes those views as real HMC1 RGBD packets, records them the way the capture
path does, then runs the offline collect + solve and checks both cameras are
recovered where they were placed.

This is the rehearsal the plan calls for: validate the solver on recorded
synthetic board frames before the tripods are set up.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from calibrate import collect, split_held_out

from hmc_backend.calibration.charuco import (
    BoardSpec,
    build_board,
    detect_board,
    solve_camera,
    stage_from_board,
    validate_solution,
)
from hmc_backend.capture.recording import save_recording
from hmc_backend.contracts.internal import CameraCalibration
from hmc_backend.fixtures.scene import encode_rgbd_packet

SPEC = BoardSpec(square_length_m=0.08, marker_length_m=0.06)
IMG_W, IMG_H = 1280, 960

# Ground-truth camera placements in stage meters.
EYES = {
    "front-phone": (0.20, 1.15, 1.45),
    "side-phone": (1.25, 1.05, 0.95),
}


def _intrinsics() -> np.ndarray:
    fx = (IMG_W / 2.0) / np.tan(np.radians(60.0) / 2.0)
    return np.array([[fx, 0, IMG_W / 2.0], [0, fx, IMG_H / 2.0], [0, 0, 1.0]], np.float64)


def _stage_look_at(eye, target, up=(0.0, 1.0, 0.0)) -> np.ndarray:
    eye = np.asarray(eye, float)
    z = np.asarray(target, float) - eye
    z /= np.linalg.norm(z)
    x = np.cross(np.asarray(up, float), z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    t = np.eye(4)
    t[:3, :3] = np.column_stack([x, y, z])
    t[:3, 3] = eye
    return t


def _invert_rigid(t: np.ndarray) -> np.ndarray:
    out = np.eye(4)
    out[:3, :3] = t[:3, :3].T
    out[:3, 3] = -t[:3, :3].T @ t[:3, 3]
    return out


def _render(board, detector, k, r_cam_from_board, t_cam_from_board) -> np.ndarray:
    px = 80
    board_img = board.generateImage((SPEC.squares_x * px, SPEC.squares_y * px))
    found = detect_board(board_img, detector)
    assert found is not None
    obj_pts, img_pts = board.matchImagePoints(*found)
    obj_pts = obj_pts.reshape(-1, 3).astype(np.float64)
    img_pts = img_pts.reshape(-1, 2).astype(np.float32)

    s = obj_pts[:, 0] + obj_pts[:, 1]
    d = obj_pts[:, 0] - obj_pts[:, 1]
    picks = [int(np.argmin(s)), int(np.argmin(d)), int(np.argmax(s)), int(np.argmax(d))]

    rvec, _ = cv2.Rodrigues(r_cam_from_board)
    dst, _ = cv2.projectPoints(obj_pts[picks], rvec, t_cam_from_board, k, np.zeros(5))
    m = cv2.getPerspectiveTransform(img_pts[picks], dst.reshape(-1, 2).astype(np.float32))

    canvas = np.full((IMG_H, IMG_W), 255, np.uint8)
    cv2.warpPerspective(
        board_img, m, (IMG_W, IMG_H), dst=canvas, borderMode=cv2.BORDER_TRANSPARENT
    )
    return cv2.cvtColor(canvas, cv2.COLOR_GRAY2RGB)


def _calib_for(device_id: str, k: np.ndarray) -> CameraCalibration:
    return CameraCalibration(
        calibration_id=uuid4(),
        device_id=device_id,
        rgb_size=(IMG_W, IMG_H),
        depth_size=(IMG_W, IMG_H),
        K_rgb=k,
        T_stage_from_optical=np.eye(4),
        reprojection_error_px=1.0,
        created_at_utc=datetime.now(UTC),
    )


def _write_board_recordings(root: Path, frames: int = 5, blank_frames: int = 0) -> dict:
    """Render and record board captures; returns ground-truth stage transforms."""
    board, detector = build_board(SPEC)
    k = _intrinsics()
    t_sfb = stage_from_board()

    centre_board = np.array([SPEC.width_m / 2, SPEC.height_m / 2, 0.0, 1.0])
    centre_stage = (t_sfb @ centre_board)[:3]

    truth = {}
    images = {}
    for device_id, eye in EYES.items():
        gt = _stage_look_at(eye, centre_stage)
        truth[device_id] = gt
        cfb = _invert_rigid(gt) @ t_sfb
        images[device_id] = _render(board, detector, k, cfb[:3, :3], cfb[:3, 3])

    for i in range(frames + blank_frames):
        capture_id = uuid4()
        blank = i >= frames
        packets = {}
        for n, device_id in enumerate(EYES):
            rgb = (
                np.full((IMG_H, IMG_W, 3), 255, np.uint8) if blank else images[device_id]
            )
            packets[device_id] = encode_rgbd_packet(
                device_id=device_id,
                session_id=uuid4(),
                capture_id=capture_id,
                sequence=i * 10 + n,
                capture_timestamp_s=1000.0 + i,
                calib=_calib_for(device_id, k),
                rgb=rgb,
                depth=np.ones((IMG_H, IMG_W), np.float32),
                confidence=np.full((IMG_H, IMG_W), 2, np.uint8),
            )
        save_recording(root, capture_id, packets)

    return truth


def test_collect_and_solve_recovers_both_cameras(tmp_path):
    truth = _write_board_recordings(tmp_path, frames=5)
    capture_dirs = sorted(d for d in tmp_path.iterdir() if (d / "manifest.json").exists())
    assert len(capture_dirs) == 5

    devices = collect(capture_dirs, SPEC)
    assert set(devices) == set(EYES)

    t_sfb = stage_from_board()
    for device_id, state in devices.items():
        assert len(state.observations) == 5, state.rejections
        assert state.rgb_size == (IMG_W, IMG_H)

        solution = solve_camera(
            state.observations, device_id=device_id, t_stage_from_board=t_sfb
        )
        recovered = solution.camera_position_stage_m
        expected = truth[device_id][:3, 3]

        # JPEG compression is the only error source here; stay well under 2 cm.
        assert np.linalg.norm(recovered - expected) < 0.02, (
            f"{device_id}: got {recovered}, expected {expected}"
        )
        # Cameras must solve above the floor.
        assert recovered[1] > 0.5
        assert solution.median_reprojection_error_px < 3.0


def test_baseline_between_cameras_matches_truth(tmp_path):
    truth = _write_board_recordings(tmp_path, frames=4)
    capture_dirs = sorted(d for d in tmp_path.iterdir() if (d / "manifest.json").exists())
    devices = collect(capture_dirs, SPEC)
    t_sfb = stage_from_board()

    solved = {
        d: solve_camera(s.observations, device_id=d, t_stage_from_board=t_sfb).camera_position_stage_m
        for d, s in devices.items()
    }
    ids = sorted(solved)
    got = np.linalg.norm(solved[ids[0]] - solved[ids[1]])
    want = np.linalg.norm(truth[ids[0]][:3, 3] - truth[ids[1]][:3, 3])
    assert abs(got - want) < 0.03


def test_blank_frames_are_rejected_not_fatal(tmp_path):
    _write_board_recordings(tmp_path, frames=3, blank_frames=2)
    capture_dirs = sorted(d for d in tmp_path.iterdir() if (d / "manifest.json").exists())
    devices = collect(capture_dirs, SPEC)

    for state in devices.values():
        assert len(state.observations) == 3
        assert state.rejections.get("no_board_detected") == 2


def test_held_out_split_is_deterministic_and_disjoint(tmp_path):
    truth = _write_board_recordings(tmp_path, frames=8)
    capture_dirs = sorted(d for d in tmp_path.iterdir() if (d / "manifest.json").exists())
    devices = collect(capture_dirs, SPEC)
    state = devices["front-phone"]

    train, held = split_held_out(state.observations, 0.25)
    assert held, "expected a held-out set"
    assert len(train) + len(held) == len(state.observations)
    assert not ({o.sequence for o in train} & {o.sequence for o in held})
    # Deterministic.
    assert split_held_out(state.observations, 0.25)[1] == held

    t_sfb = stage_from_board()
    solution = solve_camera(train, device_id="front-phone", t_stage_from_board=t_sfb)
    report = validate_solution(solution, held, t_stage_from_board=t_sfb)
    assert report["held_out_frames"] == len(held)
    assert report["median_position_error_m"] < 0.02
    assert np.linalg.norm(
        solution.camera_position_stage_m - truth["front-phone"][:3, 3]
    ) < 0.02


def test_too_few_frames_reports_no_held_out():
    from hmc_backend.calibration.charuco import BoardObservation

    obs = [BoardObservation("d", i, 30, 0.2, np.eye(3), np.array([0.0, 0, 1.0]), 1.0) for i in range(2)]
    train, held = split_held_out(obs, 0.25)
    assert held == []
    assert len(train) == 2


@pytest.mark.parametrize("fraction", [0.0, -1.0])
def test_held_out_disabled(fraction):
    from hmc_backend.calibration.charuco import BoardObservation

    obs = [BoardObservation("d", i, 30, 0.2, np.eye(3), np.array([0.0, 0, 1.0]), 1.0) for i in range(8)]
    train, held = split_held_out(obs, fraction)
    assert held == []
    assert len(train) == 8
