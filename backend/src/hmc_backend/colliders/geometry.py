"""Pure analytic geometry for the collider shapes.

These functions are the *reference* implementation the Minecraft Java lane must
match (see ``docs/workflows/04-minecraft-fabric.md`` "Geometry requirements").
They are deliberately dependency-light and scalar so the golden cases in
``contracts/fixtures/collider_geometry_golden.json`` can be regenerated and
compared value-for-value.

Conventions:

* Rays are ``origin + t * direction`` with ``direction`` normalized by the
  caller-facing helpers; results are the nearest ``t >= 0`` or ``None``.
* A single :data:`EPS` is used for tangency, parallel checks, and negative
  distances so both languages agree on borderline cases.
* OBB axes are three *row* unit vectors; the local frame is entered with the
  transpose (``axes @ (p - center)``).
* Cube overlap tests take an axis-aligned box ``(min_xyz, max_xyz)``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

import numpy as np
from numpy.typing import NDArray

from hmc_backend.colliders.models import (
    CapsuleCollider,
    DisabledCollider,
    FittedCollider,
    GeometryCollider,
    ObbCollider,
    SphereCollider,
)

EPS: float = 1e-9

Vec = NDArray[np.float64]


def _v(x) -> Vec:
    return np.asarray(x, dtype=np.float64).reshape(3)


def normalize(direction) -> Vec:
    d = _v(direction)
    n = float(np.linalg.norm(d))
    if not math.isfinite(n) or n < EPS:
        raise ValueError("ray direction must be a finite non-zero vector")
    return d / n


def _finite_t(t: float) -> float | None:
    if not math.isfinite(t) or t < -EPS:
        return None
    return max(t, 0.0)


# --- ray intersections -------------------------------------------------------


def ray_sphere(origin, direction, center, radius: float) -> float | None:
    """Nearest non-negative hit distance along a unit ray, or ``None``.

    Stable quadratic form: with ``m = o - c``, ``b = m.d``, ``c = m.m - r^2``.
    An origin inside the sphere returns the exit distance (``t >= 0``).
    Tangency (``disc`` within ``EPS`` of 0) counts as a hit.
    """
    o, d, c = _v(origin), _v(direction), _v(center)
    m = o - c
    b = float(np.dot(m, d))
    cc = float(np.dot(m, m) - radius * radius)
    if cc > 0.0 and b > 0.0:
        return None  # outside and pointing away
    disc = b * b - cc
    if disc < -EPS:
        return None
    disc = max(disc, 0.0)
    s = math.sqrt(disc)
    t = -b - s
    if t < -EPS:
        t = -b + s  # origin inside: take the exit point
    return _finite_t(t)


def _sphere_roots(o: Vec, d: Vec, c: Vec, radius: float) -> tuple[float, float] | None:
    m = o - c
    b = float(np.dot(m, d))
    cc = float(np.dot(m, m) - radius * radius)
    disc = b * b - cc
    if disc < -EPS:
        return None
    s = math.sqrt(max(disc, 0.0))
    return (-b - s, -b + s)


def ray_capsule(origin, direction, a, b, radius: float) -> float | None:
    """Ray vs finite swept sphere.

    The capsule surface is the union of the cylinder wall restricted to the
    segment's axial span and the two hemispherical caps restricted to *beyond*
    the segment ends. Every candidate root is checked against its own region
    so an interior surface (e.g. a cap sphere crossed while still inside the
    cylinder) is never reported. An origin inside returns the exit distance.
    """
    o, d = _v(origin), _v(direction)
    pa, pb = _v(a), _v(b)
    ab = pb - pa
    ab_len2 = float(np.dot(ab, ab))
    if ab_len2 < EPS * EPS:
        return ray_sphere(o, d, pa, radius)

    def axial(t: float) -> float:
        return float(np.dot((o + d * t) - pa, ab)) / ab_len2

    candidates: list[float] = []

    ao = o - pa
    ab_d = float(np.dot(ab, d))
    ab_ao = float(np.dot(ab, ao))
    d_perp = d - ab * (ab_d / ab_len2)
    ao_perp = ao - ab * (ab_ao / ab_len2)
    qa = float(np.dot(d_perp, d_perp))
    qb = 2.0 * float(np.dot(d_perp, ao_perp))
    qc = float(np.dot(ao_perp, ao_perp)) - radius * radius
    if qa > EPS:
        disc = qb * qb - 4.0 * qa * qc
        if disc >= -EPS:
            s = math.sqrt(max(disc, 0.0))
            for t in ((-qb - s) / (2.0 * qa), (-qb + s) / (2.0 * qa)):
                if t >= -EPS and -EPS <= axial(t) <= 1.0 + EPS:
                    candidates.append(max(t, 0.0))

    for cap, keep in ((pa, lambda u: u <= EPS), (pb, lambda u: u >= 1.0 - EPS)):
        roots = _sphere_roots(o, d, cap, radius)
        if roots is None:
            continue
        for t in roots:
            if t >= -EPS and keep(axial(t)):
                candidates.append(max(t, 0.0))

    if not candidates:
        return None
    return min(candidates)


def ray_obb(origin, direction, center, axes, half_extents) -> float | None:
    """Slab test in the OBB's local frame (``axes`` rows are the unit local axes)."""
    o, d, c = _v(origin), _v(direction), _v(center)
    r = np.asarray(axes, dtype=np.float64).reshape(3, 3)
    h = _v(half_extents)
    lo_local = r @ (o - c)
    ld = r @ d
    t_min, t_max = -math.inf, math.inf
    for i in range(3):
        if abs(ld[i]) < EPS:
            if lo_local[i] < -h[i] - EPS or lo_local[i] > h[i] + EPS:
                return None
            continue
        inv = 1.0 / ld[i]
        t1 = (-h[i] - lo_local[i]) * inv
        t2 = (h[i] - lo_local[i]) * inv
        if t1 > t2:
            t1, t2 = t2, t1
        t_min = max(t_min, t1)
        t_max = min(t_max, t2)
        if t_min > t_max + EPS:
            return None
    if t_max < -EPS:
        return None  # box entirely behind the origin
    if t_min < -EPS:
        return _finite_t(t_max)  # origin inside: exit distance, like the sphere
    return _finite_t(t_min)


