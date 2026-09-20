"""Pure validation of typed colliders before assembly.

Every fitted collider passes through :func:`validate_fitted`. A violation never
produces padded or repaired geometry: the collider becomes a
:class:`DisabledCollider` carrying the reason. :func:`finalize_collider_set`
then guarantees the stable required set (order of ``REQUIRED_COLLIDERS``),
filling anything the fitter did not produce with ``not_fitted``.

Rules (see docs/workflows/03-vision-and-colliders.md, "Collider validation"):

* Unique stable ID with matching ``body_part`` and geometry type.
* Finite coordinates, positive size, configured anatomical upper bounds.
* Capsule endpoints are not coincident.
* OBB axes have unit norm, near-zero pairwise dot products and |det| ~ 1.
* Centre / endpoints lie inside the configured stage crop.
* Quality below the per-part minimum disables the collider.

The transport-level checks in :mod:`hmc_backend.colliders.validate` still run
on the DTOs the assembler publishes; this module is the typed layer in front.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from hmc_backend.colliders.models import (
    REQUIRED_COLLIDERS,
    SPEC_BY_ID,
    CapsuleCollider,
    DisabledCollider,
    FittedCollider,
    ObbCollider,
    SphereCollider,
)
from hmc_backend.contracts.enums import BodyPart, ColliderType

_ORTHO_TOL = 1e-4
_DET_TOL = 1e-3


@dataclass(frozen=True, slots=True)
class StageBounds:
    min_m: tuple[float, float, float] = (-1.5, 0.0, -1.5)
    max_m: tuple[float, float, float] = (1.5, 2.5, 1.5)

    def contains(self, p) -> bool:
        return all(lo <= x <= hi for x, lo, hi in zip(p, self.min_m, self.max_m, strict=True))


def _default_min_quality() -> dict[BodyPart, float]:
    q = dict.fromkeys(BodyPart, 0.15)
    # Hands and feet are the accuracy-critical parts; weak fits are worse than none.
    for side in ("left", "right"):
        q[BodyPart(f"{side}_hand")] = 0.25
        q[BodyPart(f"{side}_foot")] = 0.25
    return q


@dataclass(frozen=True, slots=True)
class ValidationConfig:
    stage: StageBounds = StageBounds()
    max_sphere_radius_m: float = 0.16
    max_capsule_radius_m: float = 0.16
    min_capsule_length_m: float = 0.01
    # OBB half-extent caps keyed by body part family.
    max_hand_half_extent_m: tuple[float, float, float] = (0.14, 0.08, 0.04)
    max_foot_half_extent_m: tuple[float, float, float] = (0.18, 0.08, 0.08)
    max_torso_half_extent_m: tuple[float, float, float] = (0.35, 0.25, 0.20)
    min_quality: dict[BodyPart, float] = field(default_factory=_default_min_quality)

    def obb_cap(self, part: BodyPart) -> tuple[float, float, float]:
        if part.value.endswith("hand"):
            return self.max_hand_half_extent_m
        if part.value.endswith("foot"):
            return self.max_foot_half_extent_m
        return self.max_torso_half_extent_m


DEFAULT_VALIDATION = ValidationConfig()


def _finite3(v) -> bool:
    return v is not None and len(v) == 3 and all(math.isfinite(float(x)) for x in v)


def _check_common(c: SphereCollider | CapsuleCollider | ObbCollider, cfg: ValidationConfig) -> str | None:
    spec = SPEC_BY_ID.get(c.id)
    if spec is None:
        return "unknown_id"
    if spec.body_part is not c.body_part:
        return "body_part_mismatch"
    if spec.type is not c.type:
        return "type_mismatch"
    if c.quality is not None and not math.isfinite(c.quality):
        return "non_finite_quality"
    threshold = cfg.min_quality.get(c.body_part, 0.0)
    if c.quality is not None and c.quality < threshold:
        return "low_quality"
    return None


def _check_sphere(c: SphereCollider, cfg: ValidationConfig) -> str | None:
    if not _finite3(c.center):
        return "non_finite_center"
    if not math.isfinite(c.radius) or c.radius <= 0:
        return "non_positive_radius"
    if c.radius > cfg.max_sphere_radius_m:
        return "radius_exceeds_anatomy"
    if not cfg.stage.contains(c.center):
        return "outside_stage"
    return None


def _check_capsule(c: CapsuleCollider, cfg: ValidationConfig) -> str | None:
    if not (_finite3(c.a) and _finite3(c.b)):
        return "non_finite_endpoint"
    if not math.isfinite(c.radius) or c.radius <= 0:
        return "non_positive_radius"
    if c.radius > cfg.max_capsule_radius_m:
        return "radius_exceeds_anatomy"
    if float(np.linalg.norm(np.subtract(c.b, c.a))) < cfg.min_capsule_length_m:
        return "coincident_endpoints"
    if not (cfg.stage.contains(c.a) and cfg.stage.contains(c.b)):
        return "outside_stage"
    return None


def _check_obb(c: ObbCollider, cfg: ValidationConfig) -> str | None:
    if not _finite3(c.center):
        return "non_finite_center"
    if not _finite3(c.half_extents) or any(float(h) <= 0 for h in c.half_extents):
        return "non_positive_half_extent"
    cap = cfg.obb_cap(c.body_part)
    if any(float(h) > m for h, m in zip(c.half_extents, cap, strict=True)):
        return "half_extent_exceeds_anatomy"
    axes = np.asarray(c.axes, dtype=np.float64)
    if axes.shape != (3, 3) or not np.isfinite(axes).all():
        return "non_finite_axes"
    if not np.allclose(np.linalg.norm(axes, axis=1), 1.0, atol=_ORTHO_TOL):
        return "axes_not_unit"
    gram = axes @ axes.T
    if not np.allclose(gram - np.diag(np.diag(gram)), 0.0, atol=_ORTHO_TOL):
        return "axes_not_orthogonal"
    if abs(abs(float(np.linalg.det(axes))) - 1.0) > _DET_TOL:
        return "axes_determinant"
    if not cfg.stage.contains(c.center):
        return "outside_stage"
    return None


def validate_fitted(c: FittedCollider, cfg: ValidationConfig | None = None) -> FittedCollider:
    """Return ``c`` unchanged when valid, otherwise a :class:`DisabledCollider` with the reason."""
    cfg = cfg or DEFAULT_VALIDATION
    if isinstance(c, DisabledCollider):
        return c
    reason = _check_common(c, cfg)
    if reason is None:
        if isinstance(c, SphereCollider):
            reason = _check_sphere(c, cfg)
        elif isinstance(c, CapsuleCollider):
            reason = _check_capsule(c, cfg)
        elif isinstance(c, ObbCollider):
            reason = _check_obb(c, cfg)
        else:  # pragma: no cover - exhaustive over the union
            reason = "unknown_type"
    if reason is None:
        return c
    spec = SPEC_BY_ID.get(c.id)
    if spec is None:
        return DisabledCollider(c.id, c.body_part, c.type, reason)
    return DisabledCollider(spec.id, spec.body_part, spec.type, reason)


def finalize_collider_set(
    colliders: list[FittedCollider] | tuple[FittedCollider, ...],
    cfg: ValidationConfig | None = None,
) -> tuple[FittedCollider, ...]:
    """Validate, deduplicate and complete the required set in stable order.

    * Unknown IDs are dropped (they cannot be represented downstream).
    * A duplicated ID keeps the first occurrence and disables the ID with
      ``duplicate_id`` so the ambiguity is visible rather than silently resolved.
    * Every required ID is present exactly once; missing ones are ``not_fitted``.
    """
    cfg = cfg or DEFAULT_VALIDATION
    by_id: dict[str, FittedCollider] = {}
    duplicated: set[str] = set()
    for c in colliders:
        if c.id not in SPEC_BY_ID:
            continue
        if c.id in by_id:
            duplicated.add(c.id)
            continue
        by_id[c.id] = validate_fitted(c, cfg)
    out: list[FittedCollider] = []
    for spec in REQUIRED_COLLIDERS:
        if spec.id in duplicated:
            out.append(DisabledCollider(spec.id, spec.body_part, spec.type, "duplicate_id"))
        elif spec.id in by_id:
            out.append(by_id[spec.id])
        else:
            out.append(DisabledCollider(spec.id, spec.body_part, spec.type, "not_fitted"))
    return tuple(out)


def coverage_report(colliders: tuple[FittedCollider, ...]) -> dict:
    """Small summary for the debug JSON: which required parts are enabled and why not."""
    enabled = [c.id for c in colliders if not isinstance(c, DisabledCollider)]
    disabled = {c.id: c.reason for c in colliders if isinstance(c, DisabledCollider)}
    return {
        "required": len(REQUIRED_COLLIDERS),
        "enabled": len(enabled),
        "enabled_ids": enabled,
        "disabled": disabled,
        "types": {t.value: sum(1 for c in colliders if c.type is t and c.id in enabled) for t in ColliderType},
    }

