"""Subject dimensions keep provenance and never regress to a noisier value."""

from __future__ import annotations

import pytest

from hmc_backend.colliders.subject import SIDED_FIELDS, DimensionEstimate, SubjectDimensions
from hmc_backend.contracts.enums import FitSource


def test_defaults_are_labelled_global():
    d = SubjectDimensions()
    for f in SIDED_FIELDS:
        for side in ("left", "right"):
            e = d.get(f, side)
            assert e.source is FitSource.GLOBAL_DEFAULT and e.sample_count == 0
    assert d.head_radius_m.source is FitSource.GLOBAL_DEFAULT
    # A global default is never relabelled as a subject measurement.
    assert d.as_subject_default("forearm_radius_m", "left").source is FitSource.GLOBAL_DEFAULT


def test_observed_replaces_default_and_is_relabelled_for_later_poses():
    d = SubjectDimensions()
    obs = DimensionEstimate(0.052, FitSource.OBSERVED, 80, 0.002)
    d2 = d.updated("forearm_radius_m", obs, "left")
    assert d2.get("forearm_radius_m", "left") == obs
    assert d2.get("forearm_radius_m", "right").source is FitSource.GLOBAL_DEFAULT  # other side untouched
    assert d is not d2 and d.get("forearm_radius_m", "left").source is FitSource.GLOBAL_DEFAULT
    later = d2.as_subject_default("forearm_radius_m", "left")
    assert later.value_m == pytest.approx(0.052) and later.source is FitSource.SUBJECT_DEFAULT


def test_noisy_later_pose_does_not_overwrite_a_good_measurement():
    d = SubjectDimensions().updated("thigh_radius_m", DimensionEstimate(0.074, FitSource.OBSERVED, 120, 0.002), "right")
    noisy = DimensionEstimate(0.11, FitSource.OBSERVED, 40, 0.02)
    assert d.updated("thigh_radius_m", noisy, "right").get("thigh_radius_m", "right").value_m == pytest.approx(0.074)
    few = DimensionEstimate(0.07, FitSource.OBSERVED, 5, 0.0005)
    assert d.updated("thigh_radius_m", few, "right").get("thigh_radius_m", "right").value_m == pytest.approx(0.074)
    tighter = DimensionEstimate(0.073, FitSource.OBSERVED, 200, 0.001)
    assert d.updated("thigh_radius_m", tighter, "right").get("thigh_radius_m", "right").value_m == pytest.approx(0.073)


def test_disabled_and_subject_default_never_overwrite_observed():
    d = SubjectDimensions().updated("hand_width_m", DimensionEstimate(0.09, FitSource.OBSERVED, 60, 0.003), "left")
    d2 = d.updated("hand_width_m", DimensionEstimate(0.0, FitSource.DISABLED, 0, 0.0), "left")
    d3 = d2.updated("hand_width_m", DimensionEstimate(0.1, FitSource.SUBJECT_DEFAULT, 60, 0.001), "left")
    assert d3.get("hand_width_m", "left").value_m == pytest.approx(0.09)


def test_non_sided_fields_and_report():
    d = SubjectDimensions().updated("head_radius_m", DimensionEstimate(0.105, FitSource.OBSERVED, 500, 0.004))
    assert d.head_radius_m.value_m == pytest.approx(0.105)
    with pytest.raises(ValueError):
        d.get("shin_radius_m")
    rep = d.report()
    assert rep["head_radius_m"]["source"] == "observed"
    assert set(rep["shin_radius_m"]) == {"left", "right"}

