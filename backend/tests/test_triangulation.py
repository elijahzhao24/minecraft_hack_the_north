"""Triangulation recovers known points and rejects parallel rays / high residuals."""

from __future__ import annotations

import numpy as np
import pytest

from hmc_backend.calibration.synthetic import build_synthetic_rig
from hmc_backend.vision import triangulation as tri

CFG = tri.TriangulationConfig()


@pytest.fixture(scope="module")
def rig():
    return build_synthetic_rig(rgb_size=(640, 480), depth_size=(640, 480))


def test_pixel_ray_and_projection_are_inverse(rig):
    cam = rig.camera("front-phone")
    p = np.array([0.2, 1.3, -0.1])
    uv = tri.project_to_pixel(p, cam)
    ray = tri.pixel_ray(uv, cam)
    # The point lies on the ray.
    d = p - ray.origin
    d /= np.linalg.norm(d)
    assert np.allclose(d, ray.direction, atol=1e-9)
    assert tri.project_to_pixel(ray.origin - ray.direction, cam) is None  # behind


def test_triangulate_recovers_point_from_two_views(rig):
    a, b = rig.camera("front-phone"), rig.camera("side-phone")
    p = (0.15, 1.2, 0.05)
    uva, uvb = tri.project_to_pixel(p, a), tri.project_to_pixel(p, b)
    out = tri.triangulate(uva, a, uvb, b, CFG)
    assert out.position_stage_m == pytest.approx(p, abs=1e-6)
    assert out.reprojection_error_px < 1e-6
    assert 30 < out.ray_angle_deg < 50


def test_triangulate_rejects_parallel_rays(rig):
    a = rig.camera("front-phone")
    uv = (320.0, 240.0)
    with pytest.raises(tri.TriangulationRejected, match="parallel"):
        tri.triangulate(uv, a, uv, a, CFG)


def test_triangulate_rejects_high_residual(rig):
    a, b = rig.camera("front-phone"), rig.camera("side-phone")
    p = (0.15, 1.2, 0.05)
    uva, uvb = tri.project_to_pixel(p, a), tri.project_to_pixel(p, b)
    # Move off the epipolar line: the rays no longer meet.
    bad = (uvb[0] + 40.0, uvb[1])
    with pytest.raises(tri.TriangulationRejected, match="reprojection"):
        tri.triangulate(uva, a, bad, b, CFG)


def test_triangulate_rejects_points_behind_camera(rig):
    a, b = rig.camera("front-phone"), rig.camera("side-phone")
    # Rays that diverge (pointing away from each other) intersect "behind".
    ra = tri.pixel_ray((320.0, 240.0), a)
    flipped = tri.Ray(ra.origin, -ra.direction)
    rb = tri.pixel_ray((320.0, 240.0), b)
    _, s, _, _ = tri.closest_point_between_rays(flipped, rb)
    assert s < 0


def test_depth_veto_is_directional_and_support_gated(rig):
    cam = rig.camera("front-phone")  # on +Z looking toward -Z
    joint = (0.0, 1.1, 0.0)
    t = tri.Triangulated(joint, 40.0, 1.0, 0.001)
    z_joint = tri.optical_depth(joint, cam)
    surface_in_front = z_joint - 0.11  # e.g. the chest surface over a shoulder joint
    assert tri.compatible_with_depth(t, [(surface_in_front, 12, cam)], CFG)
    # Surface *behind* the triangulated point: the point would float in the air.
    assert not tri.compatible_with_depth(t, [(z_joint + 0.10, 12, cam)], CFG)
    # Way behind the surface: not an interior joint anymore.
    assert not tri.compatible_with_depth(t, [(z_joint - 0.40, 12, cam)], CFG)
    # Weakly supported samples never veto.
    assert tri.compatible_with_depth(t, [(z_joint + 0.10, 2, cam)], CFG)
