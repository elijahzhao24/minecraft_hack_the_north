"""Foot OBB from heel/foot-index landmarks, registered pose, and local surface.

Axes (rows): longitudinal (heel -> foot index), lateral, vertical.

The vertical seed comes from the registered pose (ankle above the heel-toe
midpoint). A foot is classified *planted* only when both heel and foot index
are near the floor and the sole is nearly parallel to it; only then is the
calibrated floor normal used as the vertical seed, so a lifted or turned foot
is never flattened onto the floor. When the surface clearly exposes a thin
direction it refines the vertical seed (sign-aligned with the pose seed).

Bounds include heel, foot index and, for a planted foot, the sole on the
floor; lateral/vertical extents come from surface points assigned to this
foot or, when unsupported, saved subject width/thickness (labelled). The shin
radius is never used for the foot.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from hmc_backend.colliders.fit_common import (
    FitConfig,
    FitOutcome,
    LandmarkMap,
    Segment,
    SubjectUpdate,
    landmark_conf,
    mad,
    orthonormal_frame,
    pos,
    project_extents,
    robust_bounds,
    source_quality,
)
from hmc_backend.colliders.geometry import point_segment_distance
from hmc_backend.colliders.models import SPEC_BY_ID, disabled, make_obb
from hmc_backend.colliders.subject import DimensionEstimate, SubjectDimensions
from hmc_backend.contracts.enums import FitSource
from hmc_backend.vision.model_mapping import Side, body_name

FLOOR_NORMAL_STAGE = np.array([0.0, 1.0, 0.0])
MIN_FOOT_LENGTH_M = 0.08
# Fallback vertical placement relative to the heel-toe line: the bones sit in
# the lower part of the foot volume, so most thickness lies above the line.
FALLBACK_BELOW_FRACTION = 0.25
# PCA refinement is trusted only when the thin direction is clearly thinner.
PCA_THIN_RATIO = 0.6


def classify_planted(
    heel: NDArray, toe: NDArray, cfg: FitConfig, floor_normal: NDArray = FLOOR_NORMAL_STAGE, floor_height_m: float = 0.0
) -> tuple[bool, dict]:
    n = floor_normal / np.linalg.norm(floor_normal)
    h_heel = float(np.dot(heel, n) - floor_height_m)
    h_toe = float(np.dot(toe, n) - floor_height_m)
    lo = toe - heel
    lo = lo / np.linalg.norm(lo)
    tilt = float(np.degrees(np.arcsin(np.clip(abs(np.dot(lo, n)), 0.0, 1.0))))
    planted = (
        max(h_heel, h_toe) <= cfg.foot_planted_max_height_m
        and min(h_heel, h_toe) >= -0.05
        and tilt <= cfg.foot_planted_max_tilt_deg
    )
    return planted, {"heel_height_m": h_heel, "toe_height_m": h_toe, "tilt_deg": tilt}


def _pca_thin_direction(local: NDArray) -> NDArray | None:
    """Smallest-variance direction of points in the (lateral, vertical) plane, if clearly thinner."""
    if local.shape[0] < 40:
        return None
    lv = local[:, 1:3] - local[:, 1:3].mean(axis=0)
    cov = lv.T @ lv / lv.shape[0]
    w, v = np.linalg.eigh(cov)
    if w[1] <= 0 or w[0] / w[1] > PCA_THIN_RATIO**2:
        return None
    return v[:, 0]  # (lateral, vertical) components of the thin axis


def fit_foot(
    side: Side,
    lms: LandmarkMap,
    xyz: NDArray[np.float32],
    assignment: NDArray[np.int64],
    segments: list[Segment],
    subject: SubjectDimensions,
    cfg: FitConfig,
    *,
    planted: bool | None = None,
    floor_normal: NDArray = FLOOR_NORMAL_STAGE,
    floor_height_m: float = 0.0,
) -> FitOutcome:
    """``planted`` overrides the automatic classification (e.g. an explicit calibration pose)."""
    cid = f"foot.{side}"
    body_part = SPEC_BY_ID[cid].body_part
    rep: dict = {}
    heel, toe, ankle = (pos(lms, body_name(f"{side}_{n}")) for n in ("heel", "foot_index", "ankle"))
    if heel is None or toe is None:
        rep["reason"] = "missing_heel_or_toe"
        return FitOutcome(disabled(cid, "missing_heel_or_toe"), report=rep)
    length = float(np.linalg.norm(toe - heel))
    if length < MIN_FOOT_LENGTH_M:
        rep["reason"] = "degenerate_length"
        return FitOutcome(disabled(cid, "degenerate_length"), report=rep)

    auto_planted, geo = classify_planted(heel, toe, cfg, floor_normal, floor_height_m)
    is_planted = auto_planted if planted is None else planted
    rep.update(geo, planted=is_planted)

    lo_dir = toe - heel
    if is_planted:
        vertical_seed = floor_normal / np.linalg.norm(floor_normal)
    elif ankle is not None:
        vertical_seed = ankle - (heel + toe) / 2.0
    else:
        rep["reason"] = "no_vertical_seed"
        return FitOutcome(disabled(cid, "no_vertical_seed"), report=rep)
    lateral_seed = np.cross(vertical_seed, lo_dir)
    axes = orthonormal_frame(lo_dir, lateral_seed)  # rows: longitudinal, lateral, longitudinal x lateral = vertical
    if axes is None:
        rep["reason"] = "degenerate_frame"
        return FitOutcome(disabled(cid, "degenerate_frame"), report=rep)

    seg_idx = next((i for i, s in enumerate(segments) if s.key == cid), -1)
    mine = xyz[assignment == seg_idx] if seg_idx >= 0 and xyz.shape[0] else np.zeros((0, 3), np.float32)
    if mine.shape[0]:
        d, _ = point_segment_distance(mine, heel, toe)
        mine = mine[d <= cfg.foot_search_radius_m]
    support = int(mine.shape[0])
    rep["support"] = support

    if support and not is_planted:
        local = project_extents(mine, heel, axes)
        thin = _pca_thin_direction(local)
        if thin is not None:
            refined = thin[0] * axes[1] + thin[1] * axes[2]
            if np.dot(refined, axes[2]) < 0:
                refined = -refined
            refined_axes = orthonormal_frame(lo_dir, np.cross(refined, lo_dir))
            if refined_axes is not None:
                axes = refined_axes
                rep["vertical_refined_by_surface"] = True

    # Longitudinal bounds always include heel and toe (padded); include sole for a planted foot.
    anchors = [heel, toe]
    if is_planted:
        n = floor_normal / np.linalg.norm(floor_normal)
        anchors.append(heel - n * (float(np.dot(heel, n)) - floor_height_m))
    a_local = project_extents(np.stack(anchors), heel, axes)
    lo, hi = a_local.min(axis=0), a_local.max(axis=0)

    conf = landmark_conf(lms, body_name(f"{side}_heel"), body_name(f"{side}_foot_index"))
    updates: list[SubjectUpdate] = []
    width_def = subject.as_subject_default("foot_width_m", side)
    thick_def = subject.as_subject_default("foot_thickness_m", side)
    if support >= cfg.obb_min_support:
        local = project_extents(mine, heel, axes)
        s_lo, s_hi = robust_bounds(local, cfg.obb_bounds_percentile)
        lo, hi = np.minimum(lo, s_lo), np.maximum(hi, s_hi)
        width, thick = float(hi[1] - lo[1]), float(hi[2] - lo[2])
        updates.append(("foot_width_m", side, DimensionEstimate(width, FitSource.OBSERVED, support, mad(local[:, 1], float(np.median(local[:, 1]))))))
        updates.append(("foot_thickness_m", side, DimensionEstimate(thick, FitSource.OBSERVED, support, mad(local[:, 2], float(np.median(local[:, 2]))))))
        source = FitSource.OBSERVED
        rep.update(width_m=width, thickness_m=thick)
    else:
        half_w = width_def.value_m / 2.0
        lo[1], hi[1] = min(lo[1], -half_w), max(hi[1], half_w)
        t = thick_def.value_m
        lo[2], hi[2] = min(lo[2], -t * FALLBACK_BELOW_FRACTION), max(hi[2], t * (1.0 - FALLBACK_BELOW_FRACTION))
        order = (FitSource.OBSERVED, FitSource.SUBJECT_DEFAULT, FitSource.GLOBAL_DEFAULT)
        source = max(width_def.source, thick_def.source, key=order.index)
        rep["reason"] = "insufficient_support"
    lo, hi = lo - cfg.obb_padding_m, hi + cfg.obb_padding_m
    rep["source"] = source.value

    half = np.minimum((hi - lo) / 2.0, np.asarray(cfg.max_foot_half_extent_m))
    centre = heel + axes.T @ ((lo + hi) / 2.0)
    quality = source_quality(source, 0.6 * conf + 0.4 * min(1.0, support / 200.0))
    return FitOutcome(make_obb(cid, body_part, centre, axes, half, source, quality), subject_updates=tuple(updates), report=rep)

