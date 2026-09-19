"""Hand crops and anatomical left/right association.

Two responsibilities:

1. **Crops.** When the full-frame hand pass misses small hands, derive a padded
   square crop around each pose wrist (biased along the elbow->wrist direction)
   and express the crop as an explicit 3x3 affine so normalized crop
   coordinates map back to full-image pixels exactly and reversibly.

2. **Association.** Score every candidate hand against the anatomical left and
   right body wrists using pixel distance, elbow->wrist direction consistency,
   the model's handedness label (after correcting for an *unmirrored* input),
   and optional cross-view agreement. Require a margin over the alternate
   assignment; ambiguous hands stay invalid rather than being swapped.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from hmc_backend.contracts.internal import Landmark2DObservation
from hmc_backend.vision.model_mapping import Side, mirror_side

# --- crops -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HandCrop:
    """Square crop in full-image pixels plus its normalized->full affine."""

    x0: int
    y0: int
    size: int
    image_wh: tuple[int, int]

    @property
    def affine_full_from_norm(self) -> NDArray[np.float64]:
        """Maps ``[u_norm, v_norm, 1]`` (0..1 inside the crop) to full-image pixels."""
        return np.array([[self.size, 0.0, self.x0], [0.0, self.size, self.y0], [0.0, 0.0, 1.0]])

    @property
    def affine_norm_from_full(self) -> NDArray[np.float64]:
        return np.linalg.inv(self.affine_full_from_norm)

    def to_full(self, uv_norm: tuple[float, float]) -> tuple[float, float]:
        p = self.affine_full_from_norm @ np.array([uv_norm[0], uv_norm[1], 1.0])
        return float(p[0]), float(p[1])

    def to_norm(self, xy_full: tuple[float, float]) -> tuple[float, float]:
        p = self.affine_norm_from_full @ np.array([xy_full[0], xy_full[1], 1.0])
        return float(p[0]), float(p[1])

    def extract(self, image: NDArray[np.uint8]) -> NDArray[np.uint8]:
        """Zero-padded crop so the affine holds even when the box leaves the image."""
        h, w = image.shape[:2]
        out = np.zeros((self.size, self.size, image.shape[2]), image.dtype)
        sx0, sy0 = max(self.x0, 0), max(self.y0, 0)
        sx1, sy1 = min(self.x0 + self.size, w), min(self.y0 + self.size, h)
        if sx1 > sx0 and sy1 > sy0:
            out[sy0 - self.y0 : sy1 - self.y0, sx0 - self.x0 : sx1 - self.x0] = image[sy0:sy1, sx0:sx1]
        return out


def wrist_crop(
    wrist_xy: tuple[float, float],
    elbow_xy: tuple[float, float] | None,
    image_wh: tuple[int, int],
    *,
    forearm_fraction: float = 1.4,
    min_size_px: int = 96,
    forward_bias: float = 0.35,
) -> HandCrop:
    """Padded square around the wrist, shifted along elbow->wrist toward the fingers.

    Size is a multiple of the projected forearm length (or ``min_size_px``), so
    a distant person still gets a usable crop.
    """
    wx, wy = wrist_xy
    if elbow_xy is not None:
        dx, dy = wx - elbow_xy[0], wy - elbow_xy[1]
        forearm = math.hypot(dx, dy)
    else:
        dx = dy = 0.0
        forearm = 0.0
    size = int(max(min_size_px, round(forearm * forearm_fraction)))
    if forearm > 1e-6:
        cx = wx + dx / forearm * size * forward_bias
        cy = wy + dy / forearm * size * forward_bias
    else:
        cx, cy = wx, wy
    x0 = round(cx - size / 2.0)
    y0 = round(cy - size / 2.0)
    return HandCrop(x0=x0, y0=y0, size=size, image_wh=image_wh)


# --- handedness --------------------------------------------------------------

Handedness = Literal["Left", "Right"]


def anatomical_side_from_model_handedness(label: str, *, image_is_mirrored: bool) -> Side:
    """MediaPipe reports handedness assuming a mirrored (selfie) image.

    The transmitted capture is *not* mirrored, so the label is swapped unless
    the caller states the image really is mirrored.
    """
    side: Side = "left" if label.lower().startswith("l") else "right"
    return side if image_is_mirrored else mirror_side(side)


# --- association -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HandCandidate:
    """One detected hand in full-image pixels (21 landmarks)."""

    landmarks: tuple[Landmark2DObservation, ...]
    handedness_label: str | None
    handedness_score: float | None
    world_prior_m: NDArray[np.float32] | None = None
    crop: HandCrop | None = None

    @property
    def wrist_xy(self) -> tuple[float, float]:
        return self.landmarks[0].xy_px


@dataclass(frozen=True, slots=True)
class BodyArm:
    """Body wrist/elbow for one anatomical side in the same image."""

    side: Side
    wrist_xy: tuple[float, float] | None
    elbow_xy: tuple[float, float] | None
    wrist_visibility: float | None = None


@dataclass(frozen=True, slots=True)
class AssociationConfig:
    max_wrist_distance_ratio: float = 0.6  # of forearm length (or abs px below)
    max_wrist_distance_px: float = 80.0
    handedness_weight: float = 0.25
    direction_weight: float = 0.15
    cross_view_weight: float = 0.2
    min_margin: float = 0.15
    image_is_mirrored: bool = False


@dataclass(frozen=True, slots=True)
class Association:
    side: Side
    candidate_index: int
    score: float
    margin: float
    reasons: tuple[str, ...] = field(default_factory=tuple)


def _distance_score(cand: HandCandidate, arm: BodyArm, cfg: AssociationConfig) -> float | None:
    if arm.wrist_xy is None:
        return None
    d = math.dist(cand.wrist_xy, arm.wrist_xy)
    limit = cfg.max_wrist_distance_px
    if arm.elbow_xy is not None:
        forearm = math.dist(arm.wrist_xy, arm.elbow_xy)
        limit = max(limit, forearm * cfg.max_wrist_distance_ratio)
    if d > limit:
        return None
    return 1.0 - d / limit


def _direction_score(cand: HandCandidate, arm: BodyArm) -> float:
    """+1 when the hand centre lies along elbow->wrist beyond the wrist, -1 when behind."""
    if arm.wrist_xy is None or arm.elbow_xy is None:
        return 0.0
    ew = np.subtract(arm.wrist_xy, arm.elbow_xy)
    n = np.linalg.norm(ew)
    if n < 1e-6:
        return 0.0
    ew /= n
    centre = np.mean([lm.xy_px for lm in cand.landmarks if lm.valid], axis=0)
    wh = centre - np.asarray(arm.wrist_xy)
    m = np.linalg.norm(wh)
    if m < 1e-6:
        return 0.0
    return float(np.clip(np.dot(ew, wh / m), -1.0, 1.0))


def _handedness_score(cand: HandCandidate, side: Side, cfg: AssociationConfig) -> float:
    if cand.handedness_label is None:
        return 0.0
    predicted = anatomical_side_from_model_handedness(cand.handedness_label, image_is_mirrored=cfg.image_is_mirrored)
    conf = cand.handedness_score if cand.handedness_score is not None else 0.5
    return conf if predicted == side else -conf


def score_candidate(
    cand: HandCandidate,
    arm: BodyArm,
    cfg: AssociationConfig,
    *,
    cross_view_agreement: float | None = None,
) -> float | None:
    """Combined score for assigning ``cand`` to ``arm.side``; ``None`` when out of range."""
    dist = _distance_score(cand, arm, cfg)
    if dist is None:
        return None
    s = dist
    s += cfg.direction_weight * _direction_score(cand, arm)
    s += cfg.handedness_weight * _handedness_score(cand, arm.side, cfg)
    if cross_view_agreement is not None:
        s += cfg.cross_view_weight * float(np.clip(cross_view_agreement, -1.0, 1.0))
    return s


def associate_hands(
    candidates: list[HandCandidate],
    arms: dict[Side, BodyArm],
    cfg: AssociationConfig,
    *,
    cross_view: dict[tuple[int, Side], float] | None = None,
) -> dict[Side, Association]:
    """Assign at most one candidate per side, requiring a margin over the alternative.

    A candidate is assigned to a side only if its score there exceeds both its
    own score for the other side and any other candidate's score for that side
    by ``cfg.min_margin``. Otherwise the side stays unassigned (invalid).
    """
    cross_view = cross_view or {}
    scores: dict[tuple[int, Side], float] = {}
    for i, cand in enumerate(candidates):
        for side, arm in arms.items():
            s = score_candidate(cand, arm, cfg, cross_view_agreement=cross_view.get((i, side)))
            if s is not None:
                scores[(i, side)] = s

    out: dict[Side, Association] = {}
    used: set[int] = set()
    # Greedy by score, then verify margins.
    for (i, side), s in sorted(scores.items(), key=lambda kv: -kv[1]):
        if side in out or i in used:
            continue
        other_side: Side = mirror_side(side)
        own_alt = scores.get((i, other_side), -math.inf)
        rival = max(
            (v for (j, sd), v in scores.items() if sd == side and j != i and j not in used),
            default=-math.inf,
        )
        margin = s - max(own_alt, rival)
        if margin < cfg.min_margin:
            continue
        out[side] = Association(side=side, candidate_index=i, score=s, margin=margin)
        used.add(i)
    return out
