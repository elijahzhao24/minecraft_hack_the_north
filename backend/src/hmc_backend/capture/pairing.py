"""Collect one- or two-device frames into coordinated capture groups.

Each device has a bounded deque of recently received, clock-normalized frames.
For a one-device request the frame is emitted immediately. For two devices the
collector finds the closest matching frame from the other device and enforces
the normalized-time skew budget. A consumed frame is never reused.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from uuid import UUID, uuid4

from hmc_backend.contracts.internal import CapturedFrame, CaptureGroup


@dataclass(frozen=True, slots=True)
class PairOutcome:
    """Result of offering one frame to the pairer."""

    paired: CaptureGroup | None
    rejected_reason: str | None = None


class Pairer:
    """One- or two-device frame collector. Owned by the event loop."""

    def __init__(
        self,
        device_ids: tuple[str, ...],
        *,
        pair_skew_limit_ms: float,
        clock_uncertainty_limit_ms: float,
        require_capture_id: bool = True,
        window: int = 8,
    ) -> None:
        if not 1 <= len(device_ids) <= 2 or len(set(device_ids)) != len(device_ids):
            raise ValueError("Pairer requires one or two distinct device IDs")
        self._device_ids = device_ids
        self._skew_limit_ms = pair_skew_limit_ms
        self._uncertainty_limit_ms = clock_uncertainty_limit_ms
        self._require_capture_id = require_capture_id
        self._queues: dict[str, deque[CapturedFrame]] = {
            device_id: deque(maxlen=window) for device_id in device_ids
        }

    def offer(
        self,
        frame: CapturedFrame,
        calibration_id: UUID,
        *,
        required_device_ids: tuple[str, ...] | None = None,
    ) -> PairOutcome:
        """Offer a clock-normalized frame; maybe emit a pair.

        ``calibration_id`` is the active rig calibration; it is stamped on the
        emitted pair so downstream stages cannot mix calibrations.
        """
        if frame.device_id not in self._queues:
            return PairOutcome(None, "unauthorized_device")
        if frame.clock_uncertainty_ms > self._uncertainty_limit_ms:
            return PairOutcome(None, "clock_uncertainty_exceeded")

        required = required_device_ids or self._device_ids
        if (
            not 1 <= len(required) <= 2
            or len(set(required)) != len(required)
            or any(device_id not in self._queues for device_id in required)
        ):
            return PairOutcome(None, "invalid_capture_devices")
        if frame.device_id not in required:
            return PairOutcome(None, "unexpected_capture_device")
        if len(required) == 1:
            return PairOutcome(self._build_group((frame,), calibration_id), None)

        other_id = next(device_id for device_id in required if device_id != frame.device_id)
        candidates = self._queues[other_id]

        best: CapturedFrame | None = None
        best_skew_ms = float("inf")
        for cand in candidates:
            if self._require_capture_id and cand.capture_id != frame.capture_id:
                continue
            skew_ms = abs(cand.normalized_capture_time_s - frame.normalized_capture_time_s) * 1000.0
            if skew_ms < best_skew_ms:
                best_skew_ms = skew_ms
                best = cand

        if best is not None and best_skew_ms <= self._skew_limit_ms:
            candidates.remove(best)
            ordered = tuple(
                candidate
                for device_id in self._device_ids
                for candidate in (frame, best)
                if candidate.device_id == device_id
            )
            return PairOutcome(self._build_group(ordered, calibration_id), None)

        # No acceptable partner yet: retain this frame in its own queue.
        self._queues[frame.device_id].append(frame)
        reason = "pair_skew_exceeded" if best is not None else None
        return PairOutcome(None, reason)

    def _build_group(self, frames: tuple[CapturedFrame, ...], calibration_id: UUID) -> CaptureGroup:
        times = [frame.normalized_capture_time_s for frame in frames]
        return CaptureGroup(
            group_id=uuid4(),
            frames=frames,
            normalized_capture_time_s=sum(times) / len(times),
            pair_skew_ms=(max(times) - min(times)) * 1000.0,
            calibration_id=calibration_id,
        )

    def clear_device(self, device_id: str) -> None:
        """Drop pending candidates for a device (e.g. on reconnect)."""
        if device_id in self._queues:
            self._queues[device_id].clear()

    def pending_depth(self, device_id: str) -> int:
        return len(self._queues.get(device_id, ()))
