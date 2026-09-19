"""Tests for geometry helpers and per-view reconstruction."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import numpy as np
import pytest

from hmc_backend.contracts.internal import (
    CameraCalibration,
    CapturedFrame,
    ViewDetection,
)
from hmc_backend.reconstruction.geometry import (
    apply_transform,
    deterministic_subsample,
    scale_intrinsics,
    unproject,
    voxel_downsample,
)
from hmc_backend.reconstruction.reconstruct import CropBounds, merge_clouds, reconstruct_view


def test_scale_intrinsics():
    k = np.array([[1000.0, 0, 640], [0, 1000, 360], [0, 0, 1]])
    k_d = scale_intrinsics(k, (1280, 720), (256, 144))
    assert k_d[0, 0] == pytest.approx(200.0)  # fx * 256/1280
    assert k_d[1, 1] == pytest.approx(200.0)  # fy * 144/720
    assert k_d[0, 2] == pytest.approx(128.0)  # cx * 256/1280
    assert k_d[1, 2] == pytest.approx(72.0)


def test_unproject_center_pixel():
    k = np.array([[500.0, 0, 128], [0, 500, 96], [0, 0, 1]])
    pt = unproject(np.array([128.0]), np.array([96.0]), np.array([2.0]), k)
    # Principal point at 2 m -> straight ahead in optical frame.
    np.testing.assert_allclose(pt[0], [0.0, 0.0, 2.0], atol=1e-6)


def test_apply_transform_identity_and_translation():
    pts = np.array([[1.0, 2.0, 3.0]])
    t = np.eye(4)
    t[:3, 3] = [10, 20, 30]
    out = apply_transform(t, pts)
    np.testing.assert_allclose(out[0], [11, 22, 33], atol=1e-6)


def test_voxel_downsample_collapses_and_ors_source():
    xyz = np.array([[0.0, 0, 0], [0.001, 0, 0], [1.0, 0, 0]], np.float32)
    rgba = np.array([[1, 1, 1, 255], [2, 2, 2, 255], [3, 3, 3, 255]], np.uint8)
    source = np.array([0b01, 0b10, 0b01], np.uint8)
    dx, _drgba, dsrc = voxel_downsample(xyz, rgba, source, 0.01)
    assert dx.shape[0] == 2  # first two collapse into one voxel
    # The surviving voxel's source bitset OR-combines both contributing cameras.
    assert int(dsrc[0]) == 0b11


def test_deterministic_subsample_is_reproducible():
    xyz = np.random.default_rng(0).random((100, 3)).astype(np.float32)
    rgba = np.zeros((100, 4), np.uint8)
    rgba[:, 3] = 255
    src = np.zeros(100, np.uint8)
    a = deterministic_subsample(xyz, rgba, src, 10, seed=42)[0]
    b = deterministic_subsample(xyz, rgba, src, 10, seed=42)[0]
    np.testing.assert_array_equal(a, b)
    assert a.shape[0] == 10


def _synthetic_view():
    """Build a view whose depth image is a flat plane 2 m in front of the camera.

    Calibration places the optical frame at the stage origin with optical +Z ->
    stage +Z, so recovered points should sit on the z=2 plane.
    """
    w_d, h_d = 16, 12
    fx = fy = 100.0
    cx, cy = w_d / 2.0, h_d / 2.0
    k_rgb = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], np.float64)

    depth = np.full((h_d, w_d), 2.0, np.float32)
    conf = np.full((h_d, w_d), 2, np.uint8)
    rgb = np.zeros((h_d, w_d, 3), np.uint8)
    rgb[:, :] = (120, 40, 200)

    # Optical -> stage: identity axes (optical +Z already toward +Z here).
    t = np.eye(4)

    frame = CapturedFrame(
        device_id="front-phone",
        session_id=uuid4(),
        capture_id=uuid4(),
        sequence=1,
        capture_timestamp_s=0.0,
        normalized_capture_time_s=0.0,
        clock_uncertainty_ms=1.0,
        rgb=rgb,
        depth_m=depth,
        confidence=conf,
        K_rgb=k_rgb,
        arkit_pose=np.eye(4),
    )
    calib = CameraCalibration(
        calibration_id=uuid4(),
        device_id="front-phone",
        rgb_size=(w_d, h_d),  # rgb same size as depth for the test
        depth_size=(w_d, h_d),
        K_rgb=k_rgb,
        T_stage_from_optical=t,
        reprojection_error_px=1.0,
        created_at_utc=datetime.now(UTC),
    )
    mask = np.ones((h_d, w_d), np.bool_)
    detection = ViewDetection(
        device_id="front-phone",
        capture_id=frame.capture_id,
        person_mask=mask,
        body=(),
        left_hand=(),
        right_hand=(),
        pose_world_prior_m=None,
    )
    return frame, detection, calib


def test_reconstruct_view_recovers_plane_and_color():
    frame, detection, calib = _synthetic_view()
    crop = CropBounds(-5, 5, -5, 5, -5, 5, 0.1, 10.0)
    cloud = reconstruct_view(frame, detection, calib, crop, confidence_min=1, source_bit=0b01)

    assert cloud.count == 16 * 12
    # All points lie on the z = 2 plane.
    np.testing.assert_allclose(cloud.xyz_stage_m[:, 2], 2.0, atol=1e-5)
    # Color preserved, alpha 255.
    assert (cloud.rgba[:, 3] == 255).all()
    assert tuple(cloud.rgba[0, :3]) == (120, 40, 200)


def test_reconstruct_view_excludes_zero_depth():
    frame, detection, calib = _synthetic_view()
    frame.depth_m[0:2, :] = 0.0  # invalid band
    crop = CropBounds(-5, 5, -5, 5, -5, 5, 0.1, 10.0)
    cloud = reconstruct_view(frame, detection, calib, crop, confidence_min=1, source_bit=0b01)
    assert cloud.count == 16 * (12 - 2)


def test_merge_two_views():
    frame, detection, calib = _synthetic_view()
    crop = CropBounds(-5, 5, -5, 5, -5, 5, 0.1, 10.0)
    c1 = reconstruct_view(frame, detection, calib, crop, confidence_min=1, source_bit=0b01)
    c2 = reconstruct_view(frame, detection, calib, crop, confidence_min=1, source_bit=0b10)
    merged = merge_clouds([c1, c2], voxel_size_m=0.01, max_points=100_000, seed=7)
    # Same plane from both cameras collapses; source bits OR to 0b11.
    assert merged.count > 0
    assert int(merged.source_mask.max()) == 0b11
