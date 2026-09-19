"""Whole-hand OBB from registered hand landmarks and nearby masked surface.

Axes (rows): longitudinal (wrist -> middle MCP), transverse (pinky MCP ->
index MCP with the longitudinal component removed), and their cross product,
re-orthogonalized with a validated determinant. Bounds are robust min/max of
the valid hand landmarks plus the surface points assigned to this hand,
with a small documented padding (``FitConfig.obb_padding_m``, 5 mm).

Gates:

* Non-degenerate wrist/MCP geometry is required for orientation. Without it
  the hand is disabled: there is no honest way to orient a box.
* Enough observed finger extent is required for an ``observed`` box. With
  orientation but too little finger extent, the box uses subject dimensions
  (labelled ``subject_default``/``global_default``) and a palm-proportional
  length.
* Thickness cannot be measured from a flat landmark set; it comes from the
  surface when supported, otherwise from subject dimensions, and the box's
  ``fit_source`` reflects the weakest component.
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
from hmc_backend.vision.model_mapping import HAND_LANDMARK_NAMES, Side, body_name, hand_name

# Palm length is roughly half of the wrist->fingertip hand length; used only
# when finger extent is unobserved but orientation is.
PALM_TO_HAND_LENGTH = 2.0


def _weakest(*sources: FitSource) -> FitSource:
    order = (FitSource.OBSERVED, FitSource.SUBJECT_DEFAULT, FitSource.GLOBAL_DEFAULT)
    return max(sources, key=order.index)


def hand_axes(side: Side, lms: LandmarkMap, cfg: FitConfig) -> tuple[NDArray, str] | tuple[None, str]:
    """Row axes for the hand box, or ``(None, reason)`` when orientation is unobserved."""
    wrist, mcp = pos(lms, hand_name(side, "wrist")), pos(lms, hand_name(side, "middle_mcp"))
    if wrist is None:
        wrist = pos(lms, body_name(f"{side}_wrist"))
    if wrist is None or mcp is None:
        return None, "missing_wrist_or_mcp"
    index, pinky = pos(lms, hand_name(side, "index_mcp")), pos(lms, hand_name(side, "pinky_mcp"))
    if index is None:
        index = pos(lms, body_name(f"{side}_index"))
    if pinky is None:
        pinky = pos(lms, body_name(f"{side}_pinky"))
    if index is None or pinky is None:
        return None, "missing_mcp_span"
    if np.linalg.norm(mcp - wrist) < cfg.hand_min_wrist_mcp_m:
        return None, "degenerate_wrist_mcp"
    if np.linalg.norm(index - pinky) < cfg.hand_min_mcp_span_m:
        return None, "degenerate_mcp_span"
    axes = orthonormal_frame(mcp - wrist, index - pinky)
    if axes is None:
        return None, "degenerate_frame"
    return axes, "observed"


def fit_hand(
    side: Side,
    lms: LandmarkMap,
    xyz: NDArray[np.float32],
    assignment: NDArray[np.int64],
    segments: list[Segment],
    subject: SubjectDimensions,
    cfg: FitConfig,
) -> FitOutcome:
    cid = f"hand.{side}"
    body_part = SPEC_BY_ID[cid].body_part
    rep: dict = {}
    axes, reason = hand_axes(side, lms, cfg)
    if axes is None:
        rep["reason"] = reason
        return FitOutcome(disabled(cid, reason), report=rep)
    wrist = pos(lms, hand_name(side, "wrist"))
    if wrist is None:
        wrist = pos(lms, body_name(f"{side}_wrist"))
    assert wrist is not None  # guaranteed by hand_axes

    # Landmarks in the local frame.
    lm_pts = [p for n in HAND_LANDMARK_NAMES if (p := pos(lms, hand_name(side, n))) is not None]
    lm_local = project_extents(np.stack(lm_pts), wrist, axes) if lm_pts else np.zeros((0, 3))
    finger_extent = float(lm_local[:, 0].max()) if lm_local.shape[0] else 0.0
    rep["finger_extent_m"] = finger_extent
    rep["landmarks"] = int(lm_local.shape[0])

    # Surface points assigned to this hand near the wrist->mcp axis.
    seg_idx = next((i for i, s in enumerate(segments) if s.key == cid), -1)
    mine = xyz[assignment == seg_idx] if seg_idx >= 0 and xyz.shape[0] else np.zeros((0, 3), np.float32)
    if mine.shape[0]:
        mcp = pos(lms, hand_name(side, "middle_mcp"))
        assert mcp is not None
        far = wrist + (mcp - wrist) * PALM_TO_HAND_LENGTH
        d, _ = point_segment_distance(mine, wrist, far)
        mine = mine[d <= cfg.hand_search_radius_m]
    surf_local = project_extents(mine, wrist, axes) if mine.shape[0] else np.zeros((0, 3))
    # The hand starts at the wrist: forearm end-cap points behind it are not hand surface.
    surf_local = surf_local[surf_local[:, 0] >= -cfg.obb_padding_m]
    support = int(surf_local.shape[0])
    rep["support"] = support

    conf = landmark_conf(lms, hand_name(side, "wrist"), hand_name(side, "middle_mcp"))
    width_def = subject.as_subject_default("hand_width_m", side)
    thick_def = subject.as_subject_default("hand_thickness_m", side)
    updates: list[SubjectUpdate] = []

    if finger_extent < cfg.hand_min_finger_extent_m and support < cfg.obb_min_support:
        # Orientation observed, extent not: subject dimensions and palm-proportional length.
        mcp = pos(lms, hand_name(side, "middle_mcp"))
        assert mcp is not None
        length = float(np.linalg.norm(mcp - wrist)) * PALM_TO_HAND_LENGTH
        lo = np.array([-cfg.obb_padding_m, -width_def.value_m / 2.0, -thick_def.value_m / 2.0])
        hi = np.array([length + cfg.obb_padding_m, width_def.value_m / 2.0, thick_def.value_m / 2.0])
        source = _weakest(width_def.source, thick_def.source)
        rep.update(reason="insufficient_finger_extent", source=source.value)
        return FitOutcome(_box(cid, body_part, wrist, axes, lo, hi, cfg.max_hand_half_extent_m, source, conf), report=rep)

    # Observed bounds: landmarks fully, surface robustly.
    lo = lm_local.min(axis=0) if lm_local.shape[0] else np.zeros(3)
    hi = lm_local.max(axis=0) if lm_local.shape[0] else np.zeros(3)
    sources = [FitSource.OBSERVED]
    if support >= cfg.obb_min_support:
        s_lo, s_hi = robust_bounds(surf_local, cfg.obb_bounds_percentile)
        lo, hi = np.minimum(lo, s_lo), np.maximum(hi, s_hi)
        width = float(hi[1] - lo[1])
        thick = float(hi[2] - lo[2])
        spread_w = mad(surf_local[:, 1], float(np.median(surf_local[:, 1])))
        spread_t = mad(surf_local[:, 2], float(np.median(surf_local[:, 2])))
        updates.append(("hand_width_m", side, DimensionEstimate(width, FitSource.OBSERVED, support, spread_w)))
        updates.append(("hand_thickness_m", side, DimensionEstimate(thick, FitSource.OBSERVED, support, spread_t)))
        rep.update(width_m=width, thickness_m=thick)
    else:
        # No surface: width from landmarks is fine (thumb + MCP span), thickness cannot be.
        rep["thickness_source"] = thick_def.source.value
        half_t = thick_def.value_m / 2.0
        lo[2], hi[2] = min(lo[2], -half_t), max(hi[2], half_t)
        sources.append(thick_def.source)
    lo, hi = lo - cfg.obb_padding_m, hi + cfg.obb_padding_m
    source = _weakest(*sources)
    rep["source"] = source.value
    quality = 0.5 * conf + 0.5 * min(1.0, (lm_local.shape[0] / 21.0) * 0.6 + min(1.0, support / 200.0) * 0.4)
    box = _box(cid, body_part, wrist, axes, lo, hi, cfg.max_hand_half_extent_m, source, quality)
    return FitOutcome(box, subject_updates=tuple(updates), report=rep)


def _box(cid, body_part, origin, axes, lo, hi, cap, source: FitSource, quality_base: float):
    half = (hi - lo) / 2.0
    half = np.minimum(half, np.asarray(cap))
    centre_local = (lo + hi) / 2.0
    centre = origin + axes.T @ centre_local
    return make_obb(cid, body_part, centre, axes, half, source, source_quality(source, quality_base))
