"""Depth neighbourhoods reject background across discontinuities and never fake positions."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import numpy as np
import pytest

from hmc_backend.calibration.synthetic import intrinsics, look_at_optical
from hmc_backend.contracts.internal import CameraCalibration, CapturedFrame, Landmark2DObservation
from hmc_backend.vision import depth_sampling as ds


def _cfg(**kw) -> ds.DepthSamplingConfig:
    return ds.DepthSamplingConfig(**kw)


def test_rgb_depth_pixel_mapping_round_trips():
    rgb_wh, depth_wh = (1920, 1440), (256, 192)
    xy = (960.0, 720.0)
    d = ds.rgb_to_depth_px(xy, rgb_wh, depth_wh)
    assert d == pytest.approx((128.0, 96.0))
    assert ds.depth_to_rgb_px(d, rgb_wh, depth_wh) == pytest.approx(xy)


def test_neighbourhood_excludes_background_across_discontinuity():
    # Left half of the window is the person at 1.5 m, right half is a wall at 3.0 m.
    depth = np.full((20, 20), 3.0, np.float32)
    depth[:, :10] = 1.5
    conf = np.full((20, 20), 2, np.uint8)
    mask = np.ones((20, 20), np.bool_)
    # Landmark exactly on the silhouette edge (pixel 9/10 boundary).
    est = ds.robust_depth_at(depth, conf, mask, (9.4, 10.0), _cfg(radius_px=2))
    assert est is not None
    d, support, spread = est
    assert d == pytest.approx(1.5)
    assert spread == pytest.approx(0.0)
    # A window straddling the edge: 5 cols x 5 rows = 25 samples, 15 person / 10 wall
    # -> median is on the person; wall samples are 1.5 m away and rejected.
    assert support == 15


def test_neighbourhood_rejects_zero_nonfinite_and_low_confidence():
    depth = np.full((9, 9), 2.0, np.float32)
    depth[4, 4] = 0.0
    depth[4, 5] = np.nan
    depth[3, 4] = np.inf
    conf = np.full((9, 9), 2, np.uint8)
    conf[5, 4] = 0
    mask = np.ones((9, 9), np.bool_)
    est = ds.robust_depth_at(depth, conf, mask, (4, 4), _cfg(radius_px=1, min_support=3))
    assert est is not None
    assert est[0] == pytest.approx(2.0)
    assert est[1] == 9 - 4  # 3x3 window minus zero, nan, inf, low-confidence


def test_insufficient_support_returns_none_not_a_fake_position():
    depth = np.zeros((9, 9), np.float32)
    depth[4, 4] = 1.0
    conf = np.full((9, 9), 2, np.uint8)
    mask = np.ones((9, 9), np.bool_)
    assert ds.robust_depth_at(depth, conf, mask, (4, 4), _cfg(min_support=4)) is None
    # Outside the raster.
    assert ds.robust_depth_at(depth, conf, mask, (40, 4), _cfg()) is None
    # Everything masked out.
    assert ds.robust_depth_at(np.ones((9, 9), np.float32), conf, np.zeros((9, 9), np.bool_), (4, 4), _cfg()) is None


def test_window_grows_once_when_support_is_short():
    depth = np.full((11, 11), 1.2, np.float32)
    conf = np.zeros((11, 11), np.uint8)
    conf[2:9, 2:9] = 2  # only a 7x7 core is confident -> radius 2 around (2,2) is short
    mask = np.ones((11, 11), np.bool_)
    est = ds.robust_depth_at(depth, conf, mask, (2, 2), _cfg(radius_px=1, max_radius_px=3, min_support=6))
    assert est is not None and est[1] >= 6


def _frame_and_calib(depth: np.ndarray, *, rgb_scale: int = 2):
    h_d, w_d = depth.shape
    w_r, h_r = w_d * rgb_scale, h_d * rgb_scale
    k_rgb = intrinsics(w_r, h_r)
    t = look_at_optical(np.array([0.0, 1.0, 2.0]), np.array([0.0, 1.0, 0.0]), np.array([0.0, 1.0, 0.0]))
    calib = CameraCalibration(uuid4(), "front-phone", (w_r, h_r), (w_d, h_d), k_rgb, t, 1.0, datetime.now(UTC))
    frame = CapturedFrame(
        "front-phone", uuid4(), uuid4(), 1, 0.0, 0.0, 1.0,
        np.zeros((h_r, w_r, 3), np.uint8), depth.astype(np.float32),
        np.full((h_d, w_d), 2, np.uint8), k_rgb, np.eye(4),
    )
    return frame, calib


def test_observe_landmarks_unprojects_through_calibration():
    depth = np.full((24, 32), 2.0, np.float32)  # a plane 2 m in front of the camera
    frame, calib = _frame_and_calib(depth)
    # Principal point in RGB coordinates -> straight down the optical axis -> stage (0, 1, 0).
    cx, cy = calib.K_rgb[0, 2], calib.K_rgb[1, 2]
    lms = (
        Landmark2DObservation("nose", (cx, cy), None, 0.9, 0.9, True),
        Landmark2DObservation("left_ear", (cx, cy), None, 0.9, 0.9, False),  # invalid -> skipped
    )
    mask = np.ones((24 * 2, 32 * 2), np.bool_)
    obs = ds.observe_landmarks(frame, calib, mask, lms, _cfg(), name_prefix="body.")
    assert set(obs) == {"body.nose"}
    o = obs["body.nose"]
    assert o.position_stage_m == pytest.approx((0.0, 1.0, 0.0), abs=0.08)
    assert o.depth_m == pytest.approx(2.0)
    assert o.support >= 4
    assert 0.0 <= o.quality <= 1.0
    assert o.device_id == "front-phone"


def test_observe_landmarks_never_emits_origin_for_missing_depth():
    depth = np.zeros((24, 32), np.float32)
    frame, calib = _frame_and_calib(depth)
    lms = (Landmark2DObservation("nose", (32.0, 24.0), None, 0.9, 0.9, True),)
    obs = ds.observe_landmarks(frame, calib, np.ones((48, 64), np.bool_), lms, _cfg(), name_prefix="body.")
    assert obs == {}
