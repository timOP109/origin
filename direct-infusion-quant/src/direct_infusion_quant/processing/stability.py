"""Non-destructive recommendations for stable direct-infusion periods."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class StabilityLimits:
    """Optional method-specific candidate limits; none are universal defaults."""

    max_robust_cv_percent: float | None = None
    max_relative_drift_percent: float | None = None
    max_zero_fraction: float | None = None
    minimum_response: float | None = None


@dataclass(frozen=True, slots=True)
class StableIntervalRecommendation:
    """One fixed-duration candidate with transparent stability metrics."""

    start_seconds: float
    end_seconds: float
    scan_count: int
    median_response: float
    robust_cv_percent: float
    relative_drift_percent: float
    zero_fraction: float
    signal_fraction_of_reference: float
    score: float
    limit_failures: tuple[str, ...] = ()

    @property
    def meets_limits(self) -> bool:
        return not self.limit_failures


def recommend_stable_intervals(
    times_seconds: Sequence[float],
    responses: Sequence[float],
    duration_seconds: float,
    *,
    minimum_scans: int = 10,
    candidate_count: int = 3,
    exclude_before_seconds: float | None = None,
    exclude_after_seconds: float | None = None,
    limits: StabilityLimits | None = None,
) -> tuple[StableIntervalRecommendation, ...]:
    """Return ranked non-overlapping candidates without applying any interval."""

    if duration_seconds <= 0:
        raise ValueError("recommendation duration must be positive")
    if minimum_scans < 3:
        raise ValueError("minimum scans must be at least three")
    if candidate_count < 1:
        raise ValueError("candidate count must be positive")
    times = np.asarray(times_seconds, dtype=np.float64)
    values = np.asarray(responses, dtype=np.float64)
    if times.ndim != 1 or values.ndim != 1 or times.shape != values.shape:
        raise ValueError("times and responses must be equal-length vectors")
    if times.size < minimum_scans:
        raise ValueError("not enough scans to recommend a stable interval")
    if not np.all(np.isfinite(times)) or not np.all(np.isfinite(values)):
        raise ValueError("times and responses must be finite")
    order = np.argsort(times, kind="stable")
    times = times[order]
    values = values[order]
    raw_candidates: list[tuple[float, float, np.ndarray, np.ndarray]] = []
    for start in times:
        end = float(start + duration_seconds)
        if end > times[-1]:
            continue
        if exclude_before_seconds is not None and start < exclude_before_seconds:
            continue
        if exclude_after_seconds is not None and end > exclude_after_seconds:
            continue
        selected = (times >= start) & (times <= end)
        if int(np.count_nonzero(selected)) < minimum_scans:
            continue
        raw_candidates.append((float(start), end, times[selected], values[selected]))
    if not raw_candidates:
        raise ValueError("no complete fixed-duration interval contains enough scans")
    signal_reference = max(float(np.median(item[3])) for item in raw_candidates)
    if signal_reference <= 0:
        raise ValueError("no sustained positive stability-trace response was detected")
    limits = limits or StabilityLimits()
    candidates = [
        _candidate(
            start, end, candidate_times, candidate_values, signal_reference, limits
        )
        for start, end, candidate_times, candidate_values in raw_candidates
    ]
    ranked = sorted(
        candidates,
        key=lambda item: (
            not item.meets_limits,
            item.score,
            -item.median_response,
            item.start_seconds,
        ),
    )
    selected_candidates: list[StableIntervalRecommendation] = []
    for candidate in ranked:
        if any(_overlaps(candidate, existing) for existing in selected_candidates):
            continue
        selected_candidates.append(candidate)
        if len(selected_candidates) >= candidate_count:
            break
    return tuple(selected_candidates)


def recommend_stable_interval(
    times_seconds: Sequence[float],
    responses: Sequence[float],
    duration_seconds: float,
    *,
    minimum_scans: int = 10,
) -> StableIntervalRecommendation:
    """Backward-compatible best-candidate helper."""

    return recommend_stable_intervals(
        times_seconds,
        responses,
        duration_seconds,
        minimum_scans=minimum_scans,
        candidate_count=1,
    )[0]


def interval_diagnostics(
    times_seconds: Sequence[float],
    responses: Sequence[float],
    start_seconds: float,
    end_seconds: float,
) -> StableIntervalRecommendation:
    """Describe a chosen period without ranking or acceptance judgment."""

    times = np.asarray(times_seconds, dtype=np.float64)
    values = np.asarray(responses, dtype=np.float64)
    selected = (times >= start_seconds) & (times <= end_seconds)
    if int(np.count_nonzero(selected)) < 3:
        raise ValueError("chosen interval contains fewer than three scans")
    candidate_values = values[selected]
    reference = max(float(np.median(candidate_values)), np.finfo(np.float64).eps)
    return _candidate(
        start_seconds,
        end_seconds,
        times[selected],
        candidate_values,
        reference,
        StabilityLimits(),
    )


def _candidate(
    start: float,
    end: float,
    times: np.ndarray,
    values: np.ndarray,
    signal_reference: float,
    limits: StabilityLimits,
) -> StableIntervalRecommendation:
    median = float(np.median(values))
    scale = max(abs(median), np.finfo(np.float64).eps)
    mad = float(np.median(np.abs(values - median)))
    robust_cv = 100.0 * 1.4826 * mad / scale
    slope = float(np.polyfit(times - times[0], values, 1)[0])
    relative_drift = 100.0 * abs(slope) * (end - start) / scale
    zero_fraction = float(np.count_nonzero(values == 0) / values.size)
    signal_fraction = min(max(median / signal_reference, 0.0), 1.0)
    failures: list[str] = []
    if (
        limits.max_robust_cv_percent is not None
        and robust_cv > limits.max_robust_cv_percent
    ):
        failures.append("robust_cv")
    if (
        limits.max_relative_drift_percent is not None
        and relative_drift > limits.max_relative_drift_percent
    ):
        failures.append("relative_drift")
    if (
        limits.max_zero_fraction is not None
        and zero_fraction > limits.max_zero_fraction
    ):
        failures.append("zero_fraction")
    if limits.minimum_response is not None and median < limits.minimum_response:
        failures.append("minimum_response")
    score = (
        robust_cv
        + relative_drift
        + 100.0 * zero_fraction
        + 100.0 * (1.0 - signal_fraction)
    )
    return StableIntervalRecommendation(
        start_seconds=float(start),
        end_seconds=float(end),
        scan_count=int(values.size),
        median_response=median,
        robust_cv_percent=robust_cv,
        relative_drift_percent=relative_drift,
        zero_fraction=zero_fraction,
        signal_fraction_of_reference=signal_fraction,
        score=score,
        limit_failures=tuple(failures),
    )


def _overlaps(
    left: StableIntervalRecommendation, right: StableIntervalRecommendation
) -> bool:
    return (
        left.start_seconds < right.end_seconds
        and right.start_seconds < left.end_seconds
    )
