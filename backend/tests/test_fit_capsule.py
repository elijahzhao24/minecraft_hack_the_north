"""Limb capsules: observed radius from surface points, labelled fallbacks, no lengthening."""

from __future__ import annotations

import numpy as np
import pytest

from hmc_backend.colliders import fit_capsule as fc
from hmc_backend.colliders.fit_common import FitConfig, by_name
from hmc_backend.colliders.models import CapsuleCollider, DisabledCollider
from hmc_backend.colliders.subject import DimensionEstimate, SubjectDimensions
from hmc_backend.contracts.enums import FitSource
from hmc_backend.contracts.internal import Landmark3D
from hmc_backend.fixtures.skeleton import make_skeleton_points, neutral_skeleton
from hmc_backend.vision.model_mapping import ALL_CANONICAL_NAMES, body_name

CFG = FitConfig()


def _landmarks_from_skeleton(sk, *, drop: set[str] = frozenset()) -> tuple[Landmark3D, ...]:
    out = []
    for name in ALL_CANONICAL_NAMES:
        if name.startswith("body."):
            short = name[len("body.") :]
            if short == "pelvis_center":
                p = (np.asarray(sk.body["left_hip"]) + np.asarray(sk.body["right_hip"])) / 2.0
            elif short == "head_center":
                p = (np.asarray(sk.body["left_ear"]) + np.asarray(sk.body["right_ear"])) / 2.0
            else:
                p = sk.body[short]
        else:
            _, side, short = name.split(".")
            p = sk.hands[side][short]
        if name in drop:
            out.append(Landmark3D(name, None, False, "unavailable"))
        else:
            out.append(Landmark3D(name, tuple(float(x) for x in p), True, "triangulated", 0.9))
    return tuple(out)


@pytest.fixture(scope="module")
def scene():
    sk = neutral_skeleton()
    xyz, _ = make_skeleton_points(sk, seed=1)
    return sk, xyz


def test_observed_radii_match_ground_truth(scene):
    sk, xyz = scene
    lms = by_name(_landmarks_from_skeleton(sk))
    outcomes, _, _ = fc.fit_all_limbs(lms, xyz, SubjectDimensions(), CFG)
    truth = {"upper": sk.radii.upper_arm, "forearm": sk.radii.forearm, "thigh": sk.radii.thigh, "shin": sk.radii.shin}
    for o in outcomes:
        c = o.collider
        assert isinstance(c, CapsuleCollider), o.report
        assert c.fit_source is FitSource.OBSERVED, (c.id, o.report)
        part = c.id.split(".")[-1]
        assert c.radius == pytest.approx(truth[part], abs=0.012), (c.id, c.radius, truth[part])
        assert o.subject_update is not None and o.subject_update[2].source is FitSource.OBSERVED
        # Endpoints are exactly the joints; never lengthened.
        a_name, b_name = o.report["joints"]
        assert c.a == pytest.approx(sk.body[a_name]) and c.b == pytest.approx(sk.body[b_name])


def test_torso_points_do_not_inflate_upper_arm(scene):
    sk, xyz = scene
    lms = by_name(_landmarks_from_skeleton(sk))
    outcomes, segments, assignment = fc.fit_all_limbs(lms, xyz, SubjectDimensions(), CFG)
    upper = next(o for o in outcomes if o.collider.id == "arm.left.upper")
    # The shoulder sits on the torso; without segment assignment the torso surface
    # (half-width 0.17) would dominate. The fitted radius stays near 5 cm.
    assert upper.collider.radius < 0.08
    torso_idx = next(i for i, s in enumerate(segments) if s.key == "torso")
    assert (assignment == torso_idx).sum() > 1000


def test_missing_joint_disables_and_insufficient_support_uses_labelled_fallback(scene):
    sk, _ = scene
    lms = by_name(_landmarks_from_skeleton(sk, drop={body_name("left_wrist")}))
    outcomes, _, _ = fc.fit_all_limbs(lms, np.zeros((0, 3), np.float32), SubjectDimensions(), CFG)
    by_id = {o.collider.id: o for o in outcomes}
    fore = by_id["arm.left.forearm"].collider
    assert isinstance(fore, DisabledCollider) and fore.reason == "missing_joint"
    # No cloud at all: joints valid but no support -> global default, labelled.
    thigh = by_id["leg.right.thigh"]
    assert isinstance(thigh.collider, CapsuleCollider)
    assert thigh.collider.fit_source is FitSource.GLOBAL_DEFAULT
    assert thigh.report["reason"] == "insufficient_support"
    assert thigh.subject_update is None


def test_subject_default_is_used_when_available(scene):
    sk, _ = scene
    lms = by_name(_landmarks_from_skeleton(sk))
    subject = SubjectDimensions().updated(
        "shin_radius_m", DimensionEstimate(0.061, FitSource.OBSERVED, 100, 0.002), "left"
    )
    outcomes, _, _ = fc.fit_all_limbs(lms, np.zeros((0, 3), np.float32), subject, CFG)
    shin = next(o.collider for o in outcomes if o.collider.id == "leg.left.shin")
    assert shin.fit_source is FitSource.SUBJECT_DEFAULT and shin.radius == pytest.approx(0.061)
    other = next(o.collider for o in outcomes if o.collider.id == "leg.right.shin")
    assert other.fit_source is FitSource.GLOBAL_DEFAULT


def test_radius_is_capped_to_anatomy(scene):
    sk, _ = scene
    lms = by_name(_landmarks_from_skeleton(sk))
    # A fat blob of points around the left forearm.
    a, b = np.asarray(sk.body["left_elbow"]), np.asarray(sk.body["left_wrist"])
    rng = np.random.default_rng(0)
    t = rng.random(500)
    centres = a + np.outer(t, b - a)
    blob = (centres + rng.normal(scale=0.3, size=(500, 3))).astype(np.float32)
    outcomes, _, _ = fc.fit_all_limbs(lms, blob, SubjectDimensions(), CFG)
    fore = next(o.collider for o in outcomes if o.collider.id == "arm.left.forearm")
    assert isinstance(fore, CapsuleCollider) and fore.radius <= CFG.max_limb_radius_m


def test_coincident_joints_disable():
    sk = neutral_skeleton()
    lms = by_name(_landmarks_from_skeleton(sk))
    lms[body_name("right_knee")] = Landmark3D(body_name("right_knee"), sk.body["right_ankle"], True, "triangulated", 0.9)
    outcomes, _, _ = fc.fit_all_limbs(lms, np.zeros((0, 3), np.float32), SubjectDimensions(), CFG)
    shin = next(o.collider for o in outcomes if o.collider.id == "leg.right.shin")
    assert isinstance(shin, DisabledCollider) and shin.reason == "coincident_joints"
