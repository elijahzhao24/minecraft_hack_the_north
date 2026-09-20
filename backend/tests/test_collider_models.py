"""Typed collider union, stable ID table, and the transport adapter."""

from __future__ import annotations

import numpy as np
import pytest

from hmc_backend.colliders import models as cm
from hmc_backend.colliders.validate import validate_colliders
from hmc_backend.contracts.enums import BodyPart, ColliderType, FitSource


def test_required_collider_table_is_complete_and_unique():
    ids = [s.id for s in cm.REQUIRED_COLLIDERS]
    assert len(ids) == len(set(ids)) == 15
    assert "head" in ids and "torso.chest" in ids and "torso.pelvis" in ids
    for side in ("left", "right"):
        for part in ("arm.{s}.upper", "arm.{s}.forearm", "hand.{s}", "leg.{s}.thigh", "leg.{s}.shin", "foot.{s}"):
            assert part.format(s=side) in ids
    # body_part enum matches the side encoded in the id.
    assert cm.SPEC_BY_ID["hand.left"].body_part is BodyPart.LEFT_HAND
    assert cm.SPEC_BY_ID["leg.right.shin"].body_part is BodyPart.RIGHT_SHIN
    assert cm.SPEC_BY_ID["foot.left"].type is ColliderType.OBB
    assert cm.SPEC_BY_ID["arm.right.upper"].type is ColliderType.CAPSULE


def test_disabled_has_no_geometry_and_maps_to_invalid_transport():
    d = cm.disabled("hand.right", "ambiguous_association")
    assert isinstance(d, cm.DisabledCollider)
    assert d.fit_source is FitSource.DISABLED
    assert not hasattr(d, "center")
    t = cm.to_transport(d)
    assert t.valid is False
    assert t.fit_source == "disabled"
    assert t.type == "obb"
    assert t.center_stage_m is None and t.axes_row_major is None and t.half_extents_m is None
    validate_colliders((t,))


@pytest.mark.parametrize(
    "typed",
    [
        cm.make_sphere("head", BodyPart.HEAD, (0.0, 1.7, 0.0), 0.11, FitSource.OBSERVED, 0.9),
        cm.make_capsule(
            "arm.left.forearm", BodyPart.LEFT_FOREARM, (-0.2, 1.3, 0.0), (-0.4, 1.1, 0.1), 0.05, FitSource.SUBJECT_DEFAULT, 0.6
        ),
        cm.make_obb(
            "hand.left", BodyPart.LEFT_HAND, (-0.5, 1.1, 0.1), np.eye(3), (0.1, 0.05, 0.02), FitSource.OBSERVED, 0.8
        ),
    ],
)
def test_transport_round_trip(typed):
    t = cm.to_transport(typed)
    assert t.valid is True
    assert t.id == typed.id
    assert t.body_part == typed.body_part.value
    assert t.type == typed.type.value
    validate_colliders((t,))
    back = cm.from_transport(t)
    assert back == typed


def test_obb_axes_are_stored_as_rows():
    axes = np.array([[0, 1, 0], [0, 0, 1], [1, 0, 0]], dtype=float)
    o = cm.make_obb("foot.right", BodyPart.RIGHT_FOOT, (0, 0, 0), axes, (0.1, 0.03, 0.05), FitSource.OBSERVED, 0.5)
    assert o.axes_array().tolist() == axes.tolist()
    flat = cm.to_transport(o).axes_row_major
    assert flat == (0, 1, 0, 0, 0, 1, 1, 0, 0)


def test_unknown_id_is_rejected():
    with pytest.raises(KeyError):
        cm.disabled("torso", "legacy_id")

