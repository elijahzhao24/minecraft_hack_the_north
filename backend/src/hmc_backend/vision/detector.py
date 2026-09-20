"""MediaPipe-backed ``ViewDetector``.

One long-lived Pose Landmarker (one person, segmentation masks on) and one Hand
Landmarker (up to two hands), both in ``RunningMode.IMAGE`` so frozen captures
are processed deterministically. The conversion from raw model output into
:class:`ViewDetection` is kept in pure functions operating on small
framework-independent records (:class:`RawPose`, :class:`RawHand`) so it can be
unit-tested without model weights.

Visibility/presence are preserved exactly where each model supplies them; no
joint confidence is invented by averaging unrelated values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

from hmc_backend.contracts.internal import CapturedFrame, Landmark2DObservation, ViewDetection
from hmc_backend.vision.hand_association import (
    AssociationConfig,
    BodyArm,
    HandCandidate,
    HandCrop,
    associate_hands,
    wrist_crop,
)
from hmc_backend.vision.model_mapping import (
    HAND_LANDMARK_COUNT,
    HAND_LANDMARK_NAMES,
    POSE_LANDMARK_COUNT,
    POSE_LANDMARK_NAMES,
    SIDES,
    Side,
)
from hmc_backend.vision.models import ResolvedModels


@dataclass(frozen=True, slots=True)
class DetectorConfig:
    """Thresholds are configuration; evaluate them on saved poses."""

    min_pose_detection_confidence: float = 0.5
    min_pose_presence_confidence: float = 0.5
    min_tracking_confidence: float = 0.5
    min_hand_detection_confidence: float = 0.5
    min_hand_presence_confidence: float = 0.5
    num_hands: int = 2
    mask_threshold: float = 0.5
    min_landmark_visibility: float = 0.5
    min_landmark_presence: float = 0.5
    crop_fallback: bool = True
    association: AssociationConfig = field(default_factory=AssociationConfig)


# --- framework-independent raw results ---------------------------------------


@dataclass(frozen=True, slots=True)
class RawPose:
    """33 normalized landmarks ``(x, y, z, visibility, presence)`` plus optional extras."""

    landmarks_norm: NDArray[np.float64]  # 33 x 5
    world_m: NDArray[np.float32] | None  # 33 x 3, hip-centred
    mask: NDArray[np.float32] | None  # H x W soft mask in the input raster


@dataclass(frozen=True, slots=True)
class RawHand:
    landmarks_norm: NDArray[np.float64]  # 21 x 3 (x, y, z) normalized to its input image
    handedness_label: str | None
    handedness_score: float | None
    world_m: NDArray[np.float32] | None  # 21 x 3, hand-centred
    crop: HandCrop | None = None  # set when the input image was a crop


# --- pure conversion ---------------------------------------------------------


def pose_to_observations(raw: RawPose, image_wh: tuple[int, int], cfg: DetectorConfig) -> tuple[Landmark2DObservation, ...]:
    w, h = image_wh
    arr = np.asarray(raw.landmarks_norm, dtype=np.float64)
    if arr.shape != (POSE_LANDMARK_COUNT, 5):
        raise ValueError(f"pose landmarks must be {POSE_LANDMARK_COUNT}x5, got {arr.shape}")
    out = []
    for name, (x, y, z, vis, pres) in zip(POSE_LANDMARK_NAMES, arr, strict=True):
        px, py = float(x * w), float(y * h)
        inside = 0.0 <= px < w and 0.0 <= py < h
        valid = bool(
            inside and np.isfinite([px, py]).all() and vis >= cfg.min_landmark_visibility and pres >= cfg.min_landmark_presence
        )
        out.append(
            Landmark2DObservation(
                name=name,
                xy_px=(px, py),
                z_model=float(z) if np.isfinite(z) else None,
                visibility=float(vis) if np.isfinite(vis) else None,
                presence=float(pres) if np.isfinite(pres) else None,
                valid=valid,
            )
        )
    return tuple(out)


def hand_to_candidate(raw: RawHand, image_wh: tuple[int, int]) -> HandCandidate:
    """Map normalized hand landmarks to full-image pixels (through the crop affine if any)."""
    arr = np.asarray(raw.landmarks_norm, dtype=np.float64)
    if arr.shape != (HAND_LANDMARK_COUNT, 3):
        raise ValueError(f"hand landmarks must be {HAND_LANDMARK_COUNT}x3, got {arr.shape}")
    w, h = image_wh
    lms = []
    for name, (x, y, z) in zip(HAND_LANDMARK_NAMES, arr, strict=True):
        if raw.crop is not None:
            px, py = raw.crop.to_full((float(x), float(y)))
        else:
            px, py = float(x * w), float(y * h)
        inside = 0.0 <= px < w and 0.0 <= py < h and bool(np.isfinite([px, py]).all())
        lms.append(Landmark2DObservation(name, (px, py), float(z) if np.isfinite(z) else None, None, None, inside))
    return HandCandidate(
        landmarks=tuple(lms),
        handedness_label=raw.handedness_label,
        handedness_score=raw.handedness_score,
        world_prior_m=raw.world_m,
        crop=raw.crop,
    )


def body_arms(body: tuple[Landmark2DObservation, ...]) -> dict[Side, BodyArm]:
    by_name = {lm.name: lm for lm in body}

    def _xy(n: str) -> tuple[float, float] | None:
        lm = by_name.get(n)
        return lm.xy_px if lm is not None and lm.valid else None

    return {
        side: BodyArm(
            side,
            wrist_xy=_xy(f"{side}_wrist"),
            elbow_xy=_xy(f"{side}_elbow"),
            wrist_visibility=(by_name[f"{side}_wrist"].visibility if f"{side}_wrist" in by_name else None),
        )
        for side in SIDES
    }


def hands_need_crops(candidates: list[HandCandidate], arms: dict[Side, BodyArm], cfg: DetectorConfig) -> list[Side]:
    """Sides whose body wrist is visible but has no full-frame hand nearby."""
    if not cfg.crop_fallback:
        return []
    assoc = associate_hands(candidates, arms, cfg.association)
    return [s for s in SIDES if arms[s].wrist_xy is not None and s not in assoc]


def build_view_detection(
    frame: CapturedFrame,
    *,
    person_mask: NDArray[np.bool_],
    body: tuple[Landmark2DObservation, ...],
    candidates: list[HandCandidate],
    pose_world_prior_m: NDArray[np.float32] | None,
    cfg: DetectorConfig,
) -> ViewDetection:
    arms = body_arms(body)
    assoc = associate_hands(candidates, arms, cfg.association)
    hands: dict[Side, tuple[Landmark2DObservation, ...]] = {s: () for s in SIDES}
    priors: dict[str, NDArray[np.float32]] = {}
    for side, a in assoc.items():
        cand = candidates[a.candidate_index]
        hands[side] = cand.landmarks
        if cand.world_prior_m is not None:
            priors[side] = np.asarray(cand.world_prior_m, dtype=np.float32)
    return ViewDetection(
        device_id=frame.device_id,
        capture_id=frame.capture_id,
        person_mask=np.ascontiguousarray(person_mask, dtype=np.bool_),
        body=body,
        left_hand=hands["left"],
        right_hand=hands["right"],
        pose_world_prior_m=None if pose_world_prior_m is None else np.asarray(pose_world_prior_m, np.float32),
        hand_world_priors_m=priors,
    )


def empty_detection(frame: CapturedFrame) -> ViewDetection:
    h, w = frame.rgb.shape[:2]
    return ViewDetection(frame.device_id, frame.capture_id, np.zeros((h, w), np.bool_), (), (), (), None)


# --- MediaPipe adapter -------------------------------------------------------


def _raw_pose_from_mp(result: Any, cfg: DetectorConfig) -> RawPose | None:
    if not result.pose_landmarks:
        return None
    lms = result.pose_landmarks[0]
    arr = np.array(
        [[lm.x, lm.y, lm.z, (lm.visibility if lm.visibility is not None else np.nan), (lm.presence if lm.presence is not None else np.nan)] for lm in lms],
        dtype=np.float64,
    )
    world = None
    if result.pose_world_landmarks:
        world = np.array([[lm.x, lm.y, lm.z] for lm in result.pose_world_landmarks[0]], dtype=np.float32)
    mask = None
    if result.segmentation_masks:
        mask = np.asarray(result.segmentation_masks[0].numpy_view(), dtype=np.float32)
    return RawPose(arr, world, mask)


def _raw_hands_from_mp(result: Any, crop: HandCrop | None = None) -> list[RawHand]:
    out: list[RawHand] = []
    if not result.hand_landmarks:
        return out
    n = len(result.hand_landmarks)
    for i in range(n):
        lms = np.array([[lm.x, lm.y, lm.z] for lm in result.hand_landmarks[i]], dtype=np.float64)
        label = score = None
        if result.handedness and i < len(result.handedness) and result.handedness[i]:
            cat = result.handedness[i][0]
            label = getattr(cat, "category_name", None) or getattr(cat, "display_name", None)
            score = float(cat.score) if getattr(cat, "score", None) is not None else None
        world = None
        if result.hand_world_landmarks and i < len(result.hand_world_landmarks):
            world = np.array([[lm.x, lm.y, lm.z] for lm in result.hand_world_landmarks[i]], dtype=np.float32)
        out.append(RawHand(lms, label, score, world, crop))
    return out


class MediaPipeViewDetector:
    """Long-lived MediaPipe Pose + Hand landmarkers implementing ``ViewDetector``.

    Construct and use on the single processing thread only; MediaPipe task
    objects are not thread-safe and must never be touched from the event loop.
    """

    def __init__(self, models: ResolvedModels, cfg: DetectorConfig | None = None) -> None:
        self._cfg = cfg or DetectorConfig()
        self._models = models
        # MediaPipe task objects; untyped because the import is deferred.
        self._pose: Any = None
        self._hands: Any = None
        self._mp: Any = None

    @property
    def models(self) -> ResolvedModels:
        return self._models

    def model_status(self) -> dict[str, str]:
        """Short, non-secret description for ``/health``."""
        return {
            "pose": f"mediapipe:{self._models.pose_sha256[:12]}",
            "hands": f"mediapipe:{self._models.hand_sha256[:12]}",
        }

    # Lazy so importing this module never imports mediapipe (tests, fake mode).
    def _ensure(self) -> None:
        if self._pose is not None:
            return
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision

        c = self._cfg
        self._pose = mp_vision.PoseLandmarker.create_from_options(
            mp_vision.PoseLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=str(self._models.pose_path)),
                running_mode=mp_vision.RunningMode.IMAGE,
                num_poses=1,
                min_pose_detection_confidence=c.min_pose_detection_confidence,
                min_pose_presence_confidence=c.min_pose_presence_confidence,
                min_tracking_confidence=c.min_tracking_confidence,
                output_segmentation_masks=True,
            )
        )
        self._hands = mp_vision.HandLandmarker.create_from_options(
            mp_vision.HandLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=str(self._models.hand_path)),
                running_mode=mp_vision.RunningMode.IMAGE,
                num_hands=c.num_hands,
                min_hand_detection_confidence=c.min_hand_detection_confidence,
                min_hand_presence_confidence=c.min_hand_presence_confidence,
                min_tracking_confidence=c.min_tracking_confidence,
            )
        )
        self._mp = mp

    def close(self) -> None:
        for obj in (self._pose, self._hands):
            if obj is not None:
                obj.close()
        self._pose = self._hands = None

    def _mp_image(self, rgb: NDArray[np.uint8]):
        mp = self._mp
        return mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb, dtype=np.uint8))

    def detect_view(self, frame: CapturedFrame) -> ViewDetection:
        self._ensure()
        cfg = self._cfg
        rgb = frame.rgb
        h, w = rgb.shape[:2]
        image = self._mp_image(rgb)

        raw_pose = _raw_pose_from_mp(self._pose.detect(image), cfg)
        if raw_pose is None:
            return empty_detection(frame)
        body = pose_to_observations(raw_pose, (w, h), cfg)
        if raw_pose.mask is not None and raw_pose.mask.shape == (h, w):
            person_mask = raw_pose.mask >= cfg.mask_threshold
        else:
            person_mask = np.zeros((h, w), np.bool_)

        # Full-resolution hand pass first.
        raw_hands = _raw_hands_from_mp(self._hands.detect(image))
        candidates = [hand_to_candidate(rh, (w, h)) for rh in raw_hands]

        # Crop fallback for sides whose wrist is visible but has no hand nearby.
        arms = body_arms(body)
        for side in hands_need_crops(candidates, arms, cfg):
            arm = arms[side]
            if arm.wrist_xy is None:
                continue
            crop = wrist_crop(arm.wrist_xy, arm.elbow_xy, (w, h))
            patch = crop.extract(rgb)
            for rh in _raw_hands_from_mp(self._hands.detect(self._mp_image(patch)), crop):
                candidates.append(hand_to_candidate(rh, (w, h)))

        return build_view_detection(
            frame,
            person_mask=person_mask,
            body=body,
            candidates=candidates,
            pose_world_prior_m=raw_pose.world_m,
            cfg=cfg,
        )

