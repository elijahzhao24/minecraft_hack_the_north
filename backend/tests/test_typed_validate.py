"""Typed collider validation: violations become DisabledCollider, never padded geometry."""

from __future__ import annotations

import numpy as np
import pytest

from hmc_backend.colliders.models import (
    REQUIRED_COLLIDERS,
    CapsuleCollider,
    DisabledCollider,
    ObbCollider,
    SphereCollider,
    make_capsule,
    make_obb,
    make_sphere,
)
from hmc_backend.colliders.typed_validate import (
    StageBounds,
    ValidationConfig,
    coverage_report,
    finalize_collider_set,
    validate_fitted,
)
from hmc_backend.contracts.enums import BodyPart, ColliderType, FitSource

CFG = ValidationConfig()
I3 = np.eye(3)


def _head(**kw):
    base = {"center": (0.0, 1.6, 0.0), "radius": 0.11, "fit_source": FitSource.OBSERVED, "quality": 0.9}
    base.update(kw)
    return make_sphere("head", BodyPart.HEAD, **base)


def _forearm(**kw):
    base = {"a": (-0.3, 1.2, 0.0), "b": (-0.5, 0.9, 0.1), "radius": 0.04, "fit_source": FitSource.OBSERVED, "quality": 0.8}
    base.update(kw)
    return make_capsule("arm.left.forearm", BodyPart.LEFT_FOREARM, **base)


def _hand(**kw):
    base = {"center": (-0.55, 0.85, 0.1), "axes": I3, "half_extents": (0.09, 0.05, 0.02), "fit_source": FitSource.OBSERVED, "quality": 0.8}
    base.update(kw)
    return make_obb("hand.left", BodyPart.LEFT_HAND, **base)


def test_valid_colliders_pass_through_unchanged():
    for c in (_head(), _forearm(), _hand()):
        assert validate_fitted(c, CFG) is c


@pytest.mark.parametrize(
    ("collider", "reason"),
    [
        (_head(radius=0.0), "non_positive_radius"),
        (_head(radius=0.3), "radius_exceeds_anatomy"),
        (_head(center=(0.0, float("nan"), 0.0)), "non_finite_center"),
        (_head(center=(0.0, 3.5, 0.0)), "outside_stage"),
        (_head(quality=0.05), "low_quality"),
        (_forearm(b=(-0.3, 1.2, 0.0)), "coincident_endpoints"),
        (_forearm(radius=0.2), "radius_exceeds_anatomy"),
        (_forearm(a=(-3.0, 1.2, 0.0)), "outside_stage"),
        (_hand(half_extents=(0.09, 0.05, -0.01)), "non_positive_half_extent"),
        (_hand(half_extents=(0.2, 0.05, 0.02)), "half_extent_exceeds_anatomy"),
        (_hand(axes=I3 * 1.1), "axes_not_unit"),
        (_hand(axes=np.array([[1, 0, 0], [0.7071, 0.7071, 0], [0, 0, 1]])), "axes_not_orthogonal"),
        (_hand(quality=0.2), "low_quality"),  # hands need >= 0.25
    ],
)
def test_violations_disable_with_reason(collider, reason):
    out = validate_fitted(collider, CFG)
    assert isinstance(out, DisabledCollider), (collider, out)
    assert out.reason == reason
    assert out.id == collider.id and out.body_part is collider.body_part and out.type is collider.type


def test_hand_threshold_is_stricter_than_head():
    assert isinstance(validate_fitted(_hand(quality=0.2), CFG), DisabledCollider)
    assert isinstance(validate_fitted(_head(quality=0.2), CFG), SphereCollider)


def test_spec_mismatch_is_disabled():
    wrong_part = SphereCollider("head", BodyPart.TORSO, (0.0, 1.6, 0.0), 0.11, FitSource.OBSERVED, 0.9)
    assert validate_fitted(wrong_part, CFG).reason == "body_part_mismatch"
    wrong_type = SphereCollider("torso.chest", BodyPart.TORSO, (0.0, 1.2, 0.0), 0.11, FitSource.OBSERVED, 0.9)
    assert validate_fitted(wrong_type, CFG).reason == "type_mismatch"
    unknown = SphereCollider("nose", BodyPart.HEAD, (0.0, 1.6, 0.0), 0.02, FitSource.OBSERVED, 0.9)
    out = validate_fitted(unknown, CFG)
    assert isinstance(out, DisabledCollider) and out.reason == "unknown_id"


def test_left_handed_axes_still_orthonormal_but_det_checked():
    # A reflection has |det| = 1 and passes the determinant magnitude rule; a
    # sheared "almost" basis does not.
    reflected = np.diag([1.0, 1.0, -1.0])
    assert isinstance(validate_fitted(_hand(axes=reflected), CFG), ObbCollider)
    sheared = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 0.999]])
    out = validate_fitted(_hand(axes=sheared), CFG)
    assert isinstance(out, DisabledCollider) and out.reason == "axes_not_unit"


def test_stage_bounds_are_configurable():
    tight = ValidationConfig(stage=StageBounds(min_m=(-0.1, 0.0, -0.1), max_m=(0.1, 2.0, 0.1)))
    assert validate_fitted(_forearm(), tight).reason == "outside_stage"
    assert isinstance(validate_fitted(_head(), tight), SphereCollider)


def test_finalize_completes_required_set_in_stable_order():
    out = finalize_collider_set([_hand(), _head()], CFG)
    assert [c.id for c in out] == [s.id for s in REQUIRED_COLLIDERS]
    by_id = {c.id: c for c in out}
    assert isinstance(by_id["head"], SphereCollider)
    assert isinstance(by_id["hand.left"], ObbCollider)
    missing = by_id["leg.right.shin"]
    assert isinstance(missing, DisabledCollider) and missing.reason == "not_fitted"
    assert missing.body_part is BodyPart.RIGHT_SHIN and missing.type is ColliderType.CAPSULE


def test_finalize_disables_duplicates_and_drops_unknown_ids():
    unknown = SphereCollider("nose", BodyPart.HEAD, (0.0, 1.6, 0.0), 0.02, FitSource.OBSERVED, 0.9)
    out = finalize_collider_set([_head(), _head(radius=0.10), unknown], CFG)
    ids = [c.id for c in out]
    assert "nose" not in ids and ids.count("head") == 1
    head = next(c for c in out if c.id == "head")
    assert isinstance(head, DisabledCollider) and head.reason == "duplicate_id"


def test_finalize_validates_each_and_report_counts():
    out = finalize_collider_set([_head(radius=0.5), _forearm(), _hand()], CFG)
    rep = coverage_report(out)
    assert rep["required"] == len(REQUIRED_COLLIDERS) == len(out)
    assert rep["enabled"] == 2
    assert rep["disabled"]["head"] == "radius_exceeds_anatomy"
    assert rep["disabled"]["foot.left"] == "not_fitted"
    assert rep["types"] == {"sphere": 0, "capsule": 1, "obb": 1}


def test_disabled_input_is_passed_through():
    d = DisabledCollider("head", BodyPart.HEAD, ColliderType.SPHERE, "missing_head_center")
    assert validate_fitted(d, CFG) is d
    assert isinstance(_forearm(), CapsuleCollider)

