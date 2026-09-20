"""Opt-in Sentry demonstrations; never imported by the production pipeline."""

from __future__ import annotations

import argparse
import os
from uuid import uuid4

from hmc_backend.calibration.synthetic import build_synthetic_rig
from hmc_backend.capture.pairing import Pairer
from hmc_backend.capture.replay import Replayer
from hmc_backend.fixtures.scene import build_capture_packets
from hmc_backend.observability import configure_sentry, flush_sentry, transaction
from hmc_backend.observability.quality import QualityMonitor, ViewQuality
from hmc_backend.pipeline.factory import build_processor, new_snapshot_store
from hmc_backend.settings import Settings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=("delay", "quality"))
    parser.add_argument("--delay-ms", type=int, default=750)
    args = parser.parse_args()
    configure_sentry(
        os.getenv("HMC_SENTRY_DSN"),
        environment=os.getenv("HMC_ENVIRONMENT", "observability-demo"),
        release="hmc-backend@observability-demo",
        traces_sample_rate=1.0,
    )
    try:
        if args.case == "delay":
            return demo_delay(args.delay_ms)
        return demo_quality()
    finally:
        flush_sentry()


def demo_delay(delay_ms: int) -> int:
    settings = Settings(observability_demo_delay_ms=delay_ms)
    rig = build_synthetic_rig(rgb_size=(80, 60), depth_size=(80, 60))
    pairer = Pairer(
        tuple(settings.expected_device_ids),  # type: ignore[arg-type]
        pair_skew_limit_ms=settings.pair_skew_limit_ms,
        clock_uncertainty_limit_ms=settings.clock_uncertainty_limit_ms,
    )
    replay = Replayer(rig, pairer, build_processor(settings, rig), new_snapshot_store())
    packets = list(build_capture_packets(rig, capture_id=uuid4(), seed=7).values())
    with transaction("observability.demo.delay", op="hmc.demo", demo_case="delay"):
        result = replay.feed_all(packets)
    print(f"fixture frames={result.published}; injected_delay_ms={delay_ms}")
    print("Expected evidence: hmc.calibration_fusion duration includes the injected delay.")
    return 0 if result.published == 1 else 1


def demo_quality() -> int:
    monitor = QualityMonitor(consecutive_frames=3, repeat_interval_s=60)
    calibration = uuid4()
    fusion = uuid4()
    contaminated = ViewQuality(
        camera_id="front-phone",
        background_proportion=0.82,
        hand_coverage=0.0,
        foot_coverage=1.0,
        torso_coverage=1.0,
    )
    for frame_id in range(1, 4):
        monitor.observe(
            frame_id=frame_id,
            fusion_id=fusion,
            calibration_id=calibration,
            quality=contaminated,
        )
    print("fixture emitted scan_quality_poor on frame 3: excessive background + missing hands")
    print("Expected evidence: one warning with camera, calibration, fusion and quality attributes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