def ray_collider(origin, direction, collider: FittedCollider) -> float | None:
    """Dispatch on the typed collider. Disabled colliders never hit."""
    if isinstance(collider, DisabledCollider):
        return None
    d = normalize(direction)
    if isinstance(collider, SphereCollider):
        return ray_sphere(origin, d, collider.center, collider.radius)
    if isinstance(collider, CapsuleCollider):
        return ray_capsule(origin, d, collider.a, collider.b, collider.radius)
    if isinstance(collider, ObbCollider):
        return ray_obb(origin, d, collider.center, collider.axes_array(), collider.half_extents)
    raise TypeError(type(collider).__name__)


def nearest_hit(
    origin, direction, colliders: Iterable[FittedCollider], *, max_distance: float = math.inf
) -> tuple[str, float] | None:
    """Nearest ``t`` within reach; equal distances (within EPS) tie-break by collider id."""
    best: tuple[str, float] | None = None
    for c in colliders:
        t = ray_collider(origin, direction, c)
        if t is None or t > max_distance:
            continue
        if best is None or t < best[1] - EPS or (abs(t - best[1]) <= EPS and c.id < best[0]):
            best = (c.id, t)
    return best


# --- axis-aligned cube overlap ----------------------------------------------


def closest_point_on_aabb(p, box_min, box_max) -> Vec:
    return np.minimum(np.maximum(_v(p), _v(box_min)), _v(box_max))


def sphere_overlaps_aabb(center, radius: float, box_min, box_max) -> bool:
    q = closest_point_on_aabb(center, box_min, box_max)
    d2 = float(np.dot(q - _v(center), q - _v(center)))
    return d2 <= radius * radius + EPS


