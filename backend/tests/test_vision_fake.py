"""Tests for the deterministic fake detector and fitter."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import numpy as np

from hmc_backend.contracts.internal import (
    CameraCalibration,
    CapturedFrame,
    CaptureGroup,
    ColoredPointCloud,
)
from hmc_backend.vision.fake import FakeCharacterFitter, FakePersonMaskDetector


def _frame() -> CapturedFrame:
    return CapturedFrame(
        device_id="front-phone",
        session_id=uuid4(),
        capture_id=uuid4(),
        sequence=1,
        capture_timestamp_s=0.0,
        normalized_capture_time_s=0.0,
        clock_uncertainty_ms=1.0,
        rgb=np.zeros((8, 8, 3), np.uint8),
        depth_m=np.ones((8, 8), np.float32),
        confidence=np.full((8, 8), 2, np.uint8),
        K_rgb=np.eye(3),
        arkit_pose=np.eye(4),
    )


def test_fake_detector_full_mask():
    det = FakePersonMaskDetector()
    view = det.detect_view(_frame())
    assert view.person_mask.shape == (8, 8)
    assert view.person_mask.all()


def test_fake_detector_center_box():
    det = FakePersonMaskDetector(center_box=True)
    view = det.detect_view(_frame())
    assert view.person_mask.any()
    assert not view.person_mask.all()


def test_fake_fitter_produces_all_collider_types():
    # A tall-ish synthetic cloud.
    rng = np.random.default_rng(0)
    xyz = rng.uniform([-0.3, 0.0, -0.2], [0.3, 1.8, 0.2], size=(500, 3)).astype(np.float32)
    rgba = np.zeros((500, 4), np.uint8)
    rgba[:, 3] = 255
    cloud = ColoredPointCloud(xyz, rgba, np.zeros(500, np.uint8))

    f = _frame()
    pair = CaptureGroup(uuid4(), (f,), 0.0, 0.0, uuid4())
    calib = CameraCalibration(
        uuid4(), "front-phone", (8, 8), (8, 8), np.eye(3), np.eye(4), 1.0, datetime.now(UTC)
    )

    fitted = FakeCharacterFitter().fit_character(pair, {}, cloud, calib)

    types = {c.type for c in fitted.colliders if c.valid}
    assert {"sphere", "capsule", "obb"} <= types
    # One collider is intentionally invalid (right hand).
    assert any(not c.valid for c in fitted.colliders)
    # Landmarks present and valid.
    assert all(lm.valid for lm in fitted.landmarks)
    # Head sphere sits near the top of the cloud.
    head = next(c for c in fitted.colliders if c.id == "head")
    assert head.center_stage_m[1] > 1.0


def test_fake_fitter_empty_cloud():
    f = _frame()
    pair = CaptureGroup(uuid4(), (f,), 0.0, 0.0, uuid4())
    calib = CameraCalibration(
        uuid4(), "front-phone", (8, 8), (8, 8), np.eye(3), np.eye(4), 1.0, datetime.now(UTC)
    )
    empty = ColoredPointCloud(
        np.zeros((0, 3), np.float32), np.zeros((0, 4), np.uint8), np.zeros((0,), np.uint8)
    )
    fitted = FakeCharacterFitter().fit_character(pair, {}, empty, calib)
    assert fitted.colliders == ()
    assert fitted.landmarks == ()
