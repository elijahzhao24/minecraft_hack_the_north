"""Crop mapping is reversible; left/right association survives asymmetric poses."""

from __future__ import annotations

import numpy as np
import pytest

from hmc_backend.contracts.internal import Landmark2DObservation
from hmc_backend.vision import hand_association as ha

CFG = ha.AssociationConfig()


def _hand(center, spread=12.0, label=None, score=0.9, seed=0) -> ha.HandCandidate:
    rng = np.random.default_rng(seed)
    pts = [center] + [(center[0] + rng.uniform(-spread, spread), center[1] + rng.uniform(-spread, spread)) for _ in range(20)]
    lms = tuple(Landmark2DObservation(f"h{i}", (float(x), float(y)), None, None, 0.9, True) for i, (x, y) in enumerate(pts))
    return ha.HandCandidate(lms, label, score)


# --- crops -------------------------------------------------------------------


def test_crop_mapping_is_reversible_within_pixel_tolerance():
    crop = ha.wrist_crop((500.0, 300.0), (420.0, 240.0), (1920, 1440))
    assert crop.size >= 96
    for xy in [(500.0, 300.0), (crop.x0 + 3.5, crop.y0 + 7.25), (crop.x0 + crop.size, crop.y0)]:
        uv = crop.to_norm(xy)
        back = crop.to_full(uv)
        assert back == pytest.approx(xy, abs=1e-9)
    # Normalized corners map to the crop box.
    assert crop.to_full((0.0, 0.0)) == (crop.x0, crop.y0)
    assert crop.to_full((1.0, 1.0)) == (crop.x0 + crop.size, crop.y0 + crop.size)
    assert np.allclose(crop.affine_norm_from_full @ crop.affine_full_from_norm, np.eye(3))


def test_crop_is_biased_toward_fingers_and_padded_at_borders():
    crop = ha.wrist_crop((100.0, 100.0), (200.0, 100.0), (640, 480))  # arm points -X
    centre_x = crop.x0 + crop.size / 2.0
    assert centre_x < 100.0  # shifted along elbow->wrist
    img = np.full((480, 640, 3), 7, np.uint8)
    patch = crop.extract(img)
    assert patch.shape == (crop.size, crop.size, 3)
    assert crop.x0 < 0 and (patch[:, 0] == 0).all()  # zero padding outside the image
    assert (patch[:, -1] == 7).all()


def test_crop_without_elbow_is_centered_min_size():
    crop = ha.wrist_crop((300.0, 200.0), None, (640, 480), min_size_px=128)
    assert crop.size == 128
    assert (crop.x0 + 64, crop.y0 + 64) == (300, 200)


# --- handedness --------------------------------------------------------------


def test_model_handedness_is_swapped_for_unmirrored_input():
    assert ha.anatomical_side_from_model_handedness("Left", image_is_mirrored=False) == "right"
    assert ha.anatomical_side_from_model_handedness("Right", image_is_mirrored=False) == "left"
    assert ha.anatomical_side_from_model_handedness("Left", image_is_mirrored=True) == "left"


# --- association -------------------------------------------------------------


def _arms_front_view():
    """Front camera: subject's anatomical left appears on image right."""
    return {
        "left": ha.BodyArm("left", wrist_xy=(420.0, 300.0), elbow_xy=(380.0, 220.0)),
        "right": ha.BodyArm("right", wrist_xy=(220.0, 300.0), elbow_xy=(260.0, 220.0)),
    }


def test_association_symmetric_pose_front_view():
    arms = _arms_front_view()
    # Model labels assume a mirrored image, so the anatomical-left hand is labelled "Right".
    cands = [_hand((425.0, 330.0), label="Right"), _hand((215.0, 330.0), label="Left")]
    out = ha.associate_hands(cands, arms, CFG)
    assert out["left"].candidate_index == 0
    assert out["right"].candidate_index == 1
    assert all(a.margin >= CFG.min_margin for a in out.values())


def test_association_right_arm_raised_asymmetric():
    # Right arm raised above the head; left arm hangs. Only one hand detected (the raised one).
    arms = {
        "left": ha.BodyArm("left", wrist_xy=(400.0, 420.0), elbow_xy=(390.0, 330.0)),
        "right": ha.BodyArm("right", wrist_xy=(250.0, 60.0), elbow_xy=(270.0, 150.0)),
    }
    raised = _hand((248.0, 30.0), label="Left")  # above the wrist along elbow->wrist
    out = ha.associate_hands([raised], arms, CFG)
    assert set(out) == {"right"}
    assert out["right"].candidate_index == 0


def test_association_back_view_relies_on_geometry_not_label_alone():
    # Back camera: anatomical left appears on image left; the model's label is still
    # corrected for an unmirrored image, so geometry and label agree.
    arms = {
        "left": ha.BodyArm("left", wrist_xy=(220.0, 300.0), elbow_xy=(260.0, 220.0)),
        "right": ha.BodyArm("right", wrist_xy=(420.0, 300.0), elbow_xy=(380.0, 220.0)),
    }
    cands = [_hand((215.0, 330.0), label="Right"), _hand((425.0, 330.0), label="Left")]
    out = ha.associate_hands(cands, arms, CFG)
    assert out["left"].candidate_index == 0 and out["right"].candidate_index == 1


def test_ambiguous_hand_stays_unassigned_rather_than_swapped():
    # Wrists crossed close together; one hand right between them with no handedness label.
    arms = {
        "left": ha.BodyArm("left", wrist_xy=(300.0, 300.0), elbow_xy=(240.0, 220.0)),
        "right": ha.BodyArm("right", wrist_xy=(330.0, 300.0), elbow_xy=(390.0, 220.0)),
    }
    cand = _hand((315.0, 335.0), label=None)
    out = ha.associate_hands([cand], arms, CFG)
    assert out == {}


def test_far_candidate_is_out_of_range():
    arms = _arms_front_view()
    far = _hand((50.0, 50.0), label="Right")
    assert ha.associate_hands([far], arms, CFG) == {}


def test_cross_view_agreement_can_break_a_tie():
    arms = {
        "left": ha.BodyArm("left", wrist_xy=(300.0, 300.0), elbow_xy=(240.0, 220.0)),
        "right": ha.BodyArm("right", wrist_xy=(330.0, 300.0), elbow_xy=(390.0, 220.0)),
    }
    cand = _hand((315.0, 335.0), label=None)
    cross = {(0, "left"): 1.0, (0, "right"): -1.0}
    out = ha.associate_hands([cand], arms, CFG, cross_view=cross)
    assert set(out) == {"left"}


def test_two_candidates_one_side_requires_margin():
    arms = _arms_front_view()
    # Two near-identical candidates on the left wrist: neither wins by margin.
    a = _hand((425.0, 330.0), label="Right", seed=1)
    b = _hand((426.0, 331.0), label="Right", seed=2)
    out = ha.associate_hands([a, b], arms, CFG)
    assert "left" not in out
