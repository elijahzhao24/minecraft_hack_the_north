"""Hand/foot OBBs, torso boxes and head sphere against the skeleton ground truth."""

from __future__ import annotations

import numpy as np
import pytest

from hmc_backend.colliders import fit_foot, fit_hand, fit_torso
from hmc_backend.colliders.fit_capsule import body_segments
from hmc_backend.colliders.fit_common import FitConfig, assign_points_to_segments, by_name
from hmc_backend.colliders.models import DisabledCollider, ObbCollider, SphereCollider
from hmc_backend.colliders.subject import SubjectDimensions
from hmc_backend.contracts.enums import FitSource
from hmc_backend.contracts.internal import Landmark3D
from hmc_backend.fixtures import skeleton as skel
from hmc_backend.vision.model_mapping import body_name, hand_name
from tests.test_fit_capsule import _landmarks_from_skeleton

CFG = FitConfig()
EMPTY = np.zeros((0, 3), np.float32)


def _setup(sk, *, drop=frozenset(), xyz=None, subject=None):
    subject = subject or SubjectDimensions()
    lms = by_name(_landmarks_from_skeleton(sk, drop=drop))
    if xyz is None:
        xyz, _ = skel.make_skeleton_points(sk, seed=3)
    segments = body_segments(lms, subject)
    assignment = assign_points_to_segments(xyz.astype(np.float64), segments) if xyz.shape[0] else np.zeros(0, np.int64)
    return lms, xyz, assignment, segments, subject


def _inside_obb(obb: ObbCollider, pts: np.ndarray, slack: float = 0.0) -> np.ndarray:
    local = (pts - np.asarray(obb.center)) @ obb.axes_array().T
    return np.all(np.abs(local) <= np.asarray(obb.half_extents) + slack, axis=1)


def _axes_match(a: np.ndarray, b: np.ndarray, tol_deg: float) -> bool:
    """Same box orientation up to axis sign flips."""
    cos = np.abs(np.einsum("ij,ij->i", a, b))
    return bool(np.all(cos >= np.cos(np.radians(tol_deg))))


# --- hands --------------------------------------------------------------------


@pytest.mark.parametrize("side", ["left", "right"])
def test_hand_obb_is_oriented_and_covers_hand(side):
    sk = skel.neutral_skeleton()
    lms, xyz, assignment, segments, subject = _setup(sk)
    out = fit_hand.fit_hand(side, lms, xyz, assignment, segments, subject, CFG)
    box = out.collider
    assert isinstance(box, ObbCollider), out.report
    assert box.fit_source is FitSource.OBSERVED
    truth_c, truth_axes = skel.hand_frame(sk, side)
    assert _axes_match(box.axes_array(), truth_axes, 5.0)
    # All hand landmarks and nearly all hand surface points lie inside.
    lm_pts = np.stack([lms[hand_name(side, n)].position_stage_m for n in sk.hands[side]])
    assert _inside_obb(box, lm_pts, slack=0.002).all()
    surf = xyz[assignment == next(i for i, s in enumerate(segments) if s.key == f"hand.{side}")]
    truth_local = (surf - truth_c) @ truth_axes.T
    true_hand = surf[np.all(np.abs(truth_local) <= np.asarray(sk.radii.hand_half_extents) + 1e-3, axis=1)]
    assert len(true_hand) > 800
    assert _inside_obb(box, true_hand, slack=0.003).mean() > 0.97
    # Not inflated: longitudinal half ~ truth (fingertips add ~5 mm), thickness ~ truth.
    th = sk.radii.hand_half_extents
    assert box.half_extents[0] == pytest.approx(th[0], abs=0.015)
    assert box.half_extents[2] == pytest.approx(th[2], abs=0.01)
    assert box.half_extents[1] < th[1] + 0.05  # thumb widens the box on one side only
    fields = {u[0] for u in out.subject_updates}
    assert fields == {"hand_width_m", "hand_thickness_m"}


