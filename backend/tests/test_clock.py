"""Tests for clock-offset estimation."""

from __future__ import annotations

from hmc_backend.capture.clock import ClockEstimator, compute_offset_and_delay


def test_offset_formula_symmetric_delay():
    # Phone clock is 100.0 s ahead of backend; symmetric 20 ms one-way delay.
    # backend send t0=0; phone receives t1 = t0 + delay + offset = 0.02 + 100
    # phone sends t2 = t1 (instant turnaround); backend receives t3 = t2 - offset + delay
    offset_true = 100.0
    one_way = 0.02
    t0 = 0.0
    t1 = t0 + one_way + offset_true
    t2 = t1
    t3 = t2 - offset_true + one_way
    offset, delay = compute_offset_and_delay(t0, t1, t2, t3)
    assert abs(offset - offset_true) < 1e-9
    assert abs(delay - 2 * one_way) < 1e-9


def test_estimator_normalizes_phone_time():
    est = ClockEstimator()
    offset_true = 50.0
    one_way = 0.005
    for i in range(5):
        t0 = float(i)
        t1 = t0 + one_way + offset_true
        t2 = t1
        t3 = t2 - offset_true + one_way
        est.record_pong(f"r{i}", t0, t1, t2, t3)
    assert est.ready
    # A phone capture at 1050.0 should normalize near 1000.0.
    assert abs(est.normalize(1050.0) - 1000.0) < 1e-3


def test_estimator_rejects_high_delay():
    est = ClockEstimator(max_delay_s=0.05)
    # 200 ms round trip -> rejected.
    t0, t1, t2, t3 = 0.0, 0.1, 0.1, 0.2
    assert est.record_pong("r", t0, t1, t2, t3) is None
    assert not est.ready
    assert est.offset_s() is None
    assert est.normalize(10.0) is None


def test_estimator_keeps_lowest_delay_samples():
    est = ClockEstimator(keep=2)
    # Three samples with increasing delay; keep=2 should retain the two lowest.
    for i, one_way in enumerate([0.001, 0.05, 0.01]):
        t0 = float(i)
        t1 = t0 + one_way
        t2 = t1
        t3 = t2 + one_way
        est.record_pong(f"r{i}", t0, t1, t2, t3)
    assert est.sample_count == 2
    delays = sorted(s.delay_s for s in est._samples)
    assert delays[0] < delays[1] <= 0.02 + 1e-9


def test_uncertainty_nonzero_with_single_sample():
    est = ClockEstimator()
    est.record_pong("r", 0.0, 0.02, 0.02, 0.04)  # 40 ms rtt, 20 ms one-way
    assert est.uncertainty_ms() is not None
    assert est.uncertainty_ms() > 0
