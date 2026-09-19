"""Landmark fusion against the known-skeleton fixture: provenance, accuracy, no fake zeros."""

from __future__ import annotations

import numpy as np
import pytest

from hmc_backend.calibration.synthetic import build_synthetic_rig
from hmc_backend.contracts.enums import LandmarkSource
from hmc_backend.fixtures import skeleton as skf
from hmc_backend.vision.landmarks import FusionConfig, ViewInput, fuse_landmarks
from hmc_backend.vision.model_mapping import ALL_CANONICAL_NAMES, body_name, hand_name

MAJOR_JOINTS = (
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow", "left_wrist", "right_wrist",
    "left_hip", "right_hip", "left_knee", "right_knee", "left_ankle", "right_ankle",
)


@pytest.fixture(scope="module")
def rig():
    return build_synthetic_rig(rgb_size=(480, 360), depth_size=(480, 360))


@pytest.fixture(scope="module")
def neutral(rig):
    sk = skf.neutral_skeleton()
    frames = skf.render_frames(rig, sk)
    return sk, frames


def _views(rig, frames, detector):
    return [ViewInput(f, detector.detect_view(f), rig.camera(d)) for d, f in frames.items()]


def test_fusion_covers_every_canonical_name_and_never_emits_origin(rig, neutral):
    sk, frames = neutral
    det = skf.SkeletonViewDetector(rig, sk)
    res = fuse_landmarks(_views(rig, frames, det), FusionConfig())
    names = [lm.name for lm in res.landmarks]
    assert names == list(ALL_CANONICAL_NAMES)
    for lm in res.landmarks:
        if lm.valid:
            assert lm.position_stage_m is not None
            assert np.isfinite(lm.position_stage_m).all()
            assert np.linalg.norm(lm.position_stage_m) > 0.01
        else:
            assert lm.position_stage_m is None
            assert lm.source == LandmarkSource.UNAVAILABLE.value


def test_major_joints_are_observed_and_accurate(rig, neutral):
    sk, frames = neutral
    det = skf.SkeletonViewDetector(rig, sk)
    res = fuse_landmarks(_views(rig, frames, det), FusionConfig())
    by = res.by_name()
    errs = {}
    for short in MAJOR_JOINTS:
        lm = by[body_name(short)]
        assert lm.valid, short
        assert lm.source in (LandmarkSource.TRIANGULATED.value, LandmarkSource.DEPTH_NEIGHBORHOOD.value), (short, lm.source)
        errs[short] = float(np.linalg.norm(np.subtract(lm.position_stage_m, sk.body[short])))
    # Surface samples sit up to one limb radius off the joint centre; triangulation is exact.
    assert max(errs.values()) < 0.09, errs
    assert np.median(list(errs.values())) < 0.03, errs
    # Both cameras contributed somewhere.
    assert any(len(by[body_name(s)].observed_by) == 2 for s in MAJOR_JOINTS)


def test_noisy_2d_detections_still_localize_major_joints(rig, neutral):
    sk, frames = neutral
    det = skf.SkeletonViewDetector(rig, sk, pixel_noise_px=1.5, seed=7)
    by = fuse_landmarks(_views(rig, frames, det), FusionConfig()).by_name()
    errs = [float(np.linalg.norm(np.subtract(by[body_name(s)].position_stage_m, sk.body[s]))) for s in MAJOR_JOINTS]
    assert all(by[body_name(s)].valid for s in MAJOR_JOINTS)
    assert np.median(errs) < 0.04 and max(errs) < 0.12, errs


def test_hidden_landmarks_come_from_registered_prior_not_zero(rig, neutral):
    sk, frames = neutral
    hidden = {"left_knee", "left_ankle", "left_heel", "left_foot_index"}
    det = skf.SkeletonViewDetector(rig, sk, hidden={d: hidden for d in frames})
    res = fuse_landmarks(_views(rig, frames, det), FusionConfig())
    by = res.by_name()
    assert res.report.pose_registration is not None
    for short in hidden:
        lm = by[body_name(short)]
        assert lm.valid and lm.source == LandmarkSource.REGISTERED_MODEL_PRIOR.value
        assert lm.confidence is not None and lm.confidence <= 0.5
        assert np.linalg.norm(np.subtract(lm.position_stage_m, sk.body[short])) < 0.08


