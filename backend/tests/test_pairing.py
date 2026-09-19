"""Tests for two-device frame pairing."""

from __future__ import annotations

from uuid import uuid4

import numpy as np

from hmc_backend.capture.pairing import Pairer
from hmc_backend.contracts.internal import CapturedFrame

DEVICES = ("front-phone", "side-phone")
CALIB = uuid4()


def _frame(
    device_id: str, capture_id, t_norm: float, *, uncertainty_ms: float = 5.0, seq: int = 1
) -> CapturedFrame:
    return CapturedFrame(
        device_id=device_id,
        session_id=uuid4(),
        capture_id=capture_id,
        sequence=seq,
        capture_timestamp_s=t_norm,
        normalized_capture_time_s=t_norm,
        clock_uncertainty_ms=uncertainty_ms,
        rgb=np.zeros((2, 2, 3), np.uint8),
        depth_m=np.zeros((2, 2), np.float32),
        confidence=np.full((2, 2), 2, np.uint8),
        K_rgb=np.eye(3),
        arkit_pose=np.eye(4),
    )


def _pairer(**kw) -> Pairer:
    return Pairer(DEVICES, pair_skew_limit_ms=50.0, clock_uncertainty_limit_ms=20.0, **kw)


def test_pairs_within_skew():
    p = _pairer()
    cid = uuid4()
    assert p.offer(_frame("front-phone", cid, 100.000), CALIB).paired is None
    out = p.offer(_frame("side-phone", cid, 100.020), CALIB)  # 20 ms skew
    assert out.paired is not None
    assert out.paired.pair_skew_ms < 50.0
    assert tuple(frame.device_id for frame in out.paired.frames) == DEVICES
    assert out.paired.calibration_id == CALIB


def test_rejects_skew_over_budget():
    p = _pairer()
    cid = uuid4()
    p.offer(_frame("front-phone", cid, 100.000), CALIB)
    out = p.offer(_frame("side-phone", cid, 100.200), CALIB)  # 200 ms skew
    assert out.paired is None
    assert out.rejected_reason == "pair_skew_exceeded"


def test_rejects_high_clock_uncertainty():
    p = _pairer()
    cid = uuid4()
    out = p.offer(_frame("front-phone", cid, 100.0, uncertainty_ms=50.0), CALIB)
    assert out.paired is None
    assert out.rejected_reason == "clock_uncertainty_exceeded"


def test_requires_matching_capture_id_for_snapshot():
    p = _pairer(require_capture_id=True)
    p.offer(_frame("front-phone", uuid4(), 100.000), CALIB)
    out = p.offer(_frame("side-phone", uuid4(), 100.005), CALIB)  # different capture_id
    assert out.paired is None


def test_consumed_frame_not_reused():
    p = _pairer()
    cid = uuid4()
    p.offer(_frame("front-phone", cid, 100.0), CALIB)
    first = p.offer(_frame("side-phone", cid, 100.010), CALIB)
    assert first.paired is not None
    # A second side frame for the same capture must not re-pair the consumed front frame.
    second = p.offer(_frame("side-phone", cid, 100.012), CALIB)
    assert second.paired is None
    assert p.pending_depth("side-phone") == 1


def test_picks_closest_candidate():
    p = _pairer()
    cid = uuid4()
    p.offer(_frame("front-phone", cid, 100.000, seq=1), CALIB)
    p.offer(_frame("front-phone", cid, 100.040, seq=2), CALIB)
    out = p.offer(_frame("side-phone", cid, 100.035), CALIB)
    assert out.paired is not None
    # Closest front frame is the 100.040 one (5 ms) not 100.000 (35 ms).
    assert out.paired.first.sequence == 2


def test_live_mode_pairs_without_capture_id():
    p = _pairer(require_capture_id=False)
    p.offer(_frame("front-phone", uuid4(), 100.000), CALIB)
    out = p.offer(_frame("side-phone", uuid4(), 100.010), CALIB)
    assert out.paired is not None


def test_single_required_device_emits_immediately():
    p = _pairer()
    cid = uuid4()
    out = p.offer(
        _frame("front-phone", cid, 100.0),
        CALIB,
        required_device_ids=("front-phone",),
    )
    assert out.paired is not None
    assert len(out.paired.frames) == 1
    assert out.paired.first.device_id == "front-phone"
    assert out.paired.pair_skew_ms == 0.0