def _segment_aabb_distance2(a, b, box_min, box_max, *, iterations: int = 40) -> float:
    """Squared distance from a finite segment to an AABB.

    The distance along the segment is convex, so a ternary search over ``t``
    with the exact point-box distance converges robustly without special cases.
    """
    pa, pb = _v(a), _v(b)
    lo_b, hi_b = _v(box_min), _v(box_max)

    def f(t: float) -> float:
        p = pa + (pb - pa) * t
        q = np.minimum(np.maximum(p, lo_b), hi_b)
        return float(np.dot(q - p, q - p))

    lo, hi = 0.0, 1.0
    for _ in range(iterations):
        m1 = lo + (hi - lo) / 3.0
        m2 = hi - (hi - lo) / 3.0
        if f(m1) <= f(m2):
            hi = m2
        else:
            lo = m1
    return min(f(lo), f(hi), f(0.0), f(1.0))


def capsule_overlaps_aabb(a, b, radius: float, box_min, box_max) -> bool:
    return _segment_aabb_distance2(a, b, box_min, box_max) <= radius * radius + EPS


def obb_overlaps_aabb(center, axes, half_extents, box_min, box_max) -> bool:
    """Separating-axis theorem between an OBB and an AABB (15 candidate axes)."""
    c_o = _v(center)
    r_o = np.asarray(axes, dtype=np.float64).reshape(3, 3)  # rows = obb axes
    h_o = _v(half_extents)
    lo_b, hi_b = _v(box_min), _v(box_max)
    c_b = (lo_b + hi_b) / 2.0
    h_b = (hi_b - lo_b) / 2.0
    r_b = np.eye(3)

    t = c_o - c_b
    axes_to_test: list[Vec] = [r_b[i] for i in range(3)] + [r_o[i] for i in range(3)]
    for i in range(3):
        for j in range(3):
            cr = np.cross(r_b[i], r_o[j])
            n = float(np.linalg.norm(cr))
            if n > EPS:
                axes_to_test.append(cr / n)

    for ax in axes_to_test:
        proj_b = float(np.sum(h_b * np.abs(r_b @ ax)))
        proj_o = float(np.sum(h_o * np.abs(r_o @ ax)))
        dist = abs(float(np.dot(t, ax)))
        if dist > proj_b + proj_o + EPS:
            return False
    return True


def collider_overlaps_aabb(collider: FittedCollider, box_min, box_max) -> bool:
    if isinstance(collider, DisabledCollider):
        return False
    if isinstance(collider, SphereCollider):
        return sphere_overlaps_aabb(collider.center, collider.radius, box_min, box_max)
    if isinstance(collider, CapsuleCollider):
        return capsule_overlaps_aabb(collider.a, collider.b, collider.radius, box_min, box_max)
    if isinstance(collider, ObbCollider):
        return obb_overlaps_aabb(
            collider.center, collider.axes_array(), collider.half_extents, box_min, box_max
        )
    raise TypeError(type(collider).__name__)


# --- misc helpers used by fitting -------------------------------------------


def collider_aabb(collider: GeometryCollider) -> tuple[Vec, Vec]:
    """Conservative axis-aligned bounds (broad phase / debug drawing)."""
    if isinstance(collider, SphereCollider):
        c = _v(collider.center)
        return c - collider.radius, c + collider.radius
    if isinstance(collider, CapsuleCollider):
        a, b = _v(collider.a), _v(collider.b)
        return np.minimum(a, b) - collider.radius, np.maximum(a, b) + collider.radius
    if isinstance(collider, ObbCollider):
        c = _v(collider.center)
        ext = np.abs(collider.axes_array()).T @ _v(collider.half_extents)
        return c - ext, c + ext
    raise TypeError(type(collider).__name__)


def point_segment_distance(points, a, b) -> tuple[Vec, Vec]:
    """Radial distance and normalized axial coordinate of ``points`` (N x 3) to segment ab."""
    p = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    pa, pb = _v(a), _v(b)
    ab = pb - pa
    len2 = float(np.dot(ab, ab))
    if len2 < EPS * EPS:
        d = np.linalg.norm(p - pa, axis=1)
        return d, np.zeros(len(p))
    u = ((p - pa) @ ab) / len2
    u_clamped = np.clip(u, 0.0, 1.0)
    closest = pa + np.outer(u_clamped, ab)
    return np.linalg.norm(p - closest, axis=1), u
