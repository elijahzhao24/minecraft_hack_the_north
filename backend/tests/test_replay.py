"""Recording round-trip and end-to-end replay (the vertical slice)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from hmc_backend.calibration.model import rig_to_json
from hmc_backend.calibration.synthetic import build_synthetic_rig
from hmc_backend.capture.pairing import Pairer
from hmc_backend.capture.recording import load_recording, save_capture_packet, save_recording
from hmc_backend.capture.replay import Replayer
from hmc_backend.contracts.character_codec import encode_character_frame
from hmc_backend.fixtures.scene import build_capture_packets
from hmc_backend.pipeline.factory import build_processor, new_snapshot_store
from hmc_backend.protocol.envelope import decode_envelope
from hmc_backend.settings import Settings


def _rig_and_packets(capture_id):
    rig = build_synthetic_rig(rgb_size=(160, 120), depth_size=(160, 120))
    packets = build_capture_packets(rig, capture_id=capture_id, seed=3, splat=2)
    return rig, packets


def _replayer(rig):
    settings = Settings()
    pairer = Pairer(
        tuple(settings.expected_device_ids),
        pair_skew_limit_ms=settings.pair_skew_limit_ms,
        clock_uncertainty_limit_ms=settings.clock_uncertainty_limit_ms,
    )
    processor = build_processor(settings, rig)
    store = new_snapshot_store()
    return Replayer(rig, pairer, processor, store), store


def test_recording_roundtrip_verifies_checksums(tmp_path):
    capture_id = uuid4()
    rig, packets = _rig_and_packets(capture_id)
    capture_dir = save_recording(tmp_path, capture_id, packets, calibration_json=rig_to_json(rig))
    rec = load_recording(capture_dir)
    assert rec.capture_id == capture_id
    assert set(rec.packets) == set(packets)


def test_recording_detects_corruption(tmp_path):
    capture_id = uuid4()
    _rig, packets = _rig_and_packets(capture_id)
    capture_dir = save_recording(tmp_path, capture_id, packets)
    # Corrupt one packet file.
    victim = next(capture_dir.glob("*.hmc"))
    victim.write_bytes(victim.read_bytes() + b"tampered")
    with pytest.raises(ValueError):
        load_recording(capture_dir)


def test_live_packets_incrementally_build_recording(tmp_path):
    capture_id = uuid4()
    _rig, packets = _rig_and_packets(capture_id)

    for device_id, raw in packets.items():
        capture_dir = save_capture_packet(tmp_path, capture_id, device_id, raw)

    recording = load_recording(capture_dir)
    assert recording.packets == packets


def test_live_packet_does_not_overwrite_different_bytes(tmp_path):
    capture_id = uuid4()
    _rig, packets = _rig_and_packets(capture_id)
    raw = packets["front-phone"]
    save_capture_packet(tmp_path, capture_id, "front-phone", raw)

    with pytest.raises(ValueError, match="different packet bytes"):
        save_capture_packet(tmp_path, capture_id, "front-phone", raw + b"changed")


def test_vertical_slice_publishes_one_character_frame():
    capture_id = uuid4()
    rig, packets = _rig_and_packets(capture_id)
    replayer, store = _replayer(rig)

    result = replayer.feed_all(list(packets.values()))

    assert result.published == 1
    frame = result.frames[0]
    assert frame.frame_id == 1
    assert frame.quality.point_count > 0
    assert frame.calibration_id == rig.calibration_id
    # Both source captures carry the same capture_id.
    assert {r.capture_id for r in frame.source_frames} == {capture_id}
    # Latest snapshot is the published frame and re-encodes byte-identically.
    assert store.latest_frame_id == 1
    assert store.latest_encoded == encode_character_frame(frame)
    # And it decodes as a valid CHARACTER_FRAME.
    env = decode_envelope(store.latest_encoded)
    assert env.header["schema"] == "hmc.character_frame"
    assert env.header["quality"]["point_count"] == frame.quality.point_count


def test_replay_is_deterministic():
    capture_id = uuid4()
    rig, packets = _rig_and_packets(capture_id)

    r1, s1 = _replayer(rig)
    r1.feed_all(list(packets.values()))
    r2, s2 = _replayer(rig)
    r2.feed_all(list(packets.values()))

    # The character-stream session_id is fresh per run, but the reconstructed
    # geometry must be identical: the packed point payload is byte-identical.
    e1 = decode_envelope(s1.latest_encoded)
    e2 = decode_envelope(s2.latest_encoded)
    assert e1.payload == e2.payload
    assert e1.header["quality"]["point_count"] == e2.header["quality"]["point_count"]
    assert e1.header["colliders"] == e2.header["colliders"]
