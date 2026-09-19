"""Golden geometry cases shared with the Minecraft Java implementation.

:func:`golden_document` enumerates a fixed set of shapes, rays, and cubes and
records the Python reference results. The document is written to
``contracts/fixtures/collider_geometry_golden.json``; a test asserts the file
never drifts from this module, and the Java lane replays the same cases.

Values are rounded to :data:`ROUND_DIGITS` so cross-language float noise does
not create spurious diffs; consumers should compare with tolerance ``1e-6``.
"""

from __future__ import annotations

import math

import numpy as np

from hmc_backend.colliders import geometry as g
from hmc_backend.colliders.models import (
    FittedCollider,
    make_capsule,
    make_obb,
    make_sphere,
    to_transport,
)
from hmc_backend.contracts.enums import BodyPart, FitSource

ROUND_DIGITS = 9
SCHEMA = "hmc.collider_geometry_golden"
SCHEMA_VERSION = 1


def _rot_y(deg: float) -> np.ndarray:
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    # Rows are the local axes expressed in stage coordinates.
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def golden_shapes() -> dict[str, FittedCollider]:
    return {
        "head": make_sphere("head", BodyPart.HEAD, (0.0, 1.7, 0.0), 0.11, FitSource.OBSERVED, 0.9),
        "forearm": make_capsule(
            "arm.left.forearm",
            BodyPart.LEFT_FOREARM,
            (-0.2, 1.3, 0.0),
            (-0.5, 1.3, 0.0),
            0.05,
            FitSource.OBSERVED,
            0.8,
        ),
        "hand_axis_aligned": make_obb(
            "hand.left", BodyPart.LEFT_HAND, (-0.6, 1.3, 0.0), np.eye(3), (0.09, 0.02, 0.045), FitSource.OBSERVED, 0.8
        ),
        # A foot turned 40 degrees about +Y whose AABB overlaps a cube but whose OBB does not.
        "foot_turned": make_obb(
            "foot.right",
            BodyPart.RIGHT_FOOT,
            (0.0, 0.05, 0.0),
            _rot_y(40.0),
            (0.13, 0.04, 0.05),
            FitSource.OBSERVED,
            0.7,
        ),
    }


def _ray_cases() -> list[dict]:
    return [
        # sphere
        {"name": "sphere_direct", "shape": "head", "origin": [0.0, 1.7, 2.0], "direction": [0.0, 0.0, -1.0]},
        {"name": "sphere_miss", "shape": "head", "origin": [0.5, 1.7, 2.0], "direction": [0.0, 0.0, -1.0]},
        {"name": "sphere_tangent", "shape": "head", "origin": [0.11, 1.7, 2.0], "direction": [0.0, 0.0, -1.0]},
        {"name": "sphere_inside_origin", "shape": "head", "origin": [0.0, 1.7, 0.0], "direction": [1.0, 0.0, 0.0]},
        {"name": "sphere_behind", "shape": "head", "origin": [0.0, 1.7, 2.0], "direction": [0.0, 0.0, 1.0]},
        # capsule
        {"name": "capsule_cylinder_hit", "shape": "forearm", "origin": [-0.35, 1.3, 1.0], "direction": [0.0, 0.0, -1.0]},
        {"name": "capsule_cap_hit", "shape": "forearm", "origin": [-0.7, 1.3, 0.0], "direction": [1.0, 0.0, 0.0]},
        {"name": "capsule_miss_beyond_cap", "shape": "forearm", "origin": [-0.6, 1.3, 1.0], "direction": [0.0, 0.0, -1.0]},
        {"name": "capsule_parallel_inside", "shape": "forearm", "origin": [-0.3, 1.3, 0.0], "direction": [-1.0, 0.0, 0.0]},
        {"name": "capsule_oblique", "shape": "forearm", "origin": [-0.35, 1.6, 0.6], "direction": [0.0, -1.0, -2.0]},
        # obb
        {"name": "obb_direct", "shape": "hand_axis_aligned", "origin": [-0.6, 1.3, 1.0], "direction": [0.0, 0.0, -1.0]},
        {"name": "obb_edge_miss", "shape": "hand_axis_aligned", "origin": [-0.6, 1.33, 1.0], "direction": [0.0, 0.0, -1.0]},
        {"name": "obb_inside_origin", "shape": "hand_axis_aligned", "origin": [-0.6, 1.3, 0.0], "direction": [1.0, 0.0, 0.0]},
        {"name": "obb_turned_hit", "shape": "foot_turned", "origin": [0.0, 0.05, 1.0], "direction": [0.0, 0.0, -1.0]},
        {"name": "obb_turned_corner_miss", "shape": "foot_turned", "origin": [0.14, 0.05, 1.0], "direction": [0.0, 0.0, -1.0]},
        {"name": "obb_scaled_direction", "shape": "hand_axis_aligned", "origin": [-0.6, 1.3, 1.0], "direction": [0.0, 0.0, -3.0]},
    ]


