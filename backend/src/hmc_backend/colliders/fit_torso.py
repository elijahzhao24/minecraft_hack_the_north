"""Head sphere and chest/pelvis OBBs.

Head: an algebraic sphere fit to head-region surface around the registered
``body.head_center`` with hair/clothing outliers trimmed by MAD. The fitted
centre is accepted only when it stays close to the registered centre;
otherwise the registered centre is kept and the radius is a robust percentile
of radial distances. Without surface support the saved subject head radius is
used (labelled).

Torso: axes are up (hip centre -> shoulder centre), lateral (right -> left
shoulder for the chest, right -> left hip for the pelvis), and forward, all
orthogonalized. Arm points are excluded by the nearest-segment assignment.
Chest and pelvis are separate boxes split along the up axis with a small
overlap so a bent posture is covered without a gap. Bounds come from the
masked torso surface plus the shoulder/hip landmarks; when unsupported the
box uses shoulder/hip width and the saved subject depth (labelled).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from hmc_backend.colliders.fit_common import (
    FitConfig,
    FitOutcome,
    LandmarkMap,
    Segment,
    landmark_conf,
    mad,
    orthonormal_frame,
    pos,
    project_extents,
    robust_bounds,
    robust_percentile,
    source_quality,
)
from hmc_backend.colliders.models import SPEC_BY_ID, disabled, make_obb, make_sphere
from hmc_backend.colliders.subject import DimensionEstimate, SubjectDimensions
from hmc_backend.contracts.enums import FitSource
from hmc_backend.vision.model_mapping import body_name

# Fraction of the hip->shoulder distance covered by each box (overlapping).
PELVIS_BOTTOM_FRACTION = -0.14  # the pelvis reaches only a few cm below the hip joints
PELVIS_TOP_FRACTION = 0.36
CHEST_BOTTOM_FRACTION = 0.20
# Head sphere fit: keep points within k * MAD of the median radius; accept the
# algebraic centre only if it moves less than this from the registered centre.
HEAD_TRIM_K = 3.0
HEAD_MAX_CENTRE_SHIFT_M = 0.05


def _select(xyz: NDArray, assignment: NDArray, segments: list[Segment], *keys: str) -> NDArray:
    idx = [i for i, s in enumerate(segments) if s.key in keys]
    if not idx or xyz.shape[0] == 0:
        return np.zeros((0, 3), np.float32)
    return xyz[np.isin(assignment, idx)]


def _algebraic_sphere(pts: NDArray) -> tuple[NDArray, float] | None:
    """Least-squares sphere through ``pts`` (Pratt-style linear form)."""
    p = np.asarray(pts, dtype=np.float64)
    a = np.hstack([2.0 * p, np.ones((p.shape[0], 1))])
    b = (p**2).sum(axis=1)
    sol, *_ = np.linalg.lstsq(a, b, rcond=None)
    c = sol[:3]
    r2 = sol[3] + float(np.dot(c, c))
    if not np.isfinite(r2) or r2 <= 0:
        return None
    return c, float(np.sqrt(r2))


def fit_head(lms: LandmarkMap, xyz: NDArray, assignment: NDArray, segments: list[Segment], subject: SubjectDimensions, cfg: FitConfig) -> FitOutcome:
    cid = "head"
    body_part = SPEC_BY_ID[cid].body_part
    rep: dict = {}
    centre = pos(lms, body_name("head_center"))
    if centre is None:
        rep["reason"] = "missing_head_center"
        return FitOutcome(disabled(cid, "missing_head_center"), report=rep)
    conf = landmark_conf(lms, body_name("head_center"))

    mine = _select(xyz, assignment, segments, "head").astype(np.float64)
    if mine.shape[0]:
        d = np.linalg.norm(mine - centre, axis=1)
        mine = mine[d <= cfg.head_search_radius_m]
        d = d[d <= cfg.head_search_radius_m]
        med = float(np.median(d))
        spread = mad(d, med) * 1.4826
        keep = np.abs(d - med) <= HEAD_TRIM_K * max(spread, 0.005)
        mine, d = mine[keep], d[keep]
    support = int(mine.shape[0])
    rep["support"] = support

    if support >= cfg.head_min_support:
        fit = _algebraic_sphere(mine)
        if fit is not None and np.linalg.norm(fit[0] - centre) <= HEAD_MAX_CENTRE_SHIFT_M and 0.05 <= fit[1] <= cfg.max_head_radius_m:
            c, r = fit
            rep["centre_shift_m"] = float(np.linalg.norm(c - centre))
        else:
            c = centre
            r = min(robust_percentile(d, cfg.head_radius_percentile), cfg.max_head_radius_m)
            rep["centre_shift_m"] = 0.0
        r = float(r)
        est = DimensionEstimate(r, FitSource.OBSERVED, support, mad(np.linalg.norm(mine - c, axis=1), r))
        rep.update(radius_m=r, source="observed")
        quality = source_quality(FitSource.OBSERVED, 0.5 * conf + 0.5 * min(1.0, support / 400.0))
        return FitOutcome(make_sphere(cid, body_part, c, r, FitSource.OBSERVED, quality), subject_updates=(("head_radius_m", None, est),), report=rep)

    fb = subject.as_subject_default("head_radius_m")
    rep.update(radius_m=fb.value_m, source=fb.source.value, reason="insufficient_support")
    return FitOutcome(make_sphere(cid, body_part, centre, fb.value_m, fb.source, source_quality(fb.source, conf)), report=rep)


def torso_axes(lms: LandmarkMap, lateral_pair: tuple[str, str]) -> tuple[NDArray, NDArray, NDArray] | None:
    """(hip_centre, up_unit_len_vector, row axes) or None when landmarks are missing/degenerate."""
    ls, rs = pos(lms, body_name("left_shoulder")), pos(lms, body_name("right_shoulder"))
    lh, rh = pos(lms, body_name("left_hip")), pos(lms, body_name("right_hip"))
    if ls is None or rs is None or lh is None or rh is None:
        return None
    hip_c, sh_c = (lh + rh) / 2.0, (ls + rs) / 2.0
    up = sh_c - hip_c
    if np.linalg.norm(up) < 0.15:
        return None
    left, right = pos(lms, body_name(lateral_pair[0])), pos(lms, body_name(lateral_pair[1]))
    if left is None or right is None:
        return None
    axes = orthonormal_frame(up, left - right)
    if axes is None:
        return None
    return hip_c, up, axes


def _fit_torso_box(
    cid: str,
    lms: LandmarkMap,
    xyz: NDArray,
    assignment: NDArray,
    segments: list[Segment],
    subject: SubjectDimensions,
    cfg: FitConfig,
    *,
    lateral_pair: tuple[str, str],
    up_range: tuple[float, float],
    anchors: tuple[str, ...],
) -> FitOutcome:
    body_part = SPEC_BY_ID[cid].body_part
    rep: dict = {}
    frame = torso_axes(lms, lateral_pair)
    if frame is None:
        rep["reason"] = "missing_or_degenerate_torso"
        return FitOutcome(disabled(cid, "missing_or_degenerate_torso"), report=rep)
    hip_c, up, axes = frame
    length = float(np.linalg.norm(up))
    lo_up, hi_up = up_range[0] * length, up_range[1] * length

    anchor_pts = np.stack([p for n in anchors if (p := pos(lms, body_name(n))) is not None])
    a_local = project_extents(anchor_pts, hip_c, axes)
    lo, hi = a_local.min(axis=0), a_local.max(axis=0)

    mine = _select(xyz, assignment, segments, "torso", "pelvis")
    if mine.shape[0]:
        local = project_extents(mine, hip_c, axes)
        in_band = (local[:, 0] >= lo_up) & (local[:, 0] <= hi_up)
        local = local[in_band]
    else:
        local = np.zeros((0, 3))
    support = int(local.shape[0])
    rep["support"] = support
    conf = landmark_conf(lms, *(body_name(n) for n in anchors))

    if support >= cfg.torso_min_support:
        # Surface defines lateral/forward; joint anchors only guarantee the up extent
        # (joint centres sit inside the deltoid/hip mass, not on the torso surface).
        s_lo, s_hi = robust_bounds(local, cfg.obb_bounds_percentile)
        lo = np.array([min(lo[0], s_lo[0]), s_lo[1], s_lo[2]])
        hi = np.array([max(hi[0], s_hi[0]), s_hi[1], s_hi[2]])
        source = FitSource.OBSERVED
        depth = float(hi[2] - lo[2])
        updates = (("torso_depth_m", None, DimensionEstimate(depth, FitSource.OBSERVED, support, mad(local[:, 2], float(np.median(local[:, 2]))))),) if cid == "torso.chest" else ()
        rep.update(depth_m=depth, source="observed")
    else:
        depth_def = subject.as_subject_default("torso_depth_m")
        half_d = depth_def.value_m / 2.0
        lo[2], hi[2] = min(lo[2], -half_d), max(hi[2], half_d)
        # Lateral: anchors are joint centres; add a nominal soft-tissue margin.
        lo[1], hi[1] = lo[1] - 0.03, hi[1] + 0.03
        source = depth_def.source
        updates = ()
        rep.update(source=source.value, reason="insufficient_support")
    # Clamp the up extent to this box's band so chest and pelvis stay separate.
    lo[0], hi[0] = max(lo[0], lo_up), min(hi[0], hi_up)
    if hi[0] - lo[0] < 0.05:
        lo[0], hi[0] = lo_up, hi_up
    lo, hi = lo - cfg.obb_padding_m, hi + cfg.obb_padding_m

    half = np.minimum((hi - lo) / 2.0, np.asarray(cfg.max_torso_half_extent_m))
    centre = hip_c + axes.T @ ((lo + hi) / 2.0)
    quality = source_quality(source, 0.5 * conf + 0.5 * min(1.0, support / 1000.0))
    return FitOutcome(make_obb(cid, body_part, centre, axes, half, source, quality), subject_updates=updates, report=rep)


def fit_chest(lms, xyz, assignment, segments, subject, cfg) -> FitOutcome:
    return _fit_torso_box(
        "torso.chest", lms, xyz, assignment, segments, subject, cfg,
        lateral_pair=("left_shoulder", "right_shoulder"),
        up_range=(CHEST_BOTTOM_FRACTION, 1.25),
        anchors=("left_shoulder", "right_shoulder"),
    )


def fit_pelvis(lms, xyz, assignment, segments, subject, cfg) -> FitOutcome:
    return _fit_torso_box(
        "torso.pelvis", lms, xyz, assignment, segments, subject, cfg,
        lateral_pair=("left_hip", "right_hip"),
        up_range=(PELVIS_BOTTOM_FRACTION, PELVIS_TOP_FRACTION),
        anchors=("left_hip", "right_hip"),
    )

