"""Software clock-offset estimation between a phone and the backend.

Phone capture timestamps use the phone's monotonic clock; the backend uses its
own monotonic clock. They are never compared directly. Instead the backend
periodically probes each phone and estimates the phone-minus-backend offset from
round-trip samples, normalizing capture times into the backend clock domain.

For backend send/receive times ``t0, t3`` and phone receive/send times
``t1, t2`` (all seconds)::

    offset = ((t1 - t0) + (t2 - t3)) / 2      # phone minus backend
    delay  = (t3 - t0) - (t2 - t1)            # round-trip network delay

Low-delay samples are the most trustworthy, so a bounded set of them is kept and
the offset/uncertainty are derived from that set.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ClockSample:
    """One completed round-trip probe."""

    request_id: str
    offset_s: float
    delay_s: float


def compute_offset_and_delay(t0: float, t1: float, t2: float, t3: float) -> tuple[float, float]:
    """Return ``(offset_s, delay_s)`` for one round trip.

    ``offset_s`` is phone-minus-backend; subtract it from a phone timestamp to
    convert into the backend clock domain.
    """
    offset = ((t1 - t0) + (t2 - t3)) / 2.0
    delay = (t3 - t0) - (t2 - t1)
    return offset, delay


class ClockEstimator:
    """Maintains a bounded set of low-delay samples for one device session.

    Not thread-safe; owned by the FastAPI event loop.
    """

    def __init__(self, *, keep: int = 8, max_delay_s: float = 0.25) -> None:
        self._keep = keep
        self._max_delay_s = max_delay_s
        # Ordered by ascending delay; best (lowest-delay) samples retained.
        self._samples: list[ClockSample] = []
        self._pending: deque[str] = deque(maxlen=64)

    def record_ping(self, request_id: str) -> None:
        """Note that a probe was sent so a later pong can be matched."""
        self._pending.append(request_id)

    def record_pong(
        self, request_id: str, t0: float, t1: float, t2: float, t3: float
    ) -> ClockSample | None:
        """Incorporate a completed round trip; returns the sample if accepted.

        Samples with implausible (negative) or excessive delay are rejected.
        """
        offset, delay = compute_offset_and_delay(t0, t1, t2, t3)
        if delay < 0 or delay > self._max_delay_s:
            return None
        sample = ClockSample(request_id=request_id, offset_s=offset, delay_s=delay)
        self._samples.append(sample)
        # Keep only the lowest-delay samples.
        self._samples.sort(key=lambda s: s.delay_s)
        del self._samples[self._keep :]
        return sample

    @property
    def ready(self) -> bool:
        """True once at least one accepted sample exists."""
        return bool(self._samples)

    def ready_with(self, minimum_samples: int, maximum_uncertainty_ms: float) -> bool:
        uncertainty = self.uncertainty_ms()
        return (
            self.sample_count >= minimum_samples
            and uncertainty is not None
            and uncertainty <= maximum_uncertainty_ms
        )

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    def offset_s(self) -> float | None:
        """Median offset over retained low-delay samples (None if not ready)."""
        if not self._samples:
            return None
        offsets = sorted(s.offset_s for s in self._samples)
        mid = len(offsets) // 2
        if len(offsets) % 2:
            return offsets[mid]
        return (offsets[mid - 1] + offsets[mid]) / 2.0

    def uncertainty_ms(self) -> float | None:
        """Uncertainty estimate in milliseconds.

        Uses half the spread of retained offsets, floored by the best sample's
        one-way delay, so a single sample still reports a non-zero bound.
        """
        if not self._samples:
            return None
        offsets = [s.offset_s for s in self._samples]
        spread_ms = (max(offsets) - min(offsets)) * 1000.0 / 2.0
        best_one_way_ms = min(s.delay_s for s in self._samples) * 1000.0 / 2.0
        return max(spread_ms, best_one_way_ms)

    def normalize(self, phone_time_s: float) -> float | None:
        """Convert a phone timestamp into the backend clock domain."""
        offset = self.offset_s()
        if offset is None:
            return None
        return phone_time_s - offset