def _cube_cases() -> list[dict]:
    return [
        {"name": "sphere_touching_face", "shape": "head", "box_min": [0.11, 1.5, -0.5], "box_max": [1.11, 2.5, 0.5]},
        {"name": "sphere_clear", "shape": "head", "box_min": [0.2, 1.5, -0.5], "box_max": [1.2, 2.5, 0.5]},
        {"name": "capsule_through_cube", "shape": "forearm", "box_min": [-0.4, 1.0, -0.5], "box_max": [-0.3, 2.0, 0.5]},
        {"name": "capsule_cap_touch", "shape": "forearm", "box_min": [-1.55, 0.8, -0.5], "box_max": [-0.55, 1.8, 0.5]},
        {"name": "capsule_clear", "shape": "forearm", "box_min": [-0.4, 1.4, -0.5], "box_max": [-0.3, 2.4, 0.5]},
        {"name": "obb_axis_aligned_overlap", "shape": "hand_axis_aligned", "box_min": [-0.55, 1.29, -0.5], "box_max": [0.45, 2.29, 0.5]},
        # Turned foot: its AABB reaches ~0.132 in X and ~0.122 in Z, so a cube whose
        # near corner is at (0.11, *, 0.11) overlaps the AABB but not the rotated box.
        {"name": "obb_turned_aabb_only", "shape": "foot_turned", "box_min": [0.11, 0.0, 0.11], "box_max": [1.11, 1.0, 1.11]},
        {"name": "obb_turned_real_overlap", "shape": "foot_turned", "box_min": [0.08, 0.0, -0.5], "box_max": [1.08, 1.0, 0.5]},
    ]


def _round(x: float | None) -> float | None:
    return None if x is None else round(float(x), ROUND_DIGITS)


def golden_document() -> dict:
    shapes = golden_shapes()
    shape_docs = {}
    for key, c in shapes.items():
        t = to_transport(c)
        shape_docs[key] = {
            "id": t.id,
            "type": t.type,
            "center_stage_m": t.center_stage_m,
            "radius_m": t.radius_m,
            "a_stage_m": t.a_stage_m,
            "b_stage_m": t.b_stage_m,
            "axes_row_major": None if t.axes_row_major is None else [_round(v) for v in t.axes_row_major],
            "half_extents_m": t.half_extents_m,
        }

    rays = []
    for case in _ray_cases():
        d = g.normalize(case["direction"])
        t = g.ray_collider(case["origin"], d, shapes[case["shape"]])
        rays.append(
            {
                **case,
                "direction_unit": [_round(v) for v in d],
                "expected_t": _round(t),
                "expected_hit": t is not None,
            }
        )

    cubes = []
    for case in _cube_cases():
        cubes.append(
            {**case, "expected_overlap": g.collider_overlaps_aabb(shapes[case["shape"]], case["box_min"], case["box_max"])}
        )

    nearest = g.nearest_hit([-0.35, 1.3, 2.0], [0.0, 0.0, -1.0], shapes.values())

    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "epsilon": g.EPS,
        "tolerance": 1e-6,
        "note": (
            "Reference results from backend/src/hmc_backend/colliders/geometry.py. "
            "Rays use the unit direction; expected_t is the nearest t >= 0 or null. "
            "OBB axes are row vectors; Java must enter the local frame with the transpose."
        ),
        "shapes": shape_docs,
        "rays": rays,
        "cubes": cubes,
        "nearest_hit": {
            "origin": [-0.35, 1.3, 2.0],
            "direction": [0.0, 0.0, -1.0],
            "expected_id": None if nearest is None else nearest[0],
            "expected_t": None if nearest is None else _round(nearest[1]),
        },
    }
