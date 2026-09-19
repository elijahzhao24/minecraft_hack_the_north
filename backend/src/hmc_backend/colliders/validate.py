"""Validation of fitted collider geometry before publication.

The assembler rejects a frame whose colliders violate these rules rather than
publishing fake or degenerate geometry. Invalid colliders are allowed but must
carry no geometry (identity fields only).
"""

from __future__ import annotations

import math

import numpy as np

from hmc_backend.contracts.internal import Collider

MAX_SIZE_M = 1.0
_ORTHO_TOL = 1e-4
_DET_TOL = 1e-3


class ColliderValidationError(ValueError):
    """Raised when a collider's geometry is invalid."""


def _finite_vec3(v, label: str) -> None:
    if v is None or len(v) != 3 or not all(math.isfinite(x) for x in v):
        raise ColliderValidationError(f"{label} must be a finite 3-vector")


def _positive_size(x, label: str) -> None:
    if x is None or not math.isfinite(x) or x <= 0 or x > MAX_SIZE_M:
        raise ColliderValidationError(f"{label} must be in (0, {MAX_SIZE_M}]")


def validate_collider(c: Collider) -> None:
    """Validate one collider; raises on any geometry violation."""
    if not c.valid:
        # Invalid colliders must not carry geometry.
        if any(
            g is not None
            for g in (c.center_stage_m, c.radius_m, c.a_stage_m, c.b_stage_m, c.axes_row_major, c.half_extents_m)
        ):
            raise ColliderValidationError(f"invalid collider {c.id!r} must omit geometry")
        return

    if c.type == "sphere":
        _finite_vec3(c.center_stage_m, f"{c.id} center")
        _positive_size(c.radius_m, f"{c.id} radius")
    elif c.type == "capsule":
        _finite_vec3(c.a_stage_m, f"{c.id} a")
        _finite_vec3(c.b_stage_m, f"{c.id} b")
        _positive_size(c.radius_m, f"{c.id} radius")
    elif c.type == "obb":
        _finite_vec3(c.center_stage_m, f"{c.id} center")
        _validate_obb_axes(c)
        if c.half_extents_m is None or len(c.half_extents_m) != 3:
            raise ColliderValidationError(f"{c.id} half_extents must be a 3-vector")
        for i, e in enumerate(c.half_extents_m):
            _positive_size(e, f"{c.id} half_extent[{i}]")
    else:
        raise ColliderValidationError(f"{c.id} unknown collider type {c.type!r}")


def _validate_obb_axes(c: Collider) -> None:
    if c.axes_row_major is None or len(c.axes_row_major) != 9:
        raise ColliderValidationError(f"{c.id} axes must have 9 elements")
    axes = np.array(c.axes_row_major, dtype=np.float64)
    if not np.isfinite(axes).all():
        raise ColliderValidationError(f"{c.id} axes contain non-finite values")
    m = axes.reshape(3, 3)
    # Rows are the three unit axes; must be mutually orthonormal.
    gram = m @ m.T
    if not np.allclose(gram, np.eye(3), atol=_ORTHO_TOL):
        raise ColliderValidationError(f"{c.id} OBB axes are not orthonormal")
    if abs(abs(np.linalg.det(m)) - 1.0) > _DET_TOL:
        raise ColliderValidationError(f"{c.id} OBB axes determinant magnitude != 1")


def validate_colliders(colliders: tuple[Collider, ...]) -> None:
    """Validate a whole collider set: unique IDs and per-collider geometry."""
    seen: set[str] = set()
    for c in colliders:
        if c.id in seen:
            raise ColliderValidationError(f"duplicate collider id {c.id!r}")
        seen.add(c.id)
        validate_collider(c)
