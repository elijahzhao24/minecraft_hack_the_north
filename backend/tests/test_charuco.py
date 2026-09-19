"""Tests for ChArUco detection, pose estimation, and stage composition.

The core test places a camera at a **known stage position**, renders the board
as that camera would see it, then runs the full detect -> solvePnP -> compose
chain and checks the recovered ``T_stage_from_optical`` puts the camera back
where it started. That exercises ``STAGE_FROM_BOARD_ROTATION`` itself, which a
determinant check alone cannot validate.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from hmc_backend.calibration.charuco import (
    STAGE_FROM_BOARD_ROTATION,
    BoardObservation,
    BoardSpec,
    CharucoError,
    average_rotations_so3,
    build_board,
    compose_stage_from_optical,
    corner_coverage,
    detect_board,
    estimate_pose,
    geodesic_angle_rad,
    observe_frame,
    solve_camera,
    stage_from_board,
    validate_solution,
)

# A larger board than the default so it resolves well at demo-like distances.
SPEC = BoardSpec(square_length_m=0.08, marker_length_m=0.06)
IMG_W, IMG_H = 1600, 1200


def _intrinsics(w: int, h: int, fov_x_deg: float = 60.0) -> np.ndarray:
    fx = (w / 2.0) / np.tan(np.radians(fov_x_deg) / 2.0)
    return np.array([[fx, 0, w / 2.0], [0, fx, h / 2.0], [0, 0, 1.0]], dtype=np.float64)


def _stage_look_at(eye, target, up=(0.0, 1.0, 0.0)) -> np.ndarray:
    """``T_stage_from_optical`` for a camera at ``eye`` looking at ``target``."""
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


def _render_board_view(board, detector, k, r_cam_from_board, t_cam_from_board):
    """Render the board as the camera at ``(R, t)`` would see it.

    The image-pixel <-> board-metric correspondence is *derived* by detecting
    the board in its own generated image rather than assuming which corner is
    the origin, so the rendering cannot silently disagree with the pose.
    """
    px_per_square = 80
    board_img = board.generateImage(
        (SPEC.squares_x * px_per_square, SPEC.squares_y * px_per_square)
    )

    found = detect_board(board_img, detector)
    assert found is not None, "could not detect the board in its own rendering"
    obj_pts, img_pts = board.matchImagePoints(*found)
    obj_pts = obj_pts.reshape(-1, 3).astype(np.float64)
    img_pts = img_pts.reshape(-1, 2).astype(np.float32)

    # Four well-spread correspondences, ordered around the quad.
    s = obj_pts[:, 0] + obj_pts[:, 1]
    d = obj_pts[:, 0] - obj_pts[:, 1]
    picks = [int(np.argmin(s)), int(np.argmin(d)), int(np.argmax(s)), int(np.argmax(d))]
    assert len(set(picks)) == 4, "degenerate correspondence picks"

    rvec, _ = cv2.Rodrigues(r_cam_from_board)
    dst, _ = cv2.projectPoints(obj_pts[picks], rvec, t_cam_from_board, k, np.zeros(5))

    m = cv2.getPerspectiveTransform(img_pts[picks], dst.reshape(-1, 2).astype(np.float32))
    canvas = np.full((IMG_H, IMG_W), 255, np.uint8)
    cv2.warpPerspective(
        board_img, m, (IMG_W, IMG_H), dst=canvas, borderMode=cv2.BORDER_TRANSPARENT
    )
    return canvas


def _scene(eye_stage=(0.10, 1.20, 1.50), board_origin=(0.0, 0.0, 0.0)):
    """Render the board from a camera at a known *stage* pose.

    Returns the pieces plus the ground-truth ``T_stage_from_optical``.
    """
    board, detector = build_board(SPEC)
    k = _intrinsics(IMG_W, IMG_H)

    # Board lies flat on the floor: +X along stage +X, +Y along stage +Z.
    t_stage_from_board = stage_from_board(board_origin)
    centre_board = np.array([SPEC.width_m / 2, SPEC.height_m / 2, 0.0, 1.0])
    centre_stage = (t_stage_from_board @ centre_board)[:3]

    t_stage_from_optical_gt = _stage_look_at(eye_stage, centre_stage)
    t_cam_from_board = _invert_rigid(t_stage_from_optical_gt) @ t_stage_from_board

    image = _render_board_view(
        board, detector, k, t_cam_from_board[:3, :3], t_cam_from_board[:3, 3]
    )
    return board, detector, k, image, t_stage_from_optical_gt, t_stage_from_board


# --- board-to-stage orientation -------------------------------------------

def test_stage_from_board_is_right_handed():
    det = np.linalg.det(STAGE_FROM_BOARD_ROTATION)
    assert det == pytest.approx(1.0), "a det=-1 matrix would mirror the subject"
    assert np.allclose(
        STAGE_FROM_BOARD_ROTATION @ STAGE_FROM_BOARD_ROTATION.T, np.eye(3)
    )


def test_stage_from_board_axis_mapping():
    r = STAGE_FROM_BOARD_ROTATION
    np.testing.assert_allclose(r @ np.array([1.0, 0, 0]), [1, 0, 0], atol=1e-12)
    # printed +Y (down the page) points toward the front camera -> stage +Z
    np.testing.assert_allclose(r @ np.array([0.0, 1, 0]), [0, 0, 1], atol=1e-12)
    # board +Z points INTO the printed face -> stage -Y (down into the floor)
    np.testing.assert_allclose(r @ np.array([0.0, 0, 1]), [0, -1, 0], atol=1e-12)


def test_reflection_trap_is_detectable_by_determinant():
    naive = np.array([[1.0, 0, 0], [0, 0, 1], [0, 1, 0]])
    assert np.linalg.det(naive) == pytest.approx(-1.0)


def test_flipped_z_trap_passes_determinant_check():
    """The 180-degree flip trap has det=+1, so only a real board catches it."""
    flipped = np.array([[1.0, 0, 0], [0, 0, 1], [0, -1, 0]])
    assert np.linalg.det(flipped) == pytest.approx(1.0)
    assert not np.allclose(flipped, STAGE_FROM_BOARD_ROTATION)


def test_stage_from_board_translation():
    t = stage_from_board((0.1, 0.0, -0.2))
    np.testing.assert_allclose(t[:3, 3], [0.1, 0.0, -0.2])
    assert t[3, 3] == 1.0


# --- detection and the full stage round trip -------------------------------

def test_detects_board_in_rendered_view():
    _b, detector, _k, image, _gt, _sfb = _scene()
    found = detect_board(image, detector)
    assert found is not None
    corners, ids = found
    assert len(ids) >= 20
    assert corner_coverage(corners, image.shape) > 0.05


def test_recovers_known_camera_stage_pose():
    board, detector, k, image, gt, t_sfb = _scene()
    corners, ids = detect_board(image, detector)
    solved = estimate_pose(corners, ids, board, k)
    assert solved is not None
    r, t, reproj = solved

    recovered = compose_stage_from_optical(r, t, t_sfb)

    # Camera lands within a few mm of where it was placed, with sub-degree
    # orientation error.
    assert np.linalg.norm(recovered[:3, 3] - gt[:3, 3]) < 0.01
    assert np.degrees(geodesic_angle_rad(gt[:3, :3], recovered[:3, :3])) < 1.0
    assert reproj < 2.0


def test_recovers_pose_from_a_second_viewpoint():
    board, detector, k, image, gt, t_sfb = _scene(eye_stage=(0.70, 0.95, 1.10))
    corners, ids = detect_board(image, detector)
    r, t, _ = estimate_pose(corners, ids, board, k)
    recovered = compose_stage_from_optical(r, t, t_sfb)
    assert np.linalg.norm(recovered[:3, 3] - gt[:3, 3]) < 0.01
    assert np.degrees(geodesic_angle_rad(gt[:3, :3], recovered[:3, :3])) < 1.0


def test_board_origin_offset_shifts_recovered_pose():
    """Moving the board's origin in stage space moves the solved camera with it."""
    offset = (0.30, 0.0, -0.20)
    board, detector, k, image, gt, t_sfb = _scene(board_origin=offset)
    corners, ids = detect_board(image, detector)
    r, t, _ = estimate_pose(corners, ids, board, k)
    recovered = compose_stage_from_optical(r, t, t_sfb)
    assert np.linalg.norm(recovered[:3, 3] - gt[:3, 3]) < 0.01


