"""Shared helpers for collider fitting.

All fitters consume the fused :class:`Landmark3D` set (by canonical name) and
the merged stage-frame point cloud, and return a typed collider plus a report
dict for the debug JSON. Nothing here pads geometry to hide missing data.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from hmc_backend.colliders.geometry import point_segment_distance
from hmc_backend.colliders.models import FittedCollider
from hmc_backend.colliders.subject import DimensionEstimate, Side
from hmc_backend.contracts.enums import FitSource
from hmc_backend.contracts.internal import Landmark3D

Vec = NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class FitConfig:
    # Capsules
    capsule_radius_percentile: float = 85.0
    capsule_margin_m: float = 0.004
    capsule_axial_trim: float = 0.12  # drop points within this fraction of either end
    capsule_min_support: int = 25
    capsule_min_radius_m: float = 0.02
    capsule_search_radius_m: float = 0.16
    # OBBs
    obb_padding_m: float = 0.005
    obb_bounds_percentile: float = 2.0  # robust min/max at (p, 100-p)
    obb_min_support: int = 20
    hand_search_radius_m: float = 0.14
    hand_min_wrist_mcp_m: float = 0.04
    hand_min_mcp_span_m: float = 0.03
    hand_min_finger_extent_m: float = 0.10
    foot_search_radius_m: float = 0.16
    foot_planted_max_height_m: float = 0.09
    foot_planted_max_tilt_deg: float = 25.0
    # Head / torso
    head_search_radius_m: float = 0.20
    head_radius_percentile: float = 80.0
    head_min_support: int = 60
    torso_min_support: int = 150
    # Subject-dimension policy
    subject_min_samples: int = 30
    # Anatomical caps (meters)
    max_limb_radius_m: float = 0.16
    max_head_radius_m: float = 0.16
    max_hand_half_extent_m: tuple[float, float, float] = (0.14, 0.08, 0.04)
    max_foot_half_extent_m: tuple[float, float, float] = (0.18, 0.08, 0.08)
    max_torso_half_extent_m: tuple[float, float, float] = (0.35, 0.25, 0.20)


SubjectUpdate = tuple[str, Side | None, DimensionEstimate]  # (field, side, estimate)


@dataclass(slots=True)
class FitOutcome:
    collider: FittedCollider
    subject_updates: tuple[SubjectUpdate, ...] = ()
    report: dict = field(default_factory=dict)


LandmarkMap = dict[str, Landmark3D]


def by_name(landmarks: tuple[Landmark3D, ...]) -> LandmarkMap:
    return {lm.name: lm for lm in landmarks}


def pos(lms: LandmarkMap, name: str) -> Vec | None:
    lm = lms.get(name)
    if lm is None or not lm.valid or lm.position_stage_m is None:
        return None
    return np.asarray(lm.position_stage_m, dtype=np.float64)


def landmark_conf(lms: LandmarkMap, *names: str) -> float:
    vals = [c for n in names if n in lms and lms[n].valid and (c := lms[n].confidence) is not None]
    return float(min(vals)) if vals else 0.0


# --- point selection ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Segment:
    """A body segment used for nearest-segment point assignment."""

    key: str
    a: Vec
    b: Vec
    expected_radius_m: float


def assign_points_to_segments(xyz: NDArray, segments: list[Segment]) -> NDArray[np.int64]:
    """Index of the nearest segment for every point, measured *outside* its expected radius.

    Using ``distance - expected_radius`` lets a wide torso claim points that are
    geometrically closer to a thin arm's axis but clearly on the torso surface.
    Returns -1 when the point is farther than 0.25 m from every segment.
    """
    n = xyz.shape[0]
    if n == 0 or not segments:
        return np.full(n, -1, dtype=np.int64)
    dist = np.empty((n, len(segments)))
    for i, s in enumerate(segments):
        dist[:, i], _ = point_segment_distance(xyz, s.a, s.b)
    radii = np.array([s.expected_radius_m for s in segments])
    best = np.argmin(dist - radii[None, :], axis=1)
    best[dist[np.arange(n), best] > 0.25] = -1
    return best


def robust_percentile(values: NDArray, p: float) -> float:
    return float(np.percentile(values, p)) if values.size else float("nan")


def mad(values: NDArray, centre: float) -> float:
    return float(np.median(np.abs(values - centre))) if values.size else float("nan")


# --- orthonormal frames ------------------------------------------------------


def orthonormal_frame(longitudinal, transverse_hint) -> NDArray[np.float64] | None:
    """Rows: unit longitudinal, transverse (hint minus its longitudinal part), cross.

    Returns ``None`` when the seeds are degenerate or the result is not a
    proper right-handed orthonormal basis.
    """
    lo = np.asarray(longitudinal, dtype=np.float64)
    n = np.linalg.norm(lo)
    if not np.isfinite(n) or n < 1e-6:
        return None
    lo = lo / n
    tr = np.asarray(transverse_hint, dtype=np.float64)
    tr = tr - lo * np.dot(tr, lo)
    m = np.linalg.norm(tr)
    if not np.isfinite(m) or m < 1e-6:
        return None
    tr = tr / m
    nz = np.cross(lo, tr)
    axes = np.stack([lo, tr, nz])
    # Re-orthogonalize via SVD polar decomposition and validate.
    u, _, vt = np.linalg.svd(axes)
    axes = u @ vt
    if not np.allclose(axes @ axes.T, np.eye(3), atol=1e-8) or abs(abs(np.linalg.det(axes)) - 1.0) > 1e-6:
        return None
    if np.linalg.det(axes) < 0:
        axes[2] = -axes[2]
    return axes


def project_extents(points: NDArray, origin: Vec, axes: NDArray) -> NDArray:
    """Local coordinates (N x 3) of ``points`` in the frame with row ``axes``."""
    return (np.asarray(points, dtype=np.float64) - origin) @ axes.T


def robust_bounds(local: NDArray, percentile: float) -> tuple[Vec, Vec]:
    lo = np.percentile(local, percentile, axis=0)
    hi = np.percentile(local, 100.0 - percentile, axis=0)
    return lo, hi


def source_quality(source: FitSource, base: float) -> float:
    if source is FitSource.OBSERVED:
        return float(min(1.0, base))
    if source is FitSource.SUBJECT_DEFAULT:
        return float(min(0.6, base))
    return float(min(0.35, base))
