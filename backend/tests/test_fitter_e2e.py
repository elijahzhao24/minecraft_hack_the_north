"""AnatomicalCharacterFitter end to end on the synthetic rig + skeleton fixtures.

Rendered RGB-D frames -> skeleton detector -> real reconstruction -> fusion ->
collider fitting -> validation -> transport DTOs. Accuracy targets are the
fixture's known geometry, not self-consistency.
"""

from __future__ import annotations

from uuid import uuid4

import numpy as np
import pytest

from hmc_backend.calibration.synthetic import build_synthetic_rig
from hmc_backend.colliders.fitter import AnatomicalCharacterFitter
from hmc_backend.colliders.geometry import ray_collider
from hmc_backend.colliders.models import (
    REQUIRED_COLLIDERS,
    CapsuleCollider,
    DisabledCollider,
    ObbCollider,
    SphereCollider,
)
from hmc_backend.colliders.validate import validate_colliders
from hmc_backend.contracts.enums import FitSource
from hmc_backend.contracts.internal import PairedFrames
from hmc_backend.fixtures import skeleton as skf
from hmc_backend.reconstruction.reconstruct import CropBounds, merge_clouds, reconstruct_view

CROP = CropBounds(-1.5, 1.5, -0.1, 2.5, -1.5, 1.5, 0.2, 5.0)


@pytest.fixture(scope="module")
def rig():
    return build_synthetic_rig(rgb_size=(480, 360), depth_size=(480, 360))


def _run(rig, sk, *, fitter=None, detector=None, seed=0):
    frames = skf.render_frames(rig, sk, seed=seed)
    detector = detector or skf.SkeletonViewDetector(rig, sk)
    ids = list(frames)
    detections = {d: detector.detect_view(frames[d]) for d in ids}
    clouds = [
        reconstruct_view(frames[d], detections[d], rig.camera(d), CROP, confidence_min=1, source_bit=1 << i)
        for i, d in enumerate(ids)
    ]
    cloud = merge_clouds(clouds, voxel_size_m=0.01, max_points=60_000, seed=1)
    pair = PairedFrames(uuid4(), frames[ids[0]], frames[ids[1]], 0.0, 5.0, rig.calibration_id)
    fitter = fitter or AnatomicalCharacterFitter(rig)
    fitted = fitter.fit_character(pair, detections, cloud, rig.camera(ids[0]))
    return fitter, fitted, cloud


def _typed(fitter):
    return {c.id: c for c in fitter.last_typed_colliders}


def test_neutral_pose_produces_full_valid_collider_set(rig):
    sk = skf.neutral_skeleton()
    fitter, fitted, cloud = _run(rig, sk)
    assert cloud.count > 5000
    ids = [c.id for c in fitted.colliders]
    assert ids == [s.id for s in REQUIRED_COLLIDERS]
    validate_colliders(fitted.colliders)  # transport-level rules hold
    typed = _typed(fitter)
    disabled = {k: v.reason for k, v in typed.items() if isinstance(v, DisabledCollider)}
    assert not disabled, disabled
    assert all(c.fit_source is FitSource.OBSERVED for c in typed.values()), {
        k: v.fit_source for k, v in typed.items()
    }
    rep = fitter.last_report
    assert rep.coverage["enabled"] == len(REQUIRED_COLLIDERS)
    assert set(rep.colliders) == set(typed)


def test_neutral_pose_geometry_matches_fixture(rig):
    sk = skf.neutral_skeleton()
    fitter, _, _ = _run(rig, sk)
    typed = _typed(fitter)
    r = sk.radii
    # Limb radii within ~1.5 cm (reconstructed surface is noisier than the raw cloud).
    for cid, truth in (
        ("arm.left.upper", r.upper_arm), ("arm.right.forearm", r.forearm),
        ("leg.left.thigh", r.thigh), ("leg.right.shin", r.shin),
    ):
        c = typed[cid]
        assert isinstance(c, CapsuleCollider)
        assert c.radius == pytest.approx(truth, abs=0.02), (cid, c.radius, truth)
    head = typed["head"]
    assert isinstance(head, SphereCollider)
    assert head.radius == pytest.approx(r.head, abs=0.015)
    head_c = (np.asarray(sk.body["left_ear"]) + np.asarray(sk.body["right_ear"])) / 2.0
    assert np.linalg.norm(np.asarray(head.center) - head_c) < 0.03
    for side in ("left", "right"):
        hand = typed[f"hand.{side}"]
        assert isinstance(hand, ObbCollider)
        _, truth_axes = skf.hand_frame(sk, side)
        assert np.abs(np.einsum("ij,ij->i", hand.axes_array(), truth_axes)).min() > np.cos(np.radians(12.0))
        foot = typed[f"foot.{side}"]
        assert isinstance(foot, ObbCollider)
        assert foot.half_extents[0] == pytest.approx(r.foot_half_extents[0], abs=0.03)
        # Every hand landmark is inside its box.
        for p in sk.hands[side].values():
            local = (np.asarray(p) - np.asarray(hand.center)) @ hand.axes_array().T
            assert np.all(np.abs(local) <= np.asarray(hand.half_extents) + 0.01), (side, p)