def test_camera_is_above_the_floor():
    """Sanity check the sign of the vertical axis: a camera above the board
    must solve to a positive stage Y."""
    board, detector, k, image, _gt, t_sfb = _scene()
    corners, ids = detect_board(image, detector)
    r, t, _ = estimate_pose(corners, ids, board, k)
    recovered = compose_stage_from_optical(r, t, t_sfb)
    assert recovered[1, 3] > 0.5, "camera solved to below the floor - axis flip"


def test_detect_returns_none_on_blank_image():
    _b, detector, _k, _img, _gt, _sfb = _scene()
    blank = np.full((IMG_H, IMG_W), 255, np.uint8)
    assert detect_board(blank, detector) is None


def test_observe_frame_accepts_and_reports_reason():
    board, detector, k, image, _gt, _sfb = _scene()
    obs, reason = observe_frame(
        image, k, board, detector, device_id="front-phone", sequence=1
    )
    assert reason == "accepted"
    assert obs is not None
    assert obs.device_id == "front-phone"
    assert obs.corner_count >= 20


def test_observe_frame_rejects_blank():
    board, detector = build_board(SPEC)
    k = _intrinsics(IMG_W, IMG_H)
    blank = np.full((IMG_H, IMG_W), 255, np.uint8)
    obs, reason = observe_frame(blank, k, board, detector, device_id="d", sequence=0)
    assert obs is None
    assert reason == "no_board_detected"


