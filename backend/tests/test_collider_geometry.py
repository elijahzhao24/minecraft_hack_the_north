"""Ray hits, cube overlap, and the cross-language golden document."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from hmc_backend.colliders import geometry as g
from hmc_backend.colliders.golden import golden_document, golden_shapes
from hmc_backend.colliders.models import disabled

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN = REPO_ROOT / "contracts" / "fixtures" / "collider_geometry_golden.json"


# --- sphere ------------------------------------------------------------------


def test_sphere_direct_tangent_miss_inside():
    c, r = (0.0, 1.7, 0.0), 0.11
    assert g.ray_sphere((0, 1.7, 2), (0, 0, -1), c, r) == pytest.approx(2.0 - 0.11)
    assert g.ray_sphere((0.11, 1.7, 2), (0, 0, -1), c, r) == pytest.approx(2.0)  # tangent
    assert g.ray_sphere((0.5, 1.7, 2), (0, 0, -1), c, r) is None
    assert g.ray_sphere((0, 1.7, 0), (1, 0, 0), c, r) == pytest.approx(0.11)  # inside -> exit
    assert g.ray_sphere((0, 1.7, 2), (0, 0, 1), c, r) is None  # behind


# --- capsule -----------------------------------------------------------------


def test_capsule_cylinder_and_caps():
    a, b, r = (-0.2, 1.3, 0.0), (-0.5, 1.3, 0.0), 0.05
    assert g.ray_capsule((-0.35, 1.3, 1.0), (0, 0, -1), a, b, r) == pytest.approx(0.95)
    # Hitting the end cap along the axis from beyond b.
    assert g.ray_capsule((-0.7, 1.3, 0.0), (1, 0, 0), a, b, r) == pytest.approx(0.15)
    # A ray past the cap misses (segment is finite; no lengthening).
    assert g.ray_capsule((-0.6, 1.3, 1.0), (0, 0, -1), a, b, r) is None
    # Parallel ray starting inside the cylinder exits through the far cap.
    t = g.ray_capsule((-0.3, 1.3, 0.0), (-1, 0, 0), a, b, r)
    assert t == pytest.approx(0.25)


def test_capsule_degenerate_segment_is_sphere():
    assert g.ray_capsule((0, 0, 2), (0, 0, -1), (0, 0, 0), (0, 0, 0), 0.5) == pytest.approx(1.5)


# --- obb ---------------------------------------------------------------------


def test_obb_slab_hits_and_local_frame_transpose():
    # A box rotated 90 deg about Y: local +X is stage +Z.
    axes = np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0]], float)
    half = (0.3, 0.1, 0.05)  # long along stage Z now, thin along stage X
    t = g.ray_obb((1.0, 0.0, 0.0), (-1, 0, 0), (0, 0, 0), axes, half)
    assert t == pytest.approx(1.0 - 0.05)
    t = g.ray_obb((0.0, 0.0, 1.0), (0, 0, -1), (0, 0, 0), axes, half)
    assert t == pytest.approx(1.0 - 0.3)
    assert g.ray_obb((0.0, 0.2, 1.0), (0, 0, -1), (0, 0, 0), axes, half) is None
    # Origin inside -> exit distance along stage +X (local -Z, half extent 0.05).
    assert g.ray_obb((0, 0, 0), (1, 0, 0), (0, 0, 0), axes, half) == pytest.approx(0.05)
    assert g.ray_obb((0, 0, 2), (0, 0, 1), (0, 0, 0), axes, half) is None  # behind


def test_disabled_never_hits_and_nearest_tiebreak_by_id():
    shapes = golden_shapes()
    assert g.ray_collider((0, 0, 5), (0, 0, -1), disabled("head", "x")) is None
    hit = g.nearest_hit((-0.35, 1.3, 2.0), (0, 0, -1), shapes.values())
    assert hit == ("arm.left.forearm", pytest.approx(1.95))
    # Equal distance -> smaller id wins deterministically.
    a = shapes["head"]
    b = a.__class__("aaa", a.body_part, a.center, a.radius, a.fit_source, a.quality)
    assert g.nearest_hit((0, 1.7, 2), (0, 0, -1), [a, b])[0] == "aaa"
    assert g.nearest_hit((0, 1.7, 2), (0, 0, -1), [a], max_distance=1.0) is None


def test_ray_direction_must_be_finite_nonzero():
    with pytest.raises(ValueError):
        g.normalize((0, 0, 0))
    with pytest.raises(ValueError):
        g.normalize((math.nan, 0, 0))


# --- cube overlap ------------------------------------------------------------


def test_turned_foot_aabb_overlaps_but_obb_does_not():
    foot = golden_shapes()["foot_turned"]
    lo, hi = g.collider_aabb(foot)
    box_min, box_max = (0.11, 0.0, 0.11), (1.11, 1.0, 1.11)
    aabb_overlap = bool(np.all(hi >= box_min) and np.all(lo <= box_max))
    assert aabb_overlap
    assert g.collider_overlaps_aabb(foot, box_min, box_max) is False
    assert g.collider_overlaps_aabb(foot, (0.08, 0, -0.5), (1.08, 1, 0.5)) is True


def test_capsule_vs_cube_distance_is_exact_not_enclosing():
    a, b, r = (-0.2, 1.3, 0.0), (-0.5, 1.3, 0.0), 0.05
    assert g.capsule_overlaps_aabb(a, b, r, (-0.4, 1.0, -0.5), (-0.3, 2.0, 0.5))
    # Cube exactly r above the axis -> touching.
    assert g.capsule_overlaps_aabb(a, b, r, (-0.4, 1.35, -0.5), (-0.3, 2.35, 0.5))
    assert not g.capsule_overlaps_aabb(a, b, r, (-0.4, 1.4, -0.5), (-0.3, 2.4, 0.5))
    assert not g.collider_overlaps_aabb(disabled("hand.left", "x"), (-1, -1, -1), (1, 1, 1))


def test_point_segment_distance():
    d, u = g.point_segment_distance([[0.5, 1.0, 0.0], [2.0, 0.0, 0.0]], (0, 0, 0), (1, 0, 0))
    assert d.tolist() == pytest.approx([1.0, 1.0])
    assert u.tolist() == pytest.approx([0.5, 2.0])


# --- golden document ---------------------------------------------------------


def test_golden_file_matches_python_reference():
    assert GOLDEN.exists(), "run scripts/write_collider_golden.py"
    assert json.loads(GOLDEN.read_text()) == json.loads(json.dumps(golden_document()))


def test_golden_document_covers_required_case_kinds():
    doc = golden_document()
    names = {r["name"] for r in doc["rays"]}
    assert {"sphere_tangent", "sphere_inside_origin", "capsule_cap_hit", "obb_turned_corner_miss"} <= names
    hit = {r["name"]: r["expected_hit"] for r in doc["rays"]}
    assert hit["sphere_tangent"] and not hit["sphere_miss"] and not hit["sphere_behind"]
    assert hit["obb_turned_hit"] and not hit["obb_turned_corner_miss"]
    cubes = {c["name"]: c["expected_overlap"] for c in doc["cubes"]}
    assert cubes["obb_turned_aabb_only"] is False and cubes["obb_turned_real_overlap"] is True
    assert cubes["sphere_touching_face"] is True and cubes["sphere_clear"] is False

