"""Pair frames from the two devices within the skew budget.

Each device has a bounded deque of recently received, clock-normalized frames.
When a new frame arrives, the pairer looks for the closest-in-time frame from
the *other* device (sharing the same ``capture_id`` for explicit snapshots). If
their normalized-time skew is within budget a single ``PairedFrames`` is emitted
and both frames are consumed; a consumed frame is never reused.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from uuid import UUID, uuid4

from hmc_backend.contracts.internal import CapturedFrame, PairedFrames


@dataclass(frozen=True, slots=True)
class PairOutcome:
    """Result of offering one frame to the pairer."""

    paired: PairedFrames | None
    rejected_reason: str | None = None


class Pairer:
    """Two-device frame pairer. Owned by the event loop; not thread-safe."""

    def __init__(
        self,
        device_ids: tuple[str, str],
        *,
        pair_skew_limit_ms: float,
        clock_uncertainty_limit_ms: float,
        require_capture_id: bool = True,
        window: int = 8,
    ) -> None:
        if len(device_ids) != 2 or device_ids[0] == device_ids[1]:
            raise ValueError("Pairer requires two distinct device IDs")
        self._device_ids = device_ids
        self._skew_limit_ms = pair_skew_limit_ms
        self._uncertainty_limit_ms = clock_uncertainty_limit_ms
        self._require_capture_id = require_capture_id
        self._queues: dict[str, deque[CapturedFrame]] = {
            device_ids[0]: deque(maxlen=window),
            device_ids[1]: deque(maxlen=window),
        }

    def _other(self, device_id: str) -> str:
        a, b = self._device_ids
        return b if device_id == a else a

    def offer(self, frame: CapturedFrame, calibration_id: UUID) -> PairOutcome:
        """Offer a clock-normalized frame; maybe emit a pair.

        ``calibration_id`` is the active rig calibration; it is stamped on the
        emitted pair so downstream stages cannot mix calibrations.
        """
        if frame.device_id not in self._queues:
            return PairOutcome(None, "unauthorized_device")
        if frame.clock_uncertainty_ms > self._uncertainty_limit_ms:
            return PairOutcome(None, "clock_uncertainty_exceeded")

        other_id = self._other(frame.device_id)
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
            pair = self._build_pair(frame, best, best_skew_ms, calibration_id)
            return PairOutcome(pair, None)

        # No acceptable partner yet: retain this frame in its own queue.
        self._queues[frame.device_id].append(frame)
        reason = "pair_skew_exceeded" if best is not None else None
        return PairOutcome(None, reason)

    def _build_pair(
        self,
        arriving: CapturedFrame,
        partner: CapturedFrame,
        skew_ms: float,
        calibration_id: UUID,
    ) -> PairedFrames:
        # Order deterministically by configured device order (front first).
        first_id = self._device_ids[0]
        if arriving.device_id == first_id:
            first, second = arriving, partner
        else:
            first, second = partner, arriving
        midpoint = (first.normalized_capture_time_s + second.normalized_capture_time_s) / 2.0
        return PairedFrames(
            pair_id=uuid4(),
            first=first,
            second=second,
            normalized_capture_time_s=midpoint,
            pair_skew_ms=skew_ms,
            calibration_id=calibration_id,
        )

    def clear_device(self, device_id: str) -> None:
        """Drop pending candidates for a device (e.g. on reconnect)."""
        if device_id in self._queues:
            self._queues[device_id].clear()

    def pending_depth(self, device_id: str) -> int:
        return len(self._queues.get(device_id, ()))