def test_rays_hit_the_correct_parts(rig):
    """Physical localization: rays aimed at fixture points hit the right collider, gaps stay empty."""
    sk = skf.neutral_skeleton()
    fitter, _, _ = _run(rig, sk)
    typed = _typed(fitter)
    enabled = [c for c in typed.values() if not isinstance(c, DisabledCollider)]

    def nearest(origin, target):
        d = np.asarray(target) - np.asarray(origin)
        d = d / np.linalg.norm(d)
        best = None
        for c in enabled:
            t = ray_collider(np.asarray(origin, float), d, c)
            if t is not None and (best is None or t < best[0]):
                best = (t, c.id)
        return best

    origin = (0.0, 1.2, 2.5)  # in front of the subject
    assert nearest(origin, sk.body["left_knee"])[1] in {"leg.left.thigh", "leg.left.shin"}
    assert nearest(origin, sk.body["right_wrist"])[1] in {"arm.right.forearm", "hand.right"}
    assert nearest(origin, sk.hands["left"]["middle_tip"])[1] == "hand.left"
    assert nearest(origin, sk.body["nose"])[1] == "head"
    assert nearest(origin, sk.body["left_foot_index"])[1] == "foot.left"
    # The gap between arm and torso stays empty.
    assert nearest(origin, (-0.29, 1.2, 0.0)) is None


def test_hidden_hand_falls_back_and_learns_subject_only_from_valid_fits(rig):
    sk = skf.neutral_skeleton()
    # First a clean pass to learn the subject.
    fitter, _, _ = _run(rig, sk)
    learned = fitter.subject
    assert learned.forearm_radius_m.left.source is FitSource.OBSERVED
    assert learned.hand_width_m.right.source is FitSource.OBSERVED
    # Now drop the right hand model in both views. Orientation still comes from
    # the pose model's wrist/index/pinky and the hand surface is still in the
    # cloud, so the box is measured from that surface (no hand landmarks).
    det = skf.SkeletonViewDetector(rig, sk, drop_hands={d: {"right"} for d in rig.cameras})
    fitter2, fitted2, _ = _run(rig, sk, fitter=AnatomicalCharacterFitter(rig, subject=learned), detector=det)
    right = _typed(fitter2)["hand.right"]
    assert isinstance(right, ObbCollider), right
    rep = fitter2.last_report.colliders["hand.right"]
    assert rep["orientation_from"] == "pose_model" and rep["landmarks"] == 0 and rep["support"] > 50
    assert right.fit_source is FitSource.OBSERVED
    _, truth_axes = skf.hand_frame(sk, "right")
    assert np.abs(np.einsum("ij,ij->i", right.axes_array(), truth_axes)).min() > np.cos(np.radians(15.0))
    assert right.half_extents[0] == pytest.approx(sk.radii.hand_half_extents[0], abs=0.03)
    validate_colliders(fitted2.colliders)

    # Same, but with no surface either: labelled subject dimensions, and the
    # subject is not "taught" by a default-sourced box.
    from hmc_backend.contracts.internal import ColoredPointCloud

    frames = skf.render_frames(rig, sk)
    ids = list(frames)
    detections = {d: det.detect_view(frames[d]) for d in ids}
    empty = ColoredPointCloud(np.zeros((0, 3), np.float32), np.zeros((0, 4), np.uint8), np.zeros(0, np.uint8))
    pair = PairedFrames(uuid4(), frames[ids[0]], frames[ids[1]], 0.0, 5.0, rig.calibration_id)
    fitter3 = AnatomicalCharacterFitter(rig, subject=learned)
    fitter3.fit_character(pair, detections, empty, rig.camera(ids[0]))
    right3 = _typed(fitter3)["hand.right"]
    assert isinstance(right3, ObbCollider), right3
    assert right3.fit_source is FitSource.SUBJECT_DEFAULT
    assert fitter3.last_report.colliders["hand.right"]["reason"] == "insufficient_finger_extent"
    assert right3.half_extents[1] == pytest.approx(learned.hand_width_m.right.value_m / 2.0, abs=1e-6)
    assert fitter3.subject.hand_width_m.right == learned.hand_width_m.right


def test_empty_cloud_yields_labelled_defaults_not_observed(rig):
    sk = skf.neutral_skeleton()
    frames = skf.render_frames(rig, sk)
    det = skf.SkeletonViewDetector(rig, sk)
    ids = list(frames)
    detections = {d: det.detect_view(frames[d]) for d in ids}
    from hmc_backend.contracts.internal import ColoredPointCloud

    empty = ColoredPointCloud(np.zeros((0, 3), np.float32), np.zeros((0, 4), np.uint8), np.zeros(0, np.uint8))
    pair = PairedFrames(uuid4(), frames[ids[0]], frames[ids[1]], 0.0, 5.0, rig.calibration_id)
    fitter = AnatomicalCharacterFitter(rig)
    fitted = fitter.fit_character(pair, detections, empty, rig.camera(ids[0]))
    assert "empty_cloud" in fitter.last_report.warnings
    typed = _typed(fitter)
    for c in typed.values():
        assert c.fit_source is not FitSource.OBSERVED, c
    # Joints are still observed (triangulated), so capsules exist with labelled radii.
    assert isinstance(typed["leg.left.thigh"], CapsuleCollider)
    assert typed["leg.left.thigh"].fit_source is FitSource.GLOBAL_DEFAULT
    assert fitter.subject.thigh_radius_m.left.source is FitSource.GLOBAL_DEFAULT  # nothing learned
    validate_colliders(fitted.colliders)


def test_lifted_foot_pose_end_to_end(rig):
    sk = skf.lifted_turned_foot_skeleton()
    fitter, fitted, _ = _run(rig, sk)
    typed = _typed(fitter)
    foot = typed["foot.left"]
    assert isinstance(foot, ObbCollider), foot
    assert fitter.last_report.colliders["foot.left"]["planted"] is False
    heel, toe = np.asarray(sk.body["left_heel"]), np.asarray(sk.body["left_foot_index"])
    lo = (toe - heel) / np.linalg.norm(toe - heel)
    assert abs(np.dot(foot.axes_array()[0], lo)) > np.cos(np.radians(10.0))
    assert fitter.last_report.colliders["foot.right"]["planted"] is True
    validate_colliders(fitted.colliders)
