"""Replay recorded/synthetic packets through the real decode + pairing path.

The replayer decodes each HMC1 envelope with the production decoder, builds a
``CapturedFrame`` (stamping a normalized capture time from the supplied clock
offset), offers it to the ``Pairer``, and runs the ``CharacterProcessor`` on any
emitted pair. This exercises the same boundary the live capture loop uses,
rather than calling reconstruction directly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from hmc_backend.calibration.model import RigCalibration
from hmc_backend.capture.pairing import Pairer
from hmc_backend.capture.rgbd_ingest import DecodedRgbd, decode_rgbd_frame
from hmc_backend.contracts.character_codec import encode_character_frame
from hmc_backend.contracts.internal import CapturedFrame, CharacterFrame, TraceContext
from hmc_backend.pipeline.processor import CharacterProcessor
from hmc_backend.pipeline.snapshot_store import SnapshotStore
from hmc_backend.protocol.envelope import decode_envelope


@dataclass(frozen=True, slots=True)
class ReplayResult:
    frames: list[CharacterFrame]
    published: int


def captured_frame_from_decoded(
    decoded: DecodedRgbd,
    *,
    clock_offset_s: float = 0.0,
    clock_uncertainty_ms: float = 1.0,
) -> CapturedFrame:
    """Build a CapturedFrame from a decoded RGBD packet."""
    h = decoded.header
    return CapturedFrame(
        device_id=h.device_id,
        session_id=h.session_id,
        capture_id=h.capture_id,
        sequence=h.sequence,
        capture_timestamp_s=h.capture_timestamp_s,
        normalized_capture_time_s=h.capture_timestamp_s - clock_offset_s,
        clock_uncertainty_ms=clock_uncertainty_ms,
        rgb=decoded.rgb,
        depth_m=decoded.depth_m,
        confidence=decoded.confidence,
        K_rgb=decoded.k_rgb,
        arkit_pose=decoded.arkit_pose,
        source_frame_id=h.source_frame_id,
        trace=TraceContext(
            sentry_trace=h.trace.sentry_trace if h.trace else None,
            baggage=h.trace.baggage if h.trace else None,
        ),
        received_monotonic_s=time.perf_counter(),
    )


class Replayer:
    """Drives packets through decode -> pair -> process -> publish."""

    def __init__(
        self,
        calibration: RigCalibration,
        pairer: Pairer,
        processor: CharacterProcessor,
        store: SnapshotStore,
        *,
        mode: str = "snapshot",
    ) -> None:
        self._calibration = calibration
        self._pairer = pairer
        self._processor = processor
        self._store = store
        self._mode = mode

    def feed_packet(self, raw: bytes) -> CharacterFrame | None:
        """Decode one packet; return a published CharacterFrame if a pair completed."""
        env = decode_envelope(raw)
        decoded = decode_rgbd_frame(env)
        frame = captured_frame_from_decoded(decoded)

        outcome = self._pairer.offer(frame, self._calibration.calibration_id)
        if outcome.paired is None:
            return None

        character = self._processor.process(outcome.paired, mode=self._mode)
        encoded = encode_character_frame(character)
        # Publish only after successful serialization (all-or-nothing).
        self._store.publish(character, encoded)
        return character

    def feed_all(self, packets: list[bytes]) -> ReplayResult:
        """Feed a list of packets in order, returning all published frames."""
        frames: list[CharacterFrame] = []
        for raw in packets:
            result = self.feed_packet(raw)
            if result is not None:
                frames.append(result)
        return ReplayResult(frames=frames, published=len(frames))
