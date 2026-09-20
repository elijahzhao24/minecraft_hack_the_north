"""Cheap scan-quality signals derived from data the pipeline already owns."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID

import numpy as np

from hmc_backend.contracts.internal import CapturedFrame, Landmark2DObservation, ViewDetection
from hmc_backend.observability.sentry import capture_warning, log_event


@dataclass(frozen=True, slots=True)
class ViewQuality:
    camera_id: str
    background_proportion: float | None
    hand_coverage: float | None
    foot_coverage: float | None
    torso_coverage: float | None

    def attributes(self) -> dict[str, str | float | None]:
        return {
            "camera_id": self.camera_id,
            "background_proportion": self.background_proportion,
            "hand_coverage": self.hand_coverage,
            "foot_coverage": self.foot_coverage,
            "torso_coverage": self.torso_coverage,
        }


def measure_view_quality(
    frame: CapturedFrame,
    detection: ViewDetection,
    *,
    confidence_min: int,
) -> ViewQuality:
    """Estimate background and body support without another model pass.

    The person mask is a heuristic produced by the existing detector. Coverage
    is unavailable when no reliable, in-view landmark exists for that region.
    """
    depth = frame.depth_m
    valid_depth = np.isfinite(depth) & (depth > 0) & (frame.confidence >= confidence_min)
    mask_depth = _resize_mask(detection.person_mask, depth.shape)
    valid_count = int(np.count_nonzero(valid_depth))
    background = (
        float(np.count_nonzero(valid_depth & ~mask_depth) / valid_count)
        if valid_count
        else None
    )

    body = tuple(detection.body)
    hand_observations = tuple(detection.left_hand) + tuple(detection.right_hand)
    if not hand_observations:
        hand_observations = tuple(
            item for item in body if _name_has(item.name, "wrist", "thumb", "index", "pinky")
        )
    feet = tuple(item for item in body if _name_has(item.name, "ankle", "heel", "foot", "toe"))
    torso = tuple(item for item in body if _name_has(item.name, "shoulder", "hip", "pelvis"))

    rgb_shape = frame.rgb.shape[:2]
    return ViewQuality(
        camera_id=frame.device_id,
        background_proportion=background,
        hand_coverage=_coverage(hand_observations, valid_depth & mask_depth, rgb_shape),
        foot_coverage=_coverage(feet, valid_depth & mask_depth, rgb_shape),
        torso_coverage=_coverage(torso, valid_depth & mask_depth, rgb_shape),
    )


def _resize_mask(mask: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    h, w = shape
    mh, mw = mask.shape
    ys = np.clip((np.arange(h) * (mh / h)).astype(np.int64), 0, mh - 1)
    xs = np.clip((np.arange(w) * (mw / w)).astype(np.int64), 0, mw - 1)
    return mask[np.ix_(ys, xs)]


def _name_has(name: str, *tokens: str) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in tokens)


def _coverage(
    observations: tuple[Landmark2DObservation, ...],
    supported_depth: np.ndarray,
    rgb_shape: tuple[int, int],
) -> float | None:
    h_rgb, w_rgb = rgb_shape
    h_depth, w_depth = supported_depth.shape
    reliable: list[tuple[int, int]] = []
    for item in observations:
        if not item.valid:
            continue
        if item.visibility is not None and item.visibility < 0.5:
            continue
        if item.presence is not None and item.presence < 0.5:
            continue
        x, y = item.xy_px
        if not (0 <= x < w_rgb and 0 <= y < h_rgb):
            continue
        u = min(w_depth - 1, int(x * w_depth / w_rgb))
        v = min(h_depth - 1, int(y * h_depth / h_rgb))
        reliable.append((u, v))
    if not reliable:
        return None

    radius = max(1, round(min(h_depth, w_depth) * 0.025))
    supported = 0
    for u, v in reliable:
        y0, y1 = max(0, v - radius), min(h_depth, v + radius + 1)
        x0, x1 = max(0, u - radius), min(w_depth, u + radius + 1)
        supported += bool(np.any(supported_depth[y0:y1, x0:x1]))
    return supported / len(reliable)


@dataclass(slots=True)
class _SignalState:
    poor_frames: int = 0
    warning_active: bool = False
    last_warning_s: float = -float("inf")
    warned_signals: set[str] = field(default_factory=set)


class QualityMonitor:
    """Warn after a short poor-quality streak, then record recovery once."""

    def __init__(
        self,
        *,
        consecutive_frames: int = 3,
        repeat_interval_s: float = 60.0,
        background_limit: float = 0.35,
        coverage_limit: float = 0.5,
        monotonic: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._consecutive_frames = consecutive_frames
        self._repeat_interval_s = repeat_interval_s
        self._background_limit = background_limit
        self._coverage_limit = coverage_limit
        self._monotonic = monotonic
        self._states: dict[tuple[str, UUID], _SignalState] = {}

    def observe(
        self,
        *,
        frame_id: int,
        fusion_id: UUID,
        calibration_id: UUID,
        quality: ViewQuality,
    ) -> None:
        signals = {
            "excess_background": (
                quality.background_proportion is not None
                and quality.background_proportion > self._background_limit
            ),
            "missing_hands": self._below(quality.hand_coverage),
            "missing_feet": self._below(quality.foot_coverage),
            "missing_torso": self._below(quality.torso_coverage),
        }
        now = self._monotonic()
        poor_signals = sorted(signal for signal, poor in signals.items() if poor is True)
        key = (quality.camera_id, calibration_id)
        state = self._states.setdefault(key, _SignalState())
        if poor_signals:
            state.poor_frames += 1
            due = now - state.last_warning_s >= self._repeat_interval_s
            if state.poor_frames >= self._consecutive_frames and due:
                capture_warning(
                    "scan_quality_poor",
                    frame_id=frame_id,
                    fusion_id=str(fusion_id),
                    calibration_version=str(calibration_id),
                    quality_signals=",".join(poor_signals),
                    consecutive_frames=state.poor_frames,
                    **quality.attributes(),
                )
                state.warning_active = True
                state.last_warning_s = now
                state.warned_signals = set(poor_signals)
        else:
            # An unreliable or out-of-view landmark makes coverage unavailable;
            # it is not evidence that a previously missing region recovered.
            if state.warning_active and any(
                signals[signal] is None for signal in state.warned_signals
            ):
                return
            if state.warning_active:
                log_event(
                    "info",
                    "scan_quality_recovered",
                    frame_id=frame_id,
                    fusion_id=str(fusion_id),
                    calibration_version=str(calibration_id),
                    **quality.attributes(),
                )
            state.poor_frames = 0
            state.warning_active = False
            state.warned_signals.clear()

    def _below(self, value: float | None) -> bool | None:
        return None if value is None else value < self._coverage_limit
