from __future__ import annotations

from uuid import uuid4

import numpy as np

from hmc_backend.contracts.internal import CapturedFrame, Landmark2DObservation, ViewDetection
from hmc_backend.observability.quality import QualityMonitor, ViewQuality, measure_view_quality


def _frame() -> CapturedFrame:
    return CapturedFrame(
        "front-phone",
        uuid4(),
        uuid4(),
        1,
        0.0,
        0.0,
        1.0,
        np.zeros((20, 20, 3), np.uint8),
        np.ones((20, 20), np.float32),
        np.full((20, 20), 2, np.uint8),
        np.eye(3),
        np.eye(4),
    )


def _landmark(name: str, xy: tuple[float, float]) -> Landmark2DObservation:
    return Landmark2DObservation(name, xy, None, 1.0, 1.0, True)


def test_quality_distinguishes_background_and_missing_hand_support() -> None:
    frame = _frame()
    mask = np.zeros((20, 20), np.bool_)
    mask[5:15, 5:15] = True
    detection = ViewDetection(
        frame.device_id,
        frame.capture_id,
        mask,
        (
            _landmark("left_shoulder", (8, 8)),
            _landmark("right_shoulder", (12, 8)),
            _landmark("left_heel", (8, 14)),
        ),
        (_landmark("left_wrist", (1, 1)),),
        (),
        None,
    )

    quality = measure_view_quality(frame, detection, confidence_min=1)
    assert quality.background_proportion == 0.75
    assert quality.hand_coverage == 0.0
    assert quality.foot_coverage == 1.0
    assert quality.torso_coverage == 1.0


def test_unreliable_or_out_of_view_landmark_is_unavailable_not_failure() -> None:
    frame = _frame()
    unreliable = Landmark2DObservation("left_wrist", (10, 10), None, 0.1, 0.1, True)
    detection = ViewDetection(
        frame.device_id,
        frame.capture_id,
        np.ones((20, 20), np.bool_),
        (),
        (unreliable,),
        (),
        None,
    )
    quality = measure_view_quality(frame, detection, confidence_min=1)
    assert quality.hand_coverage is None
    assert quality.foot_coverage is None
    assert quality.torso_coverage is None


def test_monitor_warns_once_after_streak_and_records_recovery(monkeypatch) -> None:
    warnings: list[dict] = []
    recoveries: list[dict] = []
    monkeypatch.setattr(
        "hmc_backend.observability.quality.capture_warning",
        lambda _message, **attributes: warnings.append(attributes),
    )
    monkeypatch.setattr(
        "hmc_backend.observability.quality.log_event",
        lambda _level, _message, **attributes: recoveries.append(attributes),
    )
    monitor = QualityMonitor(consecutive_frames=3, repeat_interval_s=60, monotonic=lambda: 10.0)
    calibration = uuid4()
    fusion = uuid4()
    poor = ViewQuality("front-phone", 0.8, 0.0, None, 1.0)
    for frame_id in range(1, 5):
        monitor.observe(
            frame_id=frame_id,
            fusion_id=fusion,
            calibration_id=calibration,
            quality=poor,
        )
    assert len(warnings) == 1
    assert warnings[0]["frame_id"] == 3
    assert warnings[0]["quality_signals"] == "excess_background,missing_hands"

    monitor.observe(
        frame_id=5,
        fusion_id=fusion,
        calibration_id=calibration,
        quality=ViewQuality("front-phone", 0.1, 1.0, None, 1.0),
    )
    assert len(recoveries) == 1


def test_monitor_does_not_treat_unavailable_coverage_as_recovery(monkeypatch) -> None:
    warnings: list[dict] = []
    recoveries: list[dict] = []
    monkeypatch.setattr(
        "hmc_backend.observability.quality.capture_warning",
        lambda _message, **attributes: warnings.append(attributes),
    )
    monkeypatch.setattr(
        "hmc_backend.observability.quality.log_event",
        lambda _level, _message, **attributes: recoveries.append(attributes),
    )
    monitor = QualityMonitor(consecutive_frames=1, monotonic=lambda: 10.0)
    calibration = uuid4()
    fusion = uuid4()
    monitor.observe(
        frame_id=1,
        fusion_id=fusion,
        calibration_id=calibration,
        quality=ViewQuality("front-phone", 0.1, 0.0, None, 1.0),
    )
    monitor.observe(
        frame_id=2,
        fusion_id=fusion,
        calibration_id=calibration,
        quality=ViewQuality("front-phone", 0.1, None, None, 1.0),
    )
    assert len(warnings) == 1
    assert recoveries == []
