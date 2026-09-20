"""Register MediaPipe world priors into the stage frame.

MediaPipe pose world landmarks are *hip-centred* and hand world landmarks are
*hand-centred* metric coordinates in an arbitrary orientation. They are never
stage coordinates. This module fits a robust similarity (rotation, translation,
bounded scale) from prior space to stage space against reliably observed
landmarks, rejecting fits with too few non-collinear anchors, implausible
scale, or large residual.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from hmc_backend.vision.model_mapping import HAND_MCPS, hand_index, pose_index

# Anchors preferred for pose registration (stable, large baseline joints).
POSE_ANCHOR_NAMES: tuple[str, ...] = (
    "left_shoulder",
    "right_shoulder",
    "left_hip",
    "right_hip",
    "left_elbow",
    "right_elbow",
    "left_knee",
    "right_knee",
)
HAND_ANCHOR_NAMES: tuple[str, ...] = ("wrist", *HAND_MCPS)


@dataclass(frozen=True, slots=True)
class RegistrationConfig:
    min_anchors: int = 4
    min_hand_anchors: int = 3
    min_scale: float = 0.6
    max_scale: float = 1.6
    max_rms_residual_m: float = 0.08
    max_hand_rms_residual_m: float = 0.03
    collinearity_ratio: float = 0.05  # 2nd singular value / 1st must exceed this
    irls_iterations: int = 5
    huber_delta_m: float = 0.05


@dataclass(frozen=True, slots=True)
class Similarity:
    """``p_stage = scale * R @ p_prior + t``."""

    scale: float
    rotation: NDArray[np.float64]  # 3x3
    translation: NDArray[np.float64]  # 3
    rms_residual_m: float
    anchor_count: int
    inlier_names: tuple[str, ...]

    def apply(self, points: NDArray) -> NDArray[np.float64]:
        p = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        return (self.scale * (p @ self.rotation.T)) + self.translation


class RegistrationRejected(ValueError):
    """Carries the rejection reason for the debug report."""


def umeyama(src: NDArray, dst: NDArray, weights: NDArray | None = None, *, with_scale: bool = True) -> tuple[float, NDArray, NDArray]:
    """Weighted Umeyama similarity: minimise ``sum w |dst - (s R src + t)|^2``."""
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    n = src.shape[0]
    w = np.ones(n) if weights is None else np.asarray(weights, dtype=np.float64)
    w = w / w.sum()
    mu_s = (w[:, None] * src).sum(axis=0)
    mu_d = (w[:, None] * dst).sum(axis=0)
    xs = src - mu_s
    xd = dst - mu_d
    cov = (w[:, None] * xd).T @ xs  # 3x3
    u, sv, vt = np.linalg.svd(cov)
    d = np.eye(3)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        d[2, 2] = -1.0
    r = u @ d @ vt
    if with_scale:
        var_s = float((w * (xs**2).sum(axis=1)).sum())
        if var_s < 1e-12:
            raise RegistrationRejected("degenerate_source")
        s = float(np.trace(np.diag(sv) @ d) / var_s)
    else:
        s = 1.0
    t = mu_d - s * (r @ mu_s)
    return s, r, t


def _anchor_rank_ok(pts: NDArray, ratio: float) -> bool:
    c = pts - pts.mean(axis=0)
    sv = np.linalg.svd(c, compute_uv=False)
    return bool(sv[0] > 1e-9 and sv[1] / sv[0] > ratio)


def fit_similarity(
    prior_pts: NDArray,
    stage_pts: NDArray,
    names: tuple[str, ...],
    cfg: RegistrationConfig,
    *,
    min_anchors: int,
    max_rms: float,
) -> Similarity:
    """Robust similarity via IRLS with a Huber weight; raises on any gate failure."""
    src = np.asarray(prior_pts, dtype=np.float64).reshape(-1, 3)
    dst = np.asarray(stage_pts, dtype=np.float64).reshape(-1, 3)
    if src.shape != dst.shape or src.shape[0] != len(names):
        raise RegistrationRejected("shape_mismatch")
    if src.shape[0] < min_anchors:
        raise RegistrationRejected(f"too_few_anchors:{src.shape[0]}")
    if not (np.isfinite(src).all() and np.isfinite(dst).all()):
        raise RegistrationRejected("non_finite_anchor")
    if not _anchor_rank_ok(dst, cfg.collinearity_ratio) or not _anchor_rank_ok(src, cfg.collinearity_ratio):
        raise RegistrationRejected("collinear_anchors")

    w = np.ones(src.shape[0])
    s, r, t = umeyama(src, dst, w)
    for _ in range(cfg.irls_iterations):
        res = np.linalg.norm(dst - (s * (src @ r.T) + t), axis=1)
        w = np.where(res <= cfg.huber_delta_m, 1.0, cfg.huber_delta_m / np.maximum(res, 1e-12))
        s, r, t = umeyama(src, dst, w)

    res = np.linalg.norm(dst - (s * (src @ r.T) + t), axis=1)
    inliers = res <= 2.0 * cfg.huber_delta_m
    if int(inliers.sum()) < min_anchors:
        raise RegistrationRejected(f"too_few_inliers:{int(inliers.sum())}")
    rms = float(np.sqrt(np.mean(res[inliers] ** 2)))
    if rms > max_rms:
        raise RegistrationRejected(f"residual:{rms:.3f}m")
    if not (cfg.min_scale <= s <= cfg.max_scale):
        raise RegistrationRejected(f"scale:{s:.2f}")
    return Similarity(
        scale=s,
        rotation=r,
        translation=t,
        rms_residual_m=rms,
        anchor_count=int(inliers.sum()),
        inlier_names=tuple(n for n, ok in zip(names, inliers, strict=True) if ok),
    )


def register_pose_prior(
    pose_world_prior_m: NDArray,
    observed_stage: dict[str, tuple[float, float, float]],
    cfg: RegistrationConfig,
) -> Similarity:
    """Fit the 33x3 hip-centred pose prior against observed body joints (short names)."""
    prior = np.asarray(pose_world_prior_m, dtype=np.float64)
    if prior.shape != (33, 3):
        raise RegistrationRejected("pose_prior_shape")
    names = tuple(n for n in POSE_ANCHOR_NAMES if n in observed_stage)
    src = np.array([prior[pose_index(n)] for n in names]).reshape(-1, 3)
    dst = np.array([observed_stage[n] for n in names]).reshape(-1, 3)
    return fit_similarity(src, dst, names, cfg, min_anchors=cfg.min_anchors, max_rms=cfg.max_rms_residual_m)


def register_hand_prior(
    hand_world_prior_m: NDArray,
    observed_stage: dict[str, tuple[float, float, float]],
    cfg: RegistrationConfig,
) -> Similarity:
    """Fit the 21x3 hand-centred prior against the observed wrist + MCPs (short names)."""
    prior = np.asarray(hand_world_prior_m, dtype=np.float64)
    if prior.shape != (21, 3):
        raise RegistrationRejected("hand_prior_shape")
    names = tuple(n for n in HAND_ANCHOR_NAMES if n in observed_stage)
    src = np.array([prior[hand_index(n)] for n in names]).reshape(-1, 3)
    dst = np.array([observed_stage[n] for n in names]).reshape(-1, 3)
    return fit_similarity(
        src, dst, names, cfg, min_anchors=cfg.min_hand_anchors, max_rms=cfg.max_hand_rms_residual_m
    )

