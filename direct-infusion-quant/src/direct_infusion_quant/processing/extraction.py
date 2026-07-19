"""GUI-independent direct-infusion scan response extraction."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

import numpy as np

from direct_infusion_quant.io.base import SpectrumRecord
from direct_infusion_quant.models import (
    ExtractionWindow,
    ProcessingSettings,
    SampleRecord,
    SummaryMethod,
    ToleranceUnit,
)
from direct_infusion_quant.processing.summaries import (
    ResponseSummary,
    summarize_responses,
)


class ProcessingError(ValueError):
    """Raised when data cannot satisfy the selected processing settings."""


class ProcessingCancelled(RuntimeError):
    """Raised after a caller requests cancellation."""


@dataclass(frozen=True, slots=True)
class WarningThresholds:
    """Explicit user-selected thresholds for descriptive warnings."""

    high_rsd_percent: float | None = None
    frequent_zero_fraction: float | None = None

    def __post_init__(self) -> None:
        if self.high_rsd_percent is not None and self.high_rsd_percent <= 0:
            raise ValueError("high RSD threshold must be positive")
        if self.frequent_zero_fraction is not None and not (
            0 <= self.frequent_zero_fraction <= 1
        ):
            raise ValueError("frequent zero fraction must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class ProcessingWarning:
    """A structured, non-destructive processing warning."""

    code: str
    message: str
    window_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ScanResponse:
    """All extracted responses for one retained scan."""

    native_id: str
    scan_index: int
    elapsed_time_seconds: float
    window_responses: Mapping[UUID, float]
    derived_response: float | None


@dataclass(frozen=True, slots=True)
class FileProcessingResult:
    """Traceable scan-level and summarized responses for one file."""

    scans: tuple[ScanResponse, ...]
    window_summaries: Mapping[UUID, ResponseSummary]
    derived_summary: ResponseSummary | None
    quantification_response: float | None
    warnings: tuple[ProcessingWarning, ...]
    observed_mz_range: tuple[float, float] | None
    time_start_seconds: float
    time_end_seconds: float


CancelCheck = Callable[[], bool]


def settings_for_sample(
    settings: ProcessingSettings, sample: SampleRecord
) -> ProcessingSettings:
    """Apply a sample start override while preserving the shared duration."""

    if sample.time_start_seconds is None:
        return settings
    if settings.time_end_seconds is None:
        raise ProcessingError("a finite default interval is required")
    duration = settings.time_end_seconds - settings.time_start_seconds
    return settings.model_copy(
        update={
            "time_start_seconds": sample.time_start_seconds,
            "time_end_seconds": sample.time_start_seconds + duration,
        }
    )


def window_bounds(window: ExtractionWindow) -> tuple[float, float]:
    """Return inclusive lower and upper m/z bounds for a window."""

    delta = (
        window.tolerance
        if window.tolerance_unit is ToleranceUnit.DA
        else window.target_mz * window.tolerance / 1_000_000.0
    )
    return window.target_mz - delta, window.target_mz + delta


def process_file(
    spectra: Iterable[SpectrumRecord],
    windows: Sequence[ExtractionWindow],
    settings: ProcessingSettings,
    warning_thresholds: WarningThresholds,
    *,
    quantifier_window_id: UUID | None = None,
    derived_window_ids: Sequence[UUID] = (),
    is_cancelled: CancelCheck | None = None,
) -> FileProcessingResult:
    """Extract and summarize one file using explicit direct-infusion settings."""

    if settings.time_end_seconds is None:
        raise ProcessingError("a finite acquisition-time interval is required")
    if settings.time_start_seconds >= settings.time_end_seconds:
        raise ProcessingError("acquisition-time interval start must be before end")

    enabled_windows = [window for window in windows if window.enabled]
    enabled_by_id = {window.id: window for window in enabled_windows}
    if len(enabled_by_id) != len(enabled_windows):
        raise ProcessingError("enabled extraction window IDs must be unique")
    if len(set(derived_window_ids)) != len(derived_window_ids):
        raise ProcessingError("derived response window IDs must be unique")
    if derived_window_ids and len(derived_window_ids) < 2:
        raise ProcessingError("derived sum requires at least two windows")
    if quantifier_window_id is not None and derived_window_ids:
        raise ProcessingError("select either one quantifier window or a derived sum")
    if quantifier_window_id is not None and quantifier_window_id not in enabled_by_id:
        raise ProcessingError("quantifier references a disabled or unknown window")
    if any(window_id not in enabled_by_id for window_id in derived_window_ids):
        raise ProcessingError(
            "derived response references a disabled or unknown window"
        )

    bounds = {window.id: window_bounds(window) for window in enabled_windows}
    scans: list[ScanResponse] = []
    selected_level_seen = False
    selected_level_time_min: float | None = None
    selected_level_time_max: float | None = None
    observed_mz_min: float | None = None
    observed_mz_max: float | None = None

    for spectrum in spectra:
        if is_cancelled is not None and is_cancelled():
            raise ProcessingCancelled("processing cancelled")
        if spectrum.ms_level != settings.ms_level:
            continue
        selected_level_seen = True
        selected_level_time_min = _minimum(
            selected_level_time_min, spectrum.elapsed_time_seconds
        )
        selected_level_time_max = _maximum(
            selected_level_time_max, spectrum.elapsed_time_seconds
        )
        if not (
            settings.time_start_seconds
            <= spectrum.elapsed_time_seconds
            <= settings.time_end_seconds
        ):
            continue
        _validate_spectrum(spectrum)
        if spectrum.mz.size:
            observed_mz_min = _minimum(observed_mz_min, float(np.min(spectrum.mz)))
            observed_mz_max = _maximum(observed_mz_max, float(np.max(spectrum.mz)))

        responses = {
            window.id: _sum_window(spectrum, bounds[window.id])
            for window in enabled_windows
        }
        derived = (
            float(sum(responses[window_id] for window_id in derived_window_ids))
            if derived_window_ids
            else None
        )
        scans.append(
            ScanResponse(
                native_id=spectrum.native_id,
                scan_index=spectrum.index,
                elapsed_time_seconds=spectrum.elapsed_time_seconds,
                window_responses=responses,
                derived_response=derived,
            )
        )

    if not selected_level_seen:
        raise ProcessingError(f"file contains no MS{settings.ms_level} scans")
    if not scans:
        interval = (
            f"{settings.time_start_seconds:g}–{settings.time_end_seconds:g} seconds"
        )
        observed = f"{selected_level_time_min:g}–{selected_level_time_max:g} seconds"
        raise ProcessingError(
            f"selected interval {interval} does not overlap this file's "
            f"MS{settings.ms_level} acquisition range {observed}"
        )

    summaries = {
        window.id: summarize_responses(
            [scan.window_responses[window.id] for scan in scans],
            settings.trim_fraction,
        )
        for window in enabled_windows
    }
    derived_summary = (
        summarize_responses(
            [
                scan.derived_response
                for scan in scans
                if scan.derived_response is not None
            ],
            settings.trim_fraction,
        )
        if derived_window_ids
        else None
    )
    warnings = _build_warnings(
        enabled_windows,
        summaries,
        derived_summary,
        observed_mz_min,
        observed_mz_max,
        warning_thresholds,
    )
    selected_summary = (
        summaries[quantifier_window_id]
        if quantifier_window_id is not None
        else derived_summary
    )
    quantification_response = (
        _selected_statistic(selected_summary, settings.summary_method)
        if selected_summary is not None
        else None
    )
    return FileProcessingResult(
        scans=tuple(scans),
        window_summaries=summaries,
        derived_summary=derived_summary,
        quantification_response=quantification_response,
        warnings=tuple(warnings),
        observed_mz_range=(observed_mz_min, observed_mz_max)
        if observed_mz_min is not None and observed_mz_max is not None
        else None,
        time_start_seconds=settings.time_start_seconds,
        time_end_seconds=settings.time_end_seconds,
    )


def process_files(
    spectra_by_file: Mapping[str, Iterable[SpectrumRecord]],
    windows: Sequence[ExtractionWindow],
    settings: ProcessingSettings,
    warning_thresholds: WarningThresholds,
    *,
    quantifier_window_id: UUID | None = None,
    derived_window_ids: Sequence[UUID] = (),
    is_cancelled: CancelCheck | None = None,
) -> dict[str, FileProcessingResult]:
    """Process files with the supplied explicit settings."""

    results: dict[str, FileProcessingResult] = {}
    for file_id, spectra in spectra_by_file.items():
        results[file_id] = process_file(
            spectra,
            windows,
            settings,
            warning_thresholds,
            quantifier_window_id=quantifier_window_id,
            derived_window_ids=derived_window_ids,
            is_cancelled=is_cancelled,
        )
    return results


def _sum_window(spectrum: SpectrumRecord, bounds: tuple[float, float]) -> float:
    lower, upper = bounds
    matches = (spectrum.mz >= lower) & (spectrum.mz <= upper)
    return float(np.sum(spectrum.intensity[matches], dtype=np.float64))


def _validate_spectrum(spectrum: SpectrumRecord) -> None:
    if spectrum.mz.ndim != 1 or spectrum.intensity.ndim != 1:
        raise ProcessingError("m/z and intensity arrays must be one-dimensional")
    if spectrum.mz.shape != spectrum.intensity.shape:
        raise ProcessingError("m/z and intensity arrays must have equal lengths")


def _build_warnings(
    windows: Sequence[ExtractionWindow],
    summaries: Mapping[UUID, ResponseSummary],
    derived_summary: ResponseSummary | None,
    mz_min: float | None,
    mz_max: float | None,
    thresholds: WarningThresholds,
) -> list[ProcessingWarning]:
    warnings: list[ProcessingWarning] = []
    for window in windows:
        lower, upper = window_bounds(window)
        if mz_min is None or mz_max is None or upper < mz_min or lower > mz_max:
            warnings.append(
                ProcessingWarning(
                    code="window_outside_mz_range",
                    message=(
                        f"Window '{window.name}' is outside the observed m/z range."
                    ),
                    window_id=window.id,
                )
            )
        warnings.extend(_summary_warnings(summaries[window.id], thresholds, window.id))
    if derived_summary is not None:
        warnings.extend(_summary_warnings(derived_summary, thresholds, None))
    return warnings


def _summary_warnings(
    summary: ResponseSummary,
    thresholds: WarningThresholds,
    window_id: UUID | None,
) -> list[ProcessingWarning]:
    warnings: list[ProcessingWarning] = []
    if (
        summary.rsd_percent is not None
        and thresholds.high_rsd_percent is not None
        and summary.rsd_percent >= thresholds.high_rsd_percent
    ):
        warnings.append(
            ProcessingWarning(
                code="high_scan_rsd",
                message=f"Scan-level RSD is {summary.rsd_percent:.2f}%.",
                window_id=window_id,
            )
        )
    zero_fraction = summary.zero_response_scan_count / summary.scan_count
    if (
        thresholds.frequent_zero_fraction is not None
        and zero_fraction >= thresholds.frequent_zero_fraction
    ):
        warnings.append(
            ProcessingWarning(
                code="frequent_zero_response",
                message=(
                    f"Zero-response scans account for {zero_fraction:.1%} of scans."
                ),
                window_id=window_id,
            )
        )
    return warnings


def _selected_statistic(summary: ResponseSummary, method: SummaryMethod) -> float:
    if method is SummaryMethod.MEDIAN:
        return summary.median
    if method is SummaryMethod.MEAN:
        return summary.mean
    return summary.trimmed_mean


def _minimum(current: float | None, value: float) -> float:
    return value if current is None else min(current, value)


def _maximum(current: float | None, value: float) -> float:
    return value if current is None else max(current, value)
