"""Vertical slice: synthetic capture -> real decode/pair/process -> CharacterFrame.

Generates a synthetic two-camera capture, records it as immutable ``.hmc``
packets, replays them through the production decoder/pairer/processor, and
writes the published ``CHARACTER_FRAME`` to disk.

Run from ``backend/``::

    uv run python scripts/run_vertical_slice.py
"""

from __future__ import annotations

import argparse
from pathlib import Path
from uuid import uuid4

from hmc_backend.calibration.model import rig_to_json
from hmc_backend.calibration.synthetic import build_synthetic_rig
from hmc_backend.capture.pairing import Pairer
from hmc_backend.capture.recording import load_recording, save_recording
from hmc_backend.capture.replay import Replayer
from hmc_backend.fixtures.scene import build_capture_packets
from hmc_backend.pipeline.factory import build_processor, new_snapshot_store
from hmc_backend.settings import Settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Workflow 2 vertical slice.")
    parser.add_argument("--out", default="data/vertical_slice", help="output directory")
    parser.add_argument("--raster", type=int, nargs=2, default=(240, 180), metavar=("W", "H"))
    args = parser.parse_args()

    out_dir = Path(args.out)
    recordings_dir = out_dir / "recordings"
    w, h = args.raster

    settings = Settings()
    rig = build_synthetic_rig(rgb_size=(w, h), depth_size=(w, h))
    capture_id = uuid4()
    packets = build_capture_packets(rig, capture_id=capture_id, seed=7, splat=2)

    # 1. Record immutably (with sha256 manifest).
    capture_dir = save_recording(
        recordings_dir,
        capture_id,
        packets,
        calibration_json=rig_to_json(rig),
        consent_note="synthetic fixture; no personal data",
    )
    print(f"recorded capture {capture_id} -> {capture_dir}")

    # 2. Reload from disk (verifies checksums) and replay through the real path.
    recording = load_recording(capture_dir)
    pairer = Pairer(
        tuple(settings.expected_device_ids),  # type: ignore[arg-type]
        pair_skew_limit_ms=settings.pair_skew_limit_ms,
        clock_uncertainty_limit_ms=settings.clock_uncertainty_limit_ms,
    )
    processor = build_processor(settings, rig)
    store = new_snapshot_store()
    replayer = Replayer(rig, pairer, processor, store)

    result = replayer.feed_all(list(recording.packets.values()))
    if result.published == 0:
        print("ERROR: no CharacterFrame published")
        return 1

    frame = result.frames[-1]
    encoded = store.latest_encoded
    assert encoded is not None
    char_path = out_dir / "character_frame.hmc"
    char_path.write_bytes(encoded)

    print("published CharacterFrame:")
    print(f"  frame_id           = {frame.frame_id}")
    print(f"  calibration_id     = {frame.calibration_id}")
    print(f"  points             = {frame.quality.point_count}")
    print(f"  valid landmarks    = {frame.quality.valid_landmark_count}")
    print(f"  valid colliders    = {frame.quality.valid_collider_count}")
    print(f"  pair_skew_ms       = {frame.pair_skew_ms:.2f}")
    print(f"  source captures    = {[r.capture_id for r in frame.source_frames]}")
    print(f"  encoded bytes      = {len(encoded)}")
    print(f"  wrote              -> {char_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