def test_hand_without_orientation_is_disabled():
    sk = skel.neutral_skeleton()
    drop = {hand_name("left", "middle_mcp"), body_name("left_index"), hand_name("left", "index_mcp")}
    lms, xyz, assignment, segments, subject = _setup(sk, drop=drop)
    out = fit_hand.fit_hand("left", lms, xyz, assignment, segments, subject, CFG)
    assert isinstance(out.collider, DisabledCollider)
    assert out.collider.reason in {"missing_wrist_or_mcp", "missing_mcp_span"}


def test_hand_with_orientation_but_no_fingers_uses_labelled_subject_dims():
    sk = skel.neutral_skeleton()
    fingers = {
        hand_name("left", n) for n in sk.hands["left"] if n.endswith(("_pip", "_dip", "_tip", "_ip")) or n.startswith("thumb")
    }
    lms, _, _, segments, subject = _setup(sk, drop=fingers, xyz=EMPTY)
    out = fit_hand.fit_hand("left", lms, EMPTY, np.zeros(0, np.int64), segments, subject, CFG)
    box = out.collider
    assert isinstance(box, ObbCollider)
    assert box.fit_source is FitSource.GLOBAL_DEFAULT
    assert out.report["reason"] == "insufficient_finger_extent"
    assert box.half_extents[1] == pytest.approx(subject.hand_width_m.left.value_m / 2.0, abs=1e-6)
    assert out.subject_updates == ()


def test_hand_landmarks_only_thickness_falls_back_to_subject():
    sk = skel.neutral_skeleton()
    lms, _, _, segments, subject = _setup(sk, xyz=EMPTY)
    out = fit_hand.fit_hand("right", lms, EMPTY, np.zeros(0, np.int64), segments, subject, CFG)
    box = out.collider
    assert isinstance(box, ObbCollider)
    assert box.fit_source is FitSource.GLOBAL_DEFAULT  # thickness unobserved from flat landmarks
    assert out.report["thickness_source"] == "global_default"


def test_degenerate_wrist_mcp_disables():
    sk = skel.neutral_skeleton()
    lms, _, _, segments, subject = _setup(sk, xyz=EMPTY)
    w = lms[hand_name("left", "wrist")].position_stage_m
    lms[hand_name("left", "middle_mcp")] = Landmark3D(hand_name("left", "middle_mcp"), w, True, "registered_prior", 0.5)
    out = fit_hand.fit_hand("left", lms, EMPTY, np.zeros(0, np.int64), segments, subject, CFG)
    assert isinstance(out.collider, DisabledCollider) and out.collider.reason == "degenerate_wrist_mcp"


# --- feet ---------------------------------------------------------------------


@pytest.mark.parametrize("side", ["left", "right"])
def test_planted_foot_uses_floor_normal_and_covers_surface(side):
    sk = skel.neutral_skeleton()
    lms, xyz, assignment, segments, subject = _setup(sk)
    out = fit_foot.fit_foot(side, lms, xyz, assignment, segments, subject, CFG)
    box = out.collider
    assert isinstance(box, ObbCollider), out.report
    assert out.report["planted"] is True and box.fit_source is FitSource.OBSERVED
    _, truth_axes = skel.foot_frame(sk, side)
    assert _axes_match(box.axes_array(), truth_axes, 3.0)
    surf = xyz[assignment == next(i for i, s in enumerate(segments) if s.key == f"foot.{side}")]
    assert _inside_obb(box, surf, slack=0.003).mean() > 0.97
    for n in ("heel", "foot_index"):
        assert _inside_obb(box, np.asarray(sk.body[f"{side}_{n}"])[None], slack=0.001).all()
    th = sk.radii.foot_half_extents
    assert box.half_extents[0] == pytest.approx(th[0], abs=0.015)
    assert box.half_extents[1] == pytest.approx(th[1], abs=0.012)
    assert box.half_extents[2] == pytest.approx(th[2], abs=0.015)  # sole anchor adds ~1 cm below
    assert {u[0] for u in out.subject_updates} == {"foot_width_m", "foot_thickness_m"}


