"""Fast soak test: repeated captures must not grow queues or leak state.

A condensed stand-in for the 10-minute soak gate. It drives many capture cycles
through the real decode/pair/process/publish path and asserts monotonic frame
IDs, bounded pairing state, and a single retained snapshot.
"""

from __future__ import annotations

from uuid import uuid4

from hmc_backend.calibration.synthetic import build_synthetic_rig
from hmc_backend.capture.pairing import Pairer
from hmc_backend.capture.replay import Replayer
from hmc_backend.fixtures.scene import build_capture_packets
from hmc_backend.pipeline.factory import build_processor, new_snapshot_store
from hmc_backend.settings import Settings


def test_many_captures_stay_bounded():
    rig = build_synthetic_rig(rgb_size=(96, 72), depth_size=(96, 72))
    settings = Settings()
    pairer = Pairer(
        tuple(settings.expected_device_ids),
        pair_skew_limit_ms=settings.pair_skew_limit_ms,
        clock_uncertainty_limit_ms=settings.clock_uncertainty_limit_ms,
    )
    processor = build_processor(settings, rig)
    store = new_snapshot_store()
    replayer = Replayer(rig, pairer, processor, store)

    cycles = 40
    last_frame_id = 0
    for _ in range(cycles):
        packets = build_capture_packets(rig, capture_id=uuid4(), seed=1, splat=1)
        result = replayer.feed_all(list(packets.values()))

        # Exactly one frame published per full pair.
        assert result.published == 1
        frame = result.frames[0]

        # Monotonic frame IDs, no skips.
        assert frame.frame_id == last_frame_id + 1
        last_frame_id = frame.frame_id

        # Pairing state does not accumulate: both frames were consumed.
        assert pairer.pending_depth("front-phone") == 0
        assert pairer.pending_depth("side-phone") == 0

    # Only the latest snapshot is retained, regardless of how many were published.
    assert store.latest_frame_id == cycles


def test_unpaired_frames_do_not_accumulate_unboundedly():
    """A stream of front-only frames stays within the pairer's bounded window."""
    rig = build_synthetic_rig(rgb_size=(64, 48), depth_size=(64, 48))
    settings = Settings()
    pairer = Pairer(
        tuple(settings.expected_device_ids),
        pair_skew_limit_ms=settings.pair_skew_limit_ms,
        clock_uncertainty_limit_ms=settings.clock_uncertainty_limit_ms,
        window=8,
    )
    processor = build_processor(settings, rig)
    store = new_snapshot_store()
    replayer = Replayer(rig, pairer, processor, store)

    for _ in range(50):
        packets = build_capture_packets(rig, capture_id=uuid4(), seed=1, splat=1)
        # Feed only the front phone; no pair can complete.
        assert replayer.feed_packet(packets["front-phone"]) is None

    # The bounded deque caps retained candidates at the window size.
    assert pairer.pending_depth("front-phone") <= 8
    assert store.latest_frame_id is None