def test_observe_frame_rejects_high_reprojection():
    board, detector, k, image, _gt, _sfb = _scene()
    obs, reason = observe_frame(
        image, k, board, detector, device_id="d", sequence=0, max_reprojection_px=0.0001
    )
    assert obs is None
    assert reason == "reprojection_too_high"


def test_observe_frame_rejects_too_few_corners():
    board, detector, k, image, _gt, _sfb = _scene()
    obs, reason = observe_frame(
        image, k, board, detector, device_id="d", sequence=0, min_corners=10_000
    )
    assert obs is None
    assert reason == "too_few_corners"


# --- rotation aggregation --------------------------------------------------

def test_average_rotations_returns_valid_rotation():
    rng = np.random.default_rng(0)
    base, _ = cv2.Rodrigues(np.array([0.3, -0.2, 0.1]))
    rotations = []
    for _ in range(10):
        noise, _ = cv2.Rodrigues(rng.normal(scale=0.01, size=3))
        rotations.append(base @ noise)
    mean = average_rotations_so3(rotations)
    assert np.linalg.det(mean) == pytest.approx(1.0, abs=1e-9)
    assert np.allclose(mean @ mean.T, np.eye(3), atol=1e-9)
    assert np.degrees(geodesic_angle_rad(base, mean)) < 1.0


def test_average_rotations_is_not_elementwise():
    """The element-wise mean of two distinct rotations is not a rotation."""
    a = np.eye(3)
    b, _ = cv2.Rodrigues(np.array([0.0, np.pi / 3, 0.0]))
    naive = (a + b) / 2.0
    assert not np.isclose(np.linalg.det(naive), 1.0)
    proper = average_rotations_so3([a, b])
    assert np.linalg.det(proper) == pytest.approx(1.0, abs=1e-9)


def test_average_rotations_rejects_empty():
    with pytest.raises(CharucoError):
        average_rotations_so3([])


def test_geodesic_angle_known_value():
    r, _ = cv2.Rodrigues(np.array([0.0, np.pi / 4, 0.0]))
    assert np.degrees(geodesic_angle_rad(np.eye(3), r)) == pytest.approx(45.0, abs=1e-6)


# --- robust solve ----------------------------------------------------------

def _obs(device_id, seq, r, t, reproj=1.0):
    return BoardObservation(device_id, seq, 30, 0.2, r, t, reproj)


def _base_pose():
    t_sfb = stage_from_board()
    gt = _stage_look_at((0.10, 1.20, 1.50), (SPEC.width_m / 2, 0.0, SPEC.height_m / 2))
    cfb = _invert_rigid(gt) @ t_sfb
    return cfb[:3, :3], cfb[:3, 3], gt, t_sfb


def test_solve_camera_aggregates_and_rejects_outlier():
    rng = np.random.default_rng(1)
    base_r, base_t, gt, t_sfb = _base_pose()

    observations = []
    for i in range(8):
        noise, _ = cv2.Rodrigues(rng.normal(scale=0.003, size=3))
        observations.append(
            _obs("front-phone", i, base_r @ noise, base_t + rng.normal(scale=0.002, size=3))
        )

    # One badly wrong view (board moved / misdetection): 40 degrees off.
    bad, _ = cv2.Rodrigues(np.array([0.0, np.radians(40.0), 0.0]))
    observations.append(_obs("front-phone", 99, base_r @ bad, base_t, reproj=2.5))

    solution = solve_camera(
        observations, device_id="front-phone", t_stage_from_board=t_sfb, max_angle_deg=5.0
    )

    assert solution.accepted_count == 8
    assert solution.rejection_reasons.get("rotation_outlier") == 1
    assert np.linalg.norm(solution.camera_position_stage_m - gt[:3, 3]) < 0.02


def test_solve_camera_requires_observations():
    with pytest.raises(CharucoError):
        solve_camera([], device_id="front-phone")


def test_validate_solution_reports_held_out_error():
    base_r, base_t, _gt, t_sfb = _base_pose()
    train = [_obs("d", i, base_r, base_t) for i in range(5)]
    solution = solve_camera(train, device_id="d", t_stage_from_board=t_sfb)
    report = validate_solution(
        solution, [_obs("d", 9, base_r, base_t)], t_stage_from_board=t_sfb
    )
    assert report["held_out_frames"] == 1
    assert report["median_position_error_m"] < 1e-6


def test_validate_solution_empty_held_out():
    base_r, base_t, _gt, t_sfb = _base_pose()
    solution = solve_camera([_obs("d", 0, base_r, base_t)], device_id="d", t_stage_from_board=t_sfb)
    assert validate_solution(solution, [])["held_out_frames"] == 0


# --- board spec ------------------------------------------------------------

def test_board_spec_roundtrip():
    assert BoardSpec.from_json(SPEC.to_json()) == SPEC


def test_board_spec_rejects_bad_marker_length():
    with pytest.raises(CharucoError):
        BoardSpec(square_length_m=0.03, marker_length_m=0.04)


def test_build_board_rejects_unknown_dictionary():
    with pytest.raises(CharucoError):
        build_board(BoardSpec(dictionary="DICT_NOT_REAL"))