def test_lifted_foot_is_not_flattened():
    sk = skel.lifted_turned_foot_skeleton()
    lms, xyz, assignment, segments, subject = _setup(sk)
    out = fit_foot.fit_foot("left", lms, xyz, assignment, segments, subject, CFG)
    box = out.collider
    assert isinstance(box, ObbCollider), out.report
    assert out.report["planted"] is False
    heel, toe = np.asarray(sk.body["left_heel"]), np.asarray(sk.body["left_foot_index"])
    lo = (toe - heel) / np.linalg.norm(toe - heel)
    assert abs(np.dot(box.axes_array()[0], lo)) > np.cos(np.radians(3.0))
    # Turned ~40 degrees: the longitudinal axis is well off the stage Z axis.
    assert abs(box.axes_array()[0][2]) < np.cos(np.radians(30.0))
    surf = xyz[assignment == next(i for i, s in enumerate(segments) if s.key == "foot.left")]
    assert _inside_obb(box, surf, slack=0.003).mean() > 0.97


def test_steeply_pointed_lifted_foot_follows_heel_to_toe():
    """Toes pointing 40 degrees down: a floor-normal vertical would flatten it."""
    sk = skel.neutral_skeleton()
    b = dict(sk.body)
    ankle = np.array([-0.15, 0.45, 0.05])
    b["left_ankle"] = tuple(ankle)
    b["left_knee"] = (-0.13, 0.75, 0.15)
    a = np.radians(40.0)
    fwd = np.array([0.0, -np.sin(a), np.cos(a)])
    heel = ankle + np.array([0.0, -0.04, 0.0]) - fwd * 0.05
    b["left_heel"] = tuple(heel)
    b["left_foot_index"] = tuple(heel + fwd * 0.22)
    sk2 = skel.Skeleton(body=b, hands=sk.hands, radii=sk.radii, planted_feet=("right",))
    lms, _, _, segments, subject = _setup(sk2, xyz=EMPTY)
    out = fit_foot.fit_foot("left", lms, EMPTY, np.zeros(0, np.int64), segments, subject, CFG)
    box = out.collider
    assert isinstance(box, ObbCollider), out.report
    assert out.report["planted"] is False
    assert abs(np.dot(box.axes_array()[0], fwd)) > np.cos(np.radians(2.0))
    # Fallback dims are labelled and come from foot fields, not shin radius.
    assert box.fit_source is FitSource.GLOBAL_DEFAULT
    assert box.half_extents[1] == pytest.approx(subject.foot_width_m.left.value_m / 2.0 + CFG.obb_padding_m, abs=1e-6)


def test_explicit_planted_override_and_missing_heel():
    sk = skel.neutral_skeleton()
    lms, _, _, segments, subject = _setup(sk, xyz=EMPTY)
    out = fit_foot.fit_foot("right", lms, EMPTY, np.zeros(0, np.int64), segments, subject, CFG, planted=False)
    assert out.report["planted"] is False and isinstance(out.collider, ObbCollider)
    lms2, *_ = _setup(sk, drop={body_name("right_heel")}, xyz=EMPTY)
    out2 = fit_foot.fit_foot("right", lms2, EMPTY, np.zeros(0, np.int64), segments, subject, CFG)
    assert isinstance(out2.collider, DisabledCollider) and out2.collider.reason == "missing_heel_or_toe"


# --- torso and head -----------------------------------------------------------


