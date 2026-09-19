"""Two-view ray triangulation in the stage frame.

For a landmark seen reliably in both RGB images, each pixel becomes a
calibrated optical ray expressed in stage coordinates, and the closest point
between the two rays is solved in least squares. The result is rejected when
the rays are near-parallel, the point lies behind either camera, the
reprojection residual is high, or it disagrees strongly with a well-supported
depth observation. Incompatible estimates are never blindly averaged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from hmc_backend.contracts.internal import CameraCalibration


@dataclass(frozen=True, slots=True)
class TriangulationConfig:
    min_ray_angle_deg: float = 4.0
    max_reprojection_px: float = 6.0
    max_depth_disagreement_m: float = 0.08
    min_depth_support_for_veto: int = 6


@dataclass(frozen=True, slots=True)
class Ray:
    origin: NDArray[np.float64]  # stage meters
    direction: NDArray[np.float64]  # unit, stage frame


@dataclass(frozen=True, slots=True)
class Triangulated:
    position_stage_m: tuple[float, float, float]
    ray_angle_deg: float
    reprojection_error_px: float  # max over the two views
    gap_m: float  # closest-approach distance between the rays


class TriangulationRejected(ValueError):
    """Carries the rejection reason for the debug report."""


def pixel_ray(xy_rgb: tuple[float, float], calibration: CameraCalibration) -> Ray:
    """Calibrated optical ray through an RGB pixel centre, in stage coordinates."""
    k = calibration.K_rgb
    x = (xy_rgb[0] - k[0, 2]) / k[0, 0]
    y = (xy_rgb[1] - k[1, 2]) / k[1, 1]
    d_opt = np.array([x, y, 1.0])
    d_opt /= np.linalg.norm(d_opt)
    t = calibration.T_stage_from_optical
    r = t[:3, :3]
    return Ray(origin=t[:3, 3].astype(np.float64), direction=(r @ d_opt).astype(np.float64))


def project_to_pixel(p_stage, calibration: CameraCalibration) -> tuple[float, float] | None:
    """Project a stage point into RGB pixels; ``None`` if behind the camera."""
    t = calibration.T_stage_from_optical
    r = t[:3, :3]
    p_opt = r.T @ (np.asarray(p_stage, dtype=np.float64) - t[:3, 3])
    if p_opt[2] <= 1e-6:
        return None
    k = calibration.K_rgb
    u = k[0, 0] * p_opt[0] / p_opt[2] + k[0, 2]
    v = k[1, 1] * p_opt[1] / p_opt[2] + k[1, 2]
    return float(u), float(v)


def closest_point_between_rays(a: Ray, b: Ray) -> tuple[NDArray[np.float64], float, float, float]:
    """Least-squares midpoint of the closest approach; returns (point, s, t, gap).

    ``s``/``t`` are the ray parameters (negative means behind that camera).
    """
    w0 = a.origin - b.origin
    da, db = a.direction, b.direction
    aa = float(np.dot(da, da))
    bb = float(np.dot(db, db))
    ab = float(np.dot(da, db))
    ad = float(np.dot(da, w0))
    bd = float(np.dot(db, w0))
    denom = aa * bb - ab * ab
    if denom < 1e-12:
        raise TriangulationRejected("parallel_rays")
    s = (ab * bd - bb * ad) / denom
    t = (aa * bd - ab * ad) / denom
    pa = a.origin + s * da
    pb = b.origin + t * db
    gap = float(np.linalg.norm(pa - pb))
    return (pa + pb) / 2.0, s, t, gap


def ray_angle_deg(a: Ray, b: Ray) -> float:
    c = float(np.clip(np.dot(a.direction, b.direction), -1.0, 1.0))
    return math.degrees(math.acos(c))


def triangulate(
    xy_a: tuple[float, float],
    calib_a: CameraCalibration,
    xy_b: tuple[float, float],
    calib_b: CameraCalibration,
    cfg: TriangulationConfig,
) -> Triangulated:
    """Triangulate one landmark from two views; raises :class:`TriangulationRejected`."""
    ra = pixel_ray(xy_a, calib_a)
    rb = pixel_ray(xy_b, calib_b)
    angle = ray_angle_deg(ra, rb)
    if angle < cfg.min_ray_angle_deg or angle > 180.0 - cfg.min_ray_angle_deg:
        raise TriangulationRejected(f"near_parallel_rays:{angle:.1f}deg")
    p, s, t, gap = closest_point_between_rays(ra, rb)
    if s <= 0.0 or t <= 0.0:
        raise TriangulationRejected("behind_camera")
    if not np.isfinite(p).all():
        raise TriangulationRejected("non_finite")

    errs = []
    for xy, calib in ((xy_a, calib_a), (xy_b, calib_b)):
        uv = project_to_pixel(p, calib)
        if uv is None:
            raise TriangulationRejected("behind_camera")
        errs.append(math.hypot(uv[0] - xy[0], uv[1] - xy[1]))
    err = max(errs)
    if err > cfg.max_reprojection_px:
        raise TriangulationRejected(f"reprojection:{err:.1f}px")

    return Triangulated(
        position_stage_m=(float(p[0]), float(p[1]), float(p[2])),
        ray_angle_deg=angle,
        reprojection_error_px=err,
        gap_m=gap,
    )


def compatible_with_depth(
    tri: Triangulated,
    depth_positions: list[tuple[tuple[float, float, float], int]],
    cfg: TriangulationConfig,
) -> bool:
    """False when a well-supported depth observation disagrees beyond the threshold."""
    p = np.asarray(tri.position_stage_m)
    for pos, support in depth_positions:
        if support < cfg.min_depth_support_for_veto:
            continue
        if float(np.linalg.norm(p - np.asarray(pos))) > cfg.max_depth_disagreement_m:
            return False
    return True
