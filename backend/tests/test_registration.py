"""Prior registration never treats hip/hand-centred coordinates as stage coordinates."""

from __future__ import annotations

import numpy as np
import pytest

from hmc_backend.vision import registration as reg
from hmc_backend.vision.model_mapping import POSE_LANDMARK_NAMES, hand_index, pose_index

CFG = reg.RegistrationConfig()


def _rot(axis, deg):
    axis = np.asarray(axis, float)
    axis /= np.linalg.norm(axis)
    a = np.radians(deg)
    k = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    return np.eye(3) + np.sin(a) * k + (1 - np.cos(a)) * (k @ k)


def _stage_skeleton() -> dict[str, np.ndarray]:
    """A plausible standing skeleton in stage meters."""
    return {
        "nose": np.array([0.0, 1.65, 0.05]),
        "left_shoulder": np.array([-0.18, 1.45, 0.0]),
        "right_shoulder": np.array([0.18, 1.45, 0.0]),
        "left_elbow": np.array([-0.45, 1.1, 0.05]),
        "right_elbow": np.array([0.45, 1.1, 0.05]),
        "left_wrist": np.array([-0.6, 0.8, 0.1]),
        "right_wrist": np.array([0.6, 0.8, 0.1]),
        "left_hip": np.array([-0.08, 0.95, 0.0]),
        "right_hip": np.array([0.08, 0.95, 0.0]),
        "left_knee": np.array([-0.1, 0.5, 0.0]),
        "right_knee": np.array([0.1, 0.5, 0.0]),
        "left_ankle": np.array([-0.1, 0.05, 0.03]),
        "right_ankle": np.array([0.1, 0.05, 0.03]),
    }


def _hip_centred_prior(stage: dict[str, np.ndarray], *, scale=1.0, rot=None, noise=0.0, seed=0):
    """Build a 33x3 'world' prior: hip-centred, rotated, scaled — as MediaPipe would emit."""
    rng = np.random.default_rng(seed)
    rot = np.eye(3) if rot is None else rot
    hips = (stage["left_hip"] + stage["right_hip"]) / 2.0
    prior = np.zeros((33, 3))
    for name in POSE_LANDMARK_NAMES:
        p = stage.get(name, hips + rng.normal(scale=0.2, size=3))
        prior[pose_index(name)] = rot.T @ ((p - hips) / scale) + rng.normal(scale=noise, size=3)
    return prior


def test_umeyama_recovers_similarity():
    rng = np.random.default_rng(1)
    src = rng.normal(size=(10, 3))
    r = _rot([0.3, 1.0, 0.2], 37.0)
    dst = 1.2 * (src @ r.T) + np.array([0.5, -0.2, 2.0])
    s, rr, t = reg.umeyama(src, dst)
    assert s == pytest.approx(1.2)
    assert np.allclose(rr, r, atol=1e-9)
    assert np.allclose(t, [0.5, -0.2, 2.0], atol=1e-9)


def test_pose_prior_is_moved_into_stage_not_used_raw():
    stage = _stage_skeleton()
    prior = _hip_centred_prior(stage, scale=1.05, rot=_rot([0, 1, 0], 25.0), noise=0.004)
    observed = {n: tuple(stage[n]) for n in reg.POSE_ANCHOR_NAMES}
    sim = reg.register_pose_prior(prior, observed, CFG)
    assert sim.scale == pytest.approx(1.05, abs=0.03)
    assert sim.rms_residual_m < 0.02
    # Raw prior nose is near the origin (hip-centred); registered nose is at head height.
    raw_nose = prior[pose_index("nose")]
    assert abs(raw_nose[1]) < 1.0
    reg_nose = sim.apply(raw_nose)[0]
    assert reg_nose == pytest.approx(stage["nose"], abs=0.03)
    # And the hips land on the observed hips, not at the stage origin.
    reg_hip = sim.apply(prior[pose_index("left_hip")])[0]
    assert reg_hip == pytest.approx(stage["left_hip"], abs=0.02)
    assert np.linalg.norm(reg_hip) > 0.5


def test_pose_prior_rejects_too_few_or_collinear_anchors():
    stage = _stage_skeleton()
    prior = _hip_centred_prior(stage)
    with pytest.raises(reg.RegistrationRejected, match="too_few_anchors"):
        reg.register_pose_prior(prior, {"left_shoulder": tuple(stage["left_shoulder"])}, CFG)
    # Four anchors on a vertical line.
    line = {
        "left_shoulder": (0.0, 1.4, 0.0),
        "right_shoulder": (0.0, 1.2, 0.0),
        "left_hip": (0.0, 1.0, 0.0),
        "right_hip": (0.0, 0.8, 0.0),
    }
    with pytest.raises(reg.RegistrationRejected, match="collinear"):
        reg.register_pose_prior(prior, line, CFG)


def test_pose_prior_rejects_implausible_scale_and_large_residual():
    stage = _stage_skeleton()
    observed = {n: tuple(stage[n]) for n in reg.POSE_ANCHOR_NAMES}
    tiny = _hip_centred_prior(stage, scale=0.3)  # prior claims a 3x smaller person
    with pytest.raises(reg.RegistrationRejected, match="scale"):
        reg.register_pose_prior(tiny, observed, CFG)
    noisy = _hip_centred_prior(stage, noise=0.15, seed=3)
    with pytest.raises(reg.RegistrationRejected):
        reg.register_pose_prior(noisy, observed, CFG)


def test_irls_downweights_a_single_outlier_anchor():
    stage = _stage_skeleton()
    prior = _hip_centred_prior(stage)
    observed = {n: tuple(stage[n]) for n in reg.POSE_ANCHOR_NAMES}
    observed["right_knee"] = (0.1, 0.5, 0.6)  # 60 cm forward: a bad depth sample
    sim = reg.register_pose_prior(prior, observed, CFG)
    assert "right_knee" not in sim.inlier_names
    assert sim.rms_residual_m < 0.02


def test_hand_prior_registers_against_wrist_and_mcps():
    # Hand-centred prior: wrist + 4 MCPs + fingertips in a flat palm.
    prior = np.zeros((21, 3))
    prior[hand_index("wrist")] = (0.0, 0.0, 0.0)
    for i, n in enumerate(("index_mcp", "middle_mcp", "ring_mcp", "pinky_mcp")):
        prior[hand_index(n)] = (0.02 * i - 0.03, 0.09, 0.0)
    prior[hand_index("middle_tip")] = (-0.01, 0.17, 0.0)
    prior -= prior[[0, 5, 9, 13, 17]].mean(axis=0)  # hand-centred like MediaPipe
    r = _rot([1, 0, 0], -80.0)
    offset = np.array([-0.6, 0.8, 0.1])
    stage_pts = (prior @ r.T) + offset
    observed = {n: tuple(stage_pts[hand_index(n)]) for n in reg.HAND_ANCHOR_NAMES}
    sim = reg.register_hand_prior(prior, observed, CFG)
    tip = sim.apply(prior[hand_index("middle_tip")])[0]
    assert tip == pytest.approx(stage_pts[hand_index("middle_tip")], abs=1e-6)
    with pytest.raises(reg.RegistrationRejected):
        reg.register_hand_prior(prior, {"wrist": observed["wrist"], "index_mcp": observed["index_mcp"]}, CFG)

