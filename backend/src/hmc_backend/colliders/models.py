"""Typed collider models used *inside* the fitting code.

The transport DTO (:class:`hmc_backend.contracts.internal.Collider`) is a
single record with optional geometry fields so it can round-trip the wire
format. Geometry functions must never accept a zero-sized placeholder by
accident, so fitting works with a frozen discriminated union instead:

* :class:`SphereCollider`, :class:`CapsuleCollider`, :class:`ObbCollider`
  always carry complete geometry.
* :class:`DisabledCollider` carries identity plus a human-readable reason and
  no geometry at all.

:func:`to_transport` maps the union onto the transport DTO (``valid=False`` and
no geometry for the disabled case). :data:`REQUIRED_COLLIDERS` is the stable ID
table every fitted character must cover, so a missing part is always reported
explicitly rather than silently absent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

import numpy as np

from hmc_backend.contracts.enums import BodyPart, ColliderType, FitSource
from hmc_backend.contracts.internal import Collider

Vec3 = tuple[float, float, float]
Mat3Rows = tuple[Vec3, Vec3, Vec3]
Side = Literal["left", "right"]


def _vec3(v) -> Vec3:
    arr = np.asarray(v, dtype=np.float64).reshape(3)
    return (float(arr[0]), float(arr[1]), float(arr[2]))


def _rows(m) -> Mat3Rows:
    arr = np.asarray(m, dtype=np.float64).reshape(3, 3)
    return (_vec3(arr[0]), _vec3(arr[1]), _vec3(arr[2]))


@dataclass(frozen=True, slots=True)
class SphereCollider:
    id: str
    body_part: BodyPart
    center: Vec3
    radius: float
    fit_source: FitSource
    quality: float | None

    @property
    def type(self) -> ColliderType:
        return ColliderType.SPHERE


@dataclass(frozen=True, slots=True)
class CapsuleCollider:
    id: str
    body_part: BodyPart
    a: Vec3
    b: Vec3
    radius: float
    fit_source: FitSource
    quality: float | None

    @property
    def type(self) -> ColliderType:
        return ColliderType.CAPSULE


@dataclass(frozen=True, slots=True)
class ObbCollider:
    id: str
    body_part: BodyPart
    center: Vec3
    axes: Mat3Rows  # three unit row vectors, right-handed
    half_extents: Vec3
    fit_source: FitSource
    quality: float | None

    @property
    def type(self) -> ColliderType:
        return ColliderType.OBB

    def axes_array(self) -> np.ndarray:
        return np.asarray(self.axes, dtype=np.float64)


@dataclass(frozen=True, slots=True)
class DisabledCollider:
    id: str
    body_part: BodyPart
    type: ColliderType
    reason: str

    @property
    def fit_source(self) -> FitSource:
        return FitSource.DISABLED


FittedCollider = SphereCollider | CapsuleCollider | ObbCollider | DisabledCollider
GeometryCollider = SphereCollider | CapsuleCollider | ObbCollider


def make_sphere(id: str, body_part: BodyPart, center, radius: float, fit_source: FitSource, quality: float | None) -> SphereCollider:
    return SphereCollider(id, body_part, _vec3(center), float(radius), fit_source, quality)


def make_capsule(id: str, body_part: BodyPart, a, b, radius: float, fit_source: FitSource, quality: float | None) -> CapsuleCollider:
    return CapsuleCollider(id, body_part, _vec3(a), _vec3(b), float(radius), fit_source, quality)


def make_obb(id: str, body_part: BodyPart, center, axes, half_extents, fit_source: FitSource, quality: float | None) -> ObbCollider:
    return ObbCollider(id, body_part, _vec3(center), _rows(axes), _vec3(half_extents), fit_source, quality)


# --- Stable ID table ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ColliderSpec:
    id: str
    body_part: BodyPart
    type: ColliderType


def _side_part(side: Side, part: str) -> BodyPart:
    return BodyPart(f"{side}_{part}")


REQUIRED_COLLIDERS: Final[tuple[ColliderSpec, ...]] = (
    ColliderSpec("head", BodyPart.HEAD, ColliderType.SPHERE),
    ColliderSpec("torso.chest", BodyPart.TORSO, ColliderType.OBB),
    ColliderSpec("torso.pelvis", BodyPart.PELVIS, ColliderType.OBB),
    *(ColliderSpec(f"arm.{s}.upper", _side_part(s, "upper_arm"), ColliderType.CAPSULE) for s in ("left", "right")),
    *(ColliderSpec(f"arm.{s}.forearm", _side_part(s, "forearm"), ColliderType.CAPSULE) for s in ("left", "right")),
    *(ColliderSpec(f"hand.{s}", _side_part(s, "hand"), ColliderType.OBB) for s in ("left", "right")),
    *(ColliderSpec(f"leg.{s}.thigh", _side_part(s, "thigh"), ColliderType.CAPSULE) for s in ("left", "right")),
    *(ColliderSpec(f"leg.{s}.shin", _side_part(s, "shin"), ColliderType.CAPSULE) for s in ("left", "right")),
    *(ColliderSpec(f"foot.{s}", _side_part(s, "foot"), ColliderType.OBB) for s in ("left", "right")),
)

SPEC_BY_ID: Final[dict[str, ColliderSpec]] = {s.id: s for s in REQUIRED_COLLIDERS}


def disabled(spec_id: str, reason: str) -> DisabledCollider:
    spec = SPEC_BY_ID[spec_id]
    return DisabledCollider(spec.id, spec.body_part, spec.type, reason)


# --- Transport adapter -------------------------------------------------------


def to_transport(c: FittedCollider) -> Collider:
    """Map a typed collider onto the transport DTO (``valid=False`` when disabled)."""
    if isinstance(c, DisabledCollider):
        return Collider(
            id=c.id,
            body_part=c.body_part.value,
            type=c.type.value,
            valid=False,
            fit_source=FitSource.DISABLED.value,
            quality=None,
        )
    if isinstance(c, SphereCollider):
        return Collider(
            id=c.id,
            body_part=c.body_part.value,
            type="sphere",
            valid=True,
            fit_source=c.fit_source.value,
            quality=c.quality,
            center_stage_m=c.center,
            radius_m=c.radius,
        )
    if isinstance(c, CapsuleCollider):
        return Collider(
            id=c.id,
            body_part=c.body_part.value,
            type="capsule",
            valid=True,
            fit_source=c.fit_source.value,
            quality=c.quality,
            a_stage_m=c.a,
            b_stage_m=c.b,
            radius_m=c.radius,
        )
    if isinstance(c, ObbCollider):
        flat = tuple(float(x) for row in c.axes for x in row)
        return Collider(
            id=c.id,
            body_part=c.body_part.value,
            type="obb",
            valid=True,
            fit_source=c.fit_source.value,
            quality=c.quality,
            center_stage_m=c.center,
            axes_row_major=flat,
            half_extents_m=c.half_extents,
        )
    raise TypeError(f"unsupported collider {type(c).__name__}")


def from_transport(c: Collider) -> FittedCollider:
    """Inverse of :func:`to_transport` (used by tests and the geometry goldens)."""
    body_part = BodyPart(c.body_part)
    if not c.valid:
        return DisabledCollider(c.id, body_part, ColliderType(c.type), "transport_invalid")
    src = FitSource(c.fit_source)
    if c.type == "sphere":
        return make_sphere(c.id, body_part, c.center_stage_m, c.radius_m, src, c.quality)  # type: ignore[arg-type]
    if c.type == "capsule":
        return make_capsule(c.id, body_part, c.a_stage_m, c.b_stage_m, c.radius_m, src, c.quality)  # type: ignore[arg-type]
    if c.type == "obb":
        axes = np.asarray(c.axes_row_major, dtype=np.float64).reshape(3, 3)
        return make_obb(c.id, body_part, c.center_stage_m, axes, c.half_extents_m, src, c.quality)
    raise ValueError(f"unknown collider type {c.type!r}")

