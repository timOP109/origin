"""Tests for fixed-duration stable-period recommendations."""

import numpy as np
import pytest

from direct_infusion_quant.models import ProcessingSettings, SampleRecord, SampleType
from direct_infusion_quant.processing import (
    StabilityLimits,
    recommend_stable_interval,
    recommend_stable_intervals,
    settings_for_sample,
)


def test_sample_override_moves_but_does_not_resize_interval(tmp_path) -> None:
    settings = ProcessingSettings(time_start_seconds=120, time_end_seconds=150)
    sample = SampleRecord(
        path=tmp_path / "sample.mzML",
        sample_name="sample",
        sample_type=SampleType.UNKNOWN,
        time_start_seconds=205,
    )
    effective = settings_for_sample(settings, sample)
    assert effective.time_start_seconds == 205
    assert effective.time_end_seconds == 235


def test_recommendation_finds_stable_fixed_duration_period() -> None:
    times = np.arange(0.0, 101.0)
    responses = np.concatenate(
        [
            np.linspace(10.0, 100.0, 40),
            np.full(31, 250.0),
            np.linspace(250.0, 500.0, 30),
        ]
    )
    result = recommend_stable_interval(times, responses, 30.0, minimum_scans=20)
    assert result.start_seconds == pytest.approx(40.0)
    assert result.end_seconds == pytest.approx(70.0)
    assert result.robust_cv_percent == pytest.approx(0.0)
    assert result.relative_drift_percent == pytest.approx(0.0, abs=1e-10)


def test_recommendation_penalizes_zero_response_periods() -> None:
    times = np.arange(0.0, 61.0)
    responses = np.concatenate([np.zeros(31), np.full(30, 100.0)])
    result = recommend_stable_interval(times, responses, 20.0, minimum_scans=10)
    assert result.start_seconds >= 31.0
    assert result.zero_fraction == 0.0


def test_equal_stability_prefers_higher_signal() -> None:
    times = np.arange(0.0, 61.0)
    responses = np.concatenate([np.ones(31), np.full(30, 100.0)])
    result = recommend_stable_interval(times, responses, 20.0, minimum_scans=10)
    assert result.start_seconds >= 31.0
    assert result.median_response == 100.0


def test_recommendation_requires_a_complete_candidate() -> None:
    with pytest.raises(ValueError, match="complete fixed-duration"):
        recommend_stable_interval([0, 1, 2, 3], [1, 1, 1, 1], 30, minimum_scans=3)


def test_recommendation_rejects_non_finite_input() -> None:
    with pytest.raises(ValueError, match="must be finite"):
        recommend_stable_interval([0, 1, 2, 3], [1, 1, np.nan, 1], 2, minimum_scans=3)


def test_recommendation_rejects_no_sustained_signal() -> None:
    times = np.arange(0.0, 61.0)
    responses = np.zeros(61)
    responses[20] = 100.0
    with pytest.raises(ValueError, match="no sustained positive"):
        recommend_stable_interval(times, responses, 20.0, minimum_scans=10)


def test_ranked_candidates_are_non_overlapping_and_respect_exclusions() -> None:
    times = np.arange(0.0, 121.0)
    responses = np.full(times.shape, 50.0)
    candidates = recommend_stable_intervals(
        times,
        responses,
        20.0,
        candidate_count=3,
        exclude_before_seconds=10.0,
        exclude_after_seconds=100.0,
    )
    assert len(candidates) == 3
    assert all(candidate.start_seconds >= 10 for candidate in candidates)
    assert all(candidate.end_seconds <= 100 for candidate in candidates)
    for left, right in zip(candidates, candidates[1:], strict=False):
        assert (
            left.end_seconds <= right.start_seconds
            or right.end_seconds <= left.start_seconds
        )
    assert candidates == recommend_stable_intervals(
        times,
        responses,
        20.0,
        candidate_count=3,
        exclude_before_seconds=10.0,
        exclude_after_seconds=100.0,
    )


def test_method_limits_are_reported_without_rejecting_candidate() -> None:
    times = np.arange(0.0, 31.0)
    responses = np.arange(100.0, 131.0)
    candidate = recommend_stable_intervals(
        times,
        responses,
        30.0,
        limits=StabilityLimits(max_robust_cv_percent=1.0, minimum_response=500.0),
    )[0]
    assert not candidate.meets_limits
    assert set(candidate.limit_failures) == {"robust_cv", "minimum_response"}