def test_torso_boxes_cover_torso_surface_without_gap():
    sk = skel.neutral_skeleton()
    lms, xyz, assignment, segments, subject = _setup(sk)
    chest = fit_torso.fit_chest(lms, xyz, assignment, segments, subject, CFG).collider
    pelvis = fit_torso.fit_pelvis(lms, xyz, assignment, segments, subject, CFG).collider
    assert isinstance(chest, ObbCollider) and isinstance(pelvis, ObbCollider)
    assert chest.fit_source is FitSource.OBSERVED and pelvis.fit_source is FitSource.OBSERVED
    (_, truth_axes), _ = skel.torso_frames(sk)
    assert _axes_match(chest.axes_array(), truth_axes, 3.0)
    assert _axes_match(pelvis.axes_array(), truth_axes, 3.0)
    # True torso surface: points on the fixture's chest/pelvis boxes.
    (cc, _), (pc, _) = skel.torso_frames(sk)
    on_chest = np.all(np.abs((xyz - cc) @ truth_axes.T) <= np.asarray(sk.radii.torso_half_extents) + 1e-3, axis=1)
    on_pelvis = np.all(np.abs((xyz - pc) @ truth_axes.T) <= np.asarray(sk.radii.pelvis_half_extents) + 1e-3, axis=1)
    torso_pts = xyz[on_chest | on_pelvis]
    assert len(torso_pts) > 5000
    covered = _inside_obb(chest, torso_pts, 0.004) | _inside_obb(pelvis, torso_pts, 0.004)
    assert covered.mean() > 0.97
    # Lateral/forward extents match the fixture; boxes are not padded wide.
    assert chest.half_extents[1] == pytest.approx(sk.radii.torso_half_extents[1], abs=0.02)
    assert chest.half_extents[2] == pytest.approx(sk.radii.torso_half_extents[2], abs=0.015)
    assert pelvis.half_extents[1] == pytest.approx(sk.radii.pelvis_half_extents[1], abs=0.02)
    assert pelvis.half_extents[2] == pytest.approx(sk.radii.pelvis_half_extents[2], abs=0.015)
    # Chest and pelvis overlap along the up axis (no gap in a bent posture).
    up = truth_axes[0]
    chest_lo = np.dot(np.asarray(chest.center), up) - chest.half_extents[0]
    pelvis_hi = np.dot(np.asarray(pelvis.center), up) + pelvis.half_extents[0]
    assert pelvis_hi > chest_lo


def test_torso_fallback_is_labelled_and_missing_shoulder_disables():
    sk = skel.neutral_skeleton()
    lms, _, _, segments, subject = _setup(sk, xyz=EMPTY)
    chest = fit_torso.fit_chest(lms, EMPTY, np.zeros(0, np.int64), segments, subject, CFG)
    assert isinstance(chest.collider, ObbCollider) and chest.collider.fit_source is FitSource.GLOBAL_DEFAULT
    assert chest.collider.half_extents[2] == pytest.approx(subject.torso_depth_m.value_m / 2.0 + CFG.obb_padding_m, abs=1e-6)
    lms2, *_ = _setup(sk, drop={body_name("left_shoulder")}, xyz=EMPTY)
    out = fit_torso.fit_chest(lms2, EMPTY, np.zeros(0, np.int64), segments, subject, CFG)
    assert isinstance(out.collider, DisabledCollider)


def test_head_sphere_matches_fixture_and_trims_outliers():
    sk = skel.neutral_skeleton()
    xyz, _ = skel.make_skeleton_points(sk, seed=5)
    # Add a "hair/hat" shell of outliers well outside the head.
    rng = np.random.default_rng(1)
    head_c = (np.asarray(sk.body["left_ear"]) + np.asarray(sk.body["right_ear"])) / 2.0
    dirs = rng.normal(size=(150, 3))
    dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
    dirs[:, 1] = np.abs(dirs[:, 1])
    hat = (head_c + dirs * 0.17).astype(np.float32)
    lms, xyz2, assignment, segments, subject = _setup(sk, xyz=np.concatenate([xyz, hat]))
    out = fit_torso.fit_head(lms, xyz2, assignment, segments, subject, CFG)
    head = out.collider
    assert isinstance(head, SphereCollider), out.report
    assert head.fit_source is FitSource.OBSERVED
    assert head.radius == pytest.approx(sk.radii.head, abs=0.012)
    assert np.linalg.norm(np.asarray(head.center) - head_c) < 0.02
    assert out.subject_updates[0][0] == "head_radius_m"


def test_head_without_surface_uses_labelled_default_and_missing_centre_disables():
    sk = skel.neutral_skeleton()
    lms, _, _, segments, subject = _setup(sk, xyz=EMPTY)
    out = fit_torso.fit_head(lms, EMPTY, np.zeros(0, np.int64), segments, subject, CFG)
    assert isinstance(out.collider, SphereCollider) and out.collider.fit_source is FitSource.GLOBAL_DEFAULT
    lms2, *_ = _setup(sk, drop={body_name("head_center")}, xyz=EMPTY)
    out2 = fit_torso.fit_head(lms2, EMPTY, np.zeros(0, np.int64), segments, subject, CFG)
    assert isinstance(out2.collider, DisabledCollider)
