"""Deterministic summaries of scan-level direct-infusion responses."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import trim_mean


@dataclass(frozen=True, slots=True)
class ResponseSummary:
    """File-level descriptive statistics for one response series."""

    scan_count: int
    median: float
    mean: float
    sample_standard_deviation: float | None
    rsd_percent: float | None
    trimmed_mean: float
    zero_response_scan_count: int


def summarize_responses(
    responses: list[float], trim_fraction: float
) -> ResponseSummary:
    """Summarize responses without removing or changing any observations."""

    if not responses:
        raise ValueError("at least one response is required")
    if not 0 <= trim_fraction < 0.5:
        raise ValueError("trim fraction must be at least 0 and less than 0.5")

    values = np.asarray(responses, dtype=np.float64)
    mean = float(np.mean(values))
    standard_deviation = float(np.std(values, ddof=1)) if values.size >= 2 else None
    rsd = (
        standard_deviation / abs(mean) * 100.0
        if standard_deviation is not None and mean != 0.0
        else None
    )
    return ResponseSummary(
        scan_count=int(values.size),
        median=float(np.median(values)),
        mean=mean,
        sample_standard_deviation=standard_deviation,
        rsd_percent=rsd,
        trimmed_mean=float(trim_mean(values, proportiontocut=trim_fraction)),
        zero_response_scan_count=int(np.count_nonzero(values == 0.0)),
    )