def test_single_view_depth_occluder_hit_is_replaced_by_prior(rig, neutral):
    """The right heel is behind the shin for the front camera and hidden from the
    side camera: its lone depth sample lands on the shin surface (~17 cm off).
    Fusion must recognize the occluder hit against the registered prior rather
    than publish the shin as the heel."""
    sk, frames = neutral
    side_cam = [d for d in frames if d != next(iter(frames))]
    det = skf.SkeletonViewDetector(rig, sk, hidden={d: {"right_heel"} for d in side_cam})
    res = fuse_landmarks(_views(rig, frames, det), FusionConfig())
    heel = res.by_name()[body_name("right_heel")]
    assert heel.valid
    err = float(np.linalg.norm(np.subtract(heel.position_stage_m, sk.body["right_heel"])))
    assert err < 0.08, (heel.source, err)
    gate = res.report.per_landmark[body_name("right_heel")].get("depth_prior_gate")
    if heel.source == LandmarkSource.REGISTERED_MODEL_PRIOR.value:
        assert gate is not None and gate["replaced"] is True
    # Landmarks whose lone depth sample agrees with the prior are kept as depth observations.
    kept = [
        n for n, rec in res.report.per_landmark.items()
        if rec.get("depth_prior_gate", {}).get("replaced") is False
    ]
    assert all(res.by_name()[n].source == LandmarkSource.DEPTH_NEIGHBORHOOD.value for n in kept)


def test_prior_scale_is_recovered_not_trusted(rig, neutral):
    sk, frames = neutral
    hidden = {"left_knee"}
    det = skf.SkeletonViewDetector(rig, sk, hidden={d: hidden for d in frames}, prior_scale=1.3)
    res = fuse_landmarks(_views(rig, frames, det), FusionConfig())
    assert res.report.pose_registration["scale"] == pytest.approx(1.3, abs=0.1)
    lm = res.by_name()[body_name("left_knee")]
    assert np.linalg.norm(np.subtract(lm.position_stage_m, sk.body["left_knee"])) < 0.08


def test_derived_centres(rig, neutral):
    sk, frames = neutral
    det = skf.SkeletonViewDetector(rig, sk)
    by = fuse_landmarks(_views(rig, frames, det), FusionConfig()).by_name()
    pelvis = by[body_name("pelvis_center")]
    head = by[body_name("head_center")]
    assert pelvis.valid and pelvis.source == LandmarkSource.DERIVED.value
    expected_pelvis = (np.asarray(sk.body["left_hip"]) + np.asarray(sk.body["right_hip"])) / 2.0
    assert np.linalg.norm(np.subtract(pelvis.position_stage_m, expected_pelvis)) < 0.08
    assert head.valid and head.source == LandmarkSource.DERIVED.value
    # Head centre is between the ears, not at the nose (which is ~10 cm forward).
    ears_mid = (np.asarray(sk.body["left_ear"]) + np.asarray(sk.body["right_ear"])) / 2.0
    assert np.linalg.norm(np.subtract(head.position_stage_m, ears_mid)) < 0.08
    assert np.linalg.norm(np.subtract(head.position_stage_m, sk.body["nose"])) > 0.04


def test_hands_are_fused_and_attached(rig, neutral):
    sk, frames = neutral
    det = skf.SkeletonViewDetector(rig, sk)
    by = fuse_landmarks(_views(rig, frames, det), FusionConfig()).by_name()
    for side in ("left", "right"):
        tip = by[hand_name(side, "index_tip")]
        assert tip.valid
        assert np.linalg.norm(np.subtract(tip.position_stage_m, sk.hands[side]["index_tip"])) < 0.06


def test_hand_missing_in_both_views_is_unavailable(rig, neutral):
    sk, frames = neutral
    det = skf.SkeletonViewDetector(rig, sk, drop_hands={d: {"right"} for d in frames})
    by = fuse_landmarks(_views(rig, frames, det), FusionConfig()).by_name()
    assert not by[hand_name("right", "index_tip")].valid
    assert by[hand_name("right", "index_tip")].position_stage_m is None
    assert by[hand_name("left", "index_tip")].valid


def test_empty_views_yield_all_unavailable():
    res = fuse_landmarks([], FusionConfig())
    assert all(not lm.valid and lm.position_stage_m is None for lm in res.landmarks)
