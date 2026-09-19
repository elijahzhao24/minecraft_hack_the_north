"""Depth-backed 3D observations for 2D landmarks.

For each valid 2D landmark the RGB pixel centre is mapped into the depth raster
(same uncropped scale convention as reconstruction), a small neighbourhood is
inspected under the person mask and depth-confidence gate, background samples
across a depth discontinuity are rejected relative to the local median, and a
robust (weighted-median) depth is unprojected with ``K_depth`` and transformed
through ``T_stage_from_optical``.

The result is a *surface* observation: the visible skin/clothing along the
landmark's ray, not automatically the joint centre. Fusion decides how to use
it (see :mod:`hmc_backend.vision.landmarks`).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from hmc_backend.contracts.internal import CameraCalibration, CapturedFrame, Landmark2DObservation
from hmc_backend.reconstruction.geometry import apply_transform, scale_intrinsics, unproject


@dataclass(frozen=True, slots=True)
class DepthSamplingConfig:
    """Tunables; evaluate on saved poses rather than widening for one demo."""

    radius_px: int = 2
    max_radius_px: int = 4  # grow the window once if support is short
    min_support: int = 4
    confidence_min: int = 1
    discontinuity_abs_m: float = 0.05
    discontinuity_rel: float = 0.03  # 3% of the median depth
    depth_min_m: float = 0.2
    depth_max_m: float = 5.0


@dataclass(frozen=True, slots=True)
class DepthObservation:
    """A robust surface sample for one landmark in one view."""

    name: str
    device_id: str
    position_stage_m: tuple[float, float, float]
    depth_m: float
    support: int
    spread_m: float
    pixel_rgb: tuple[float, float]
    pixel_depth: tuple[float, float]
    visibility: float | None

    @property
    def quality(self) -> float:
        """0..1 heuristic: more support and tighter spread is better."""
        support_term = min(1.0, self.support / 12.0)
        spread_term = max(0.0, 1.0 - self.spread_m / 0.03)
        return float(0.5 * support_term + 0.5 * spread_term)


def rgb_to_depth_px(xy_rgb: tuple[float, float], rgb_wh: tuple[int, int], depth_wh: tuple[int, int]) -> tuple[float, float]:
    """Map an RGB pixel centre to depth-raster coordinates (uncropped resample)."""
    sx = depth_wh[0] / rgb_wh[0]
    sy = depth_wh[1] / rgb_wh[1]
    return (xy_rgb[0] * sx, xy_rgb[1] * sy)


def depth_to_rgb_px(xy_depth: tuple[float, float], rgb_wh: tuple[int, int], depth_wh: tuple[int, int]) -> tuple[float, float]:
    sx = rgb_wh[0] / depth_wh[0]
    sy = rgb_wh[1] / depth_wh[1]
    return (xy_depth[0] * sx, xy_depth[1] * sy)


def _weighted_median(values: NDArray[np.float64], weights: NDArray[np.float64]) -> float:
    order = np.argsort(values)
    v = values[order]
    w = weights[order]
    cum = np.cumsum(w)
    idx = int(np.searchsorted(cum, cum[-1] / 2.0))
    return float(v[min(idx, len(v) - 1)])


def robust_depth_at(
    depth_m: NDArray[np.float32],
    confidence: NDArray[np.uint8],
    person_mask_depth: NDArray[np.bool_],
    xy_depth: tuple[float, float],
    cfg: DepthSamplingConfig,
) -> tuple[float, int, float] | None:
    """Return ``(depth, support, spread)`` for a depth-raster location or ``None``.

    Samples are drawn from a square window of ``radius_px`` around the pixel,
    masked by person/confidence/finite/positive/range; then samples farther than
    the discontinuity threshold from the window median are dropped so a
    background surface never leaks into a landmark on a silhouette edge.
    """
    h, w = depth_m.shape
    u0 = round(float(xy_depth[0]))
    v0 = round(float(xy_depth[1]))
    if not (0 <= u0 < w and 0 <= v0 < h):
        return None

    for radius in range(cfg.radius_px, cfg.max_radius_px + 1):
        u_lo, u_hi = max(0, u0 - radius), min(w, u0 + radius + 1)
        v_lo, v_hi = max(0, v0 - radius), min(h, v0 + radius + 1)
        win = depth_m[v_lo:v_hi, u_lo:u_hi].astype(np.float64)
        conf = confidence[v_lo:v_hi, u_lo:u_hi]
        mask = person_mask_depth[v_lo:v_hi, u_lo:u_hi]
        ok = (
            mask
            & (conf >= cfg.confidence_min)
            & np.isfinite(win)
            & (win > 0.0)
            & (win >= cfg.depth_min_m)
            & (win <= cfg.depth_max_m)
        )
        if int(ok.sum()) < cfg.min_support:
            continue
        vals = win[ok]
        # Distance weights: nearer the centre counts more.
        vv, uu = np.nonzero(ok)
        du = (uu + u_lo) - u0
        dv = (vv + v_lo) - v0
        weights = 1.0 / (1.0 + np.sqrt(du * du + dv * dv))

        med = float(np.median(vals))
        thresh = max(cfg.discontinuity_abs_m, cfg.discontinuity_rel * med)
        keep = np.abs(vals - med) <= thresh
        if int(keep.sum()) < cfg.min_support:
            continue
        vals_k = vals[keep]
        w_k = weights[keep]
        depth = _weighted_median(vals_k, w_k)
        spread = float(np.median(np.abs(vals_k - depth)))  # MAD
        return depth, int(keep.sum()), spread
    return None


def resample_mask_to_depth(mask_rgb: NDArray[np.bool_], depth_hw: tuple[int, int]) -> NDArray[np.bool_]:
    """Nearest-neighbour resample of an RGB-resolution mask (same rule as reconstruction)."""
    h_d, w_d = depth_hw
    h_r, w_r = mask_rgb.shape
    if (h_r, w_r) == (h_d, w_d):
        return mask_rgb
    vv = np.clip((np.arange(h_d) * (h_r / h_d)).astype(np.int64), 0, h_r - 1)
    uu = np.clip((np.arange(w_d) * (w_r / w_d)).astype(np.int64), 0, w_r - 1)
    return mask_rgb[np.ix_(vv, uu)]


def observe_landmarks(
    frame: CapturedFrame,
    calibration: CameraCalibration,
    person_mask: NDArray[np.bool_],
    landmarks: tuple[Landmark2DObservation, ...],
    cfg: DepthSamplingConfig,
    *,
    name_prefix: str = "",
) -> dict[str, DepthObservation]:
    """Depth-backed stage-frame observations for every valid 2D landmark that has support."""
    h_d, w_d = frame.depth_m.shape
    mask_depth = resample_mask_to_depth(person_mask, (h_d, w_d))
    k_depth = scale_intrinsics(calibration.K_rgb, calibration.rgb_size, calibration.depth_size)

    out: dict[str, DepthObservation] = {}
    for lm in landmarks:
        if not lm.valid:
            continue
        xy_d = rgb_to_depth_px(lm.xy_px, calibration.rgb_size, calibration.depth_size)
        est = robust_depth_at(frame.depth_m, frame.confidence, mask_depth, xy_d, cfg)
        if est is None:
            continue
        depth, support, spread = est
        optical = unproject(np.array([xy_d[0]]), np.array([xy_d[1]]), np.array([depth]), k_depth)
        stage = apply_transform(calibration.T_stage_from_optical, optical)[0]
        if not np.isfinite(stage).all():
            continue
        name = name_prefix + lm.name
        out[name] = DepthObservation(
            name=name,
            device_id=frame.device_id,
            position_stage_m=(float(stage[0]), float(stage[1]), float(stage[2])),
            depth_m=depth,
            support=support,
            spread_m=spread,
            pixel_rgb=lm.xy_px,
            pixel_depth=xy_d,
            visibility=lm.visibility,
        )
    return out
