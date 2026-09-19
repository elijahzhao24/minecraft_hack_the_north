"""Tests for calibration loading, round-trip, and synthetic-rig geometry."""

from __future__ import annotations

import numpy as np
import pytest

from hmc_backend.calibration.model import (
    CalibrationError,
    load_rig_calibration,
    parse_rig_calibration,
    rig_to_json,
    save_rig_calibration,
)
from hmc_backend.calibration.synthetic import build_synthetic_rig, look_at_optical
from hmc_backend.reconstruction.geometry import apply_transform


def test_synthetic_rig_has_two_cameras_sharing_id():
    rig = build_synthetic_rig()
    assert set(rig.device_ids()) == {"front-phone", "side-phone"}
    ids = {c.calibration_id for c in rig.cameras.values()}
    assert ids == {rig.calibration_id}


def test_look_at_maps_optical_origin_to_eye():
    eye = np.array([1.0, 1.1, 2.0])
    t = look_at_optical(eye, np.array([0.0, 1.1, 0.0]), np.array([0.0, 1.0, 0.0]))
    origin = apply_transform(t, np.zeros((1, 3), np.float32))
    np.testing.assert_allclose(origin[0], eye, atol=1e-6)
    # Optical +Z (forward) should point from eye toward the target.
    fwd = apply_transform(t, np.array([[0.0, 0.0, 1.0]], np.float32))[0] - eye
    assert fwd[2] < 0  # target is at smaller Z than the eye


def test_rig_json_roundtrip():
    rig = build_synthetic_rig()
    parsed = parse_rig_calibration(rig_to_json(rig))
    assert parsed.calibration_id == rig.calibration_id
    front = parsed.camera("front-phone")
    np.testing.assert_allclose(front.K_rgb, rig.camera("front-phone").K_rgb)
    np.testing.assert_allclose(
        front.T_stage_from_optical, rig.camera("front-phone").T_stage_from_optical
    )


def test_save_and_load(tmp_path):
    rig = build_synthetic_rig()
    path = tmp_path / "calibration.json"
    save_rig_calibration(rig, path)
    loaded = load_rig_calibration(path)
    assert loaded.calibration_id == rig.calibration_id
    assert set(loaded.device_ids()) == {"front-phone", "side-phone"}


def test_missing_file_raises():
    with pytest.raises(CalibrationError):
        load_rig_calibration("/nonexistent/calibration.json")


def test_bad_schema_rejected():
    with pytest.raises(CalibrationError):
        parse_rig_calibration({"schema": "wrong", "schema_version": 1})


def test_bad_matrix_length_rejected():
    rig = rig_to_json(build_synthetic_rig())
    rig["cameras"][0]["K_rgb_row_major"] = [1, 2, 3]
    with pytest.raises(CalibrationError):
        parse_rig_calibration(rig)
