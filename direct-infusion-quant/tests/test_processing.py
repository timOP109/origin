"""Tests for direct-infusion scan extraction and response summaries."""

from collections.abc import Iterable

import numpy as np
import pytest

from direct_infusion_quant.io.base import SpectrumRecord
from direct_infusion_quant.models import (
    ExtractionWindow,
    ProcessingSettings,
    SummaryMethod,
    ToleranceUnit,
)
from direct_infusion_quant.processing.extraction import (
    ProcessingCancelled,
    ProcessingError,
    WarningThresholds,
    process_file,
)

THRESHOLDS = WarningThresholds(high_rsd_percent=50.0, frequent_zero_fraction=0.5)


def spectrum(
    index: int,
    time: float,
    mz: Iterable[float],
    intensity: Iterable[float],
    ms_level: int = 1,
) -> SpectrumRecord:
    return SpectrumRecord(
        native_id=f"scan={index}",
        index=index,
        ms_level=ms_level,
        elapsed_time_seconds=time,
        mz=np.asarray(list(mz), dtype=np.float64),
        intensity=np.asarray(list(intensity), dtype=np.float64),
    )


def window(
    name: str,
    target: float,
    tolerance: float,
    unit: ToleranceUnit = ToleranceUnit.DA,
) -> ExtractionWindow:
    return ExtractionWindow(
        name=name,
        target_mz=target,
        tolerance=tolerance,
        tolerance_unit=unit,
    )


def settings(**changes: object) -> ProcessingSettings:
    return ProcessingSettings(
        time_start_seconds=1.0,
        time_end_seconds=3.0,
        **changes,
    )


def test_da_window_sums_matching_centroids_and_filters_scans() -> None:
    quantifier = window("q", 100.0, 0.1)
    spectra = [
        spectrum(0, 0.0, [100.0], [999.0]),
        spectrum(1, 1.0, [99.9, 100.0, 100.1, 101.0], [1, 2, 3, 50]),
        spectrum(2, 2.0, [100.0], [999.0], ms_level=2),
    ]
    result = process_file(spectra, [quantifier], settings(), THRESHOLDS)
    assert len(result.scans) == 1
    assert result.scans[0].window_responses[quantifier.id] == pytest.approx(6.0)


def test_ppm_window_uses_target_relative_tolerance() -> None:
    quantifier = window("q", 1000.0, 10.0, ToleranceUnit.PPM)
    result = process_file(
        [spectrum(1, 1.0, [999.989, 999.99, 1000.01, 1000.011], [8, 2, 3, 9])],
        [quantifier],
        settings(),
        THRESHOLDS,
    )
    assert result.scans[0].window_responses[quantifier.id] == pytest.approx(5.0)


def test_exact_mz_and_time_boundaries_are_inclusive() -> None:
    quantifier = window("q", 100.0, 0.1)
    result = process_file(
        [
            spectrum(1, 1.0, [99.9], [2]),
            spectrum(2, 3.0, [100.1], [3]),
        ],
        [quantifier],
        settings(),
        THRESHOLDS,
    )
    assert [scan.window_responses[quantifier.id] for scan in result.scans] == [2, 3]


def test_no_matching_peak_is_preserved_as_zero_and_counted() -> None:
    quantifier = window("q", 100.0, 0.01)
    result = process_file(
        [spectrum(1, 1.0, [200.0], [7.0])],
        [quantifier],
        settings(),
        THRESHOLDS,
    )
    summary = result.window_summaries[quantifier.id]
    assert result.scans[0].window_responses[quantifier.id] == 0
    assert summary.zero_response_scan_count == 1
    assert {warning.code for warning in result.warnings} == {
        "frequent_zero_response",
        "window_outside_mz_range",
    }


def test_multiple_windows_are_separate_unless_sum_is_explicit() -> None:
    first = window("first", 100.0, 0.01)
    second = window("second", 200.0, 0.01)
    spectra = [spectrum(1, 1.0, [100.0, 200.0], [4.0, 6.0])]
    separate = process_file(spectra, [first, second], settings(), THRESHOLDS)
    explicit_sum = process_file(
        spectra,
        [first, second],
        settings(),
        THRESHOLDS,
        derived_window_ids=[first.id, second.id],
    )
    assert separate.scans[0].derived_response is None
    assert explicit_sum.scans[0].derived_response == pytest.approx(10.0)
    assert explicit_sum.quantification_response == pytest.approx(10.0)


def test_large_spray_spike_is_retained_and_changes_mean_not_median() -> None:
    quantifier = window("q", 100.0, 0.01)
    responses = [10.0, 10.0, 10.0, 1000.0]
    result = process_file(
        [spectrum(i, float(i), [100.0], [value]) for i, value in enumerate(responses)],
        [quantifier],
        ProcessingSettings(time_start_seconds=0, time_end_seconds=3),
        THRESHOLDS,
        quantifier_window_id=quantifier.id,
    )
    summary = result.window_summaries[quantifier.id]
    assert len(result.scans) == 4
    assert summary.median == pytest.approx(10.0)
    assert summary.mean == pytest.approx(257.5)
    assert result.quantification_response == pytest.approx(10.0)
    assert "high_scan_rsd" in {warning.code for warning in result.warnings}


def test_mean_can_be_explicit_quantification_response() -> None:
    quantifier = window("q", 100.0, 0.01)
    result = process_file(
        [spectrum(1, 1, [100], [10]), spectrum(2, 2, [100], [20])],
        [quantifier],
        settings(summary_method=SummaryMethod.MEAN),
        THRESHOLDS,
        quantifier_window_id=quantifier.id,
    )
    assert result.quantification_response == pytest.approx(15.0)


def test_trimmed_mean_is_configurable() -> None:
    quantifier = window("q", 100.0, 0.01)
    result = process_file(
        [
            spectrum(index, float(index), [100], [response])
            for index, response in enumerate([0, 10, 10, 10, 100])
        ],
        [quantifier],
        ProcessingSettings(
            time_start_seconds=0,
            time_end_seconds=4,
            summary_method=SummaryMethod.TRIMMED_MEAN,
            trim_fraction=0.2,
        ),
        THRESHOLDS,
        quantifier_window_id=quantifier.id,
    )
    assert result.window_summaries[quantifier.id].trimmed_mean == pytest.approx(10.0)
    assert result.quantification_response == pytest.approx(10.0)


def test_interval_must_overlap_file() -> None:
    quantifier = window("q", 100.0, 0.01)
    with pytest.raises(ProcessingError, match="does not overlap"):
        process_file(
            [spectrum(1, 10.0, [100], [1])],
            [quantifier],
            settings(),
            THRESHOLDS,
        )


def test_cancellation_stops_without_partial_result() -> None:
    quantifier = window("q", 100.0, 0.01)
    checks = 0

    def cancel_after_first_check() -> bool:
        nonlocal checks
        checks += 1
        return checks > 1

    with pytest.raises(ProcessingCancelled):
        process_file(
            [spectrum(1, 1, [100], [1]), spectrum(2, 2, [100], [2])],
            [quantifier],
            settings(),
            THRESHOLDS,
            is_cancelled=cancel_after_first_check,
        )
