"""Background Qt workers for metadata inspection and scientific processing."""

from __future__ import annotations

import traceback
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from threading import Event

import numpy as np
from PySide6.QtCore import QObject, Signal, Slot

from direct_infusion_quant.io import MzMLBackend, MzMLReader, create_mzml_reader
from direct_infusion_quant.models import (
    AnalysisProject,
    QuantifierMode,
    SourceFileProvenance,
    StabilityTraceMode,
)
from direct_infusion_quant.processing import (
    FileProcessingResult,
    ProcessingCancelled,
    StabilityLimits,
    StableIntervalRecommendation,
    WarningThresholds,
    interval_diagnostics,
    process_file,
    recommend_stable_intervals,
    settings_for_sample,
    window_bounds,
)


@dataclass(frozen=True, slots=True)
class ProcessingBundle:
    """Complete worker outcome, including visible per-file failures."""

    results: dict[str, FileProcessingResult]
    failures: dict[str, str]
    provenance: dict[str, SourceFileProvenance]
    processed_at_utc: datetime


@dataclass(frozen=True, slots=True)
class StabilityRecommendationBundle:
    """Per-sample recommendations and visible assessment failures."""

    assessments: dict[str, SampleStabilityAssessment]
    failures: dict[str, str]
    assessed_at_utc: datetime


@dataclass(frozen=True, slots=True)
class SampleStabilityAssessment:
    """Ranked stability candidates with separate analyte-SIC diagnostics."""

    candidates: tuple[StableIntervalRecommendation, ...]
    analyte_diagnostics: tuple[StableIntervalRecommendation, ...]
    times_seconds: tuple[float, ...]
    trace_responses: tuple[float, ...]
    analyte_responses: tuple[float, ...]
    ambiguous: bool


@dataclass(frozen=True, slots=True)
class HashVerificationResult:
    """Byte-for-byte comparison of one source against its saved SHA-256."""

    sample_id: str
    path: Path
    expected_sha256: str | None
    actual_sha256: str | None
    status: str


@dataclass(frozen=True, slots=True)
class HashVerificationBundle:
    """Complete explicit source verification outcome."""

    results: tuple[HashVerificationResult, ...]
    verified_at_utc: datetime


class HashVerificationWorker(QObject):
    """Rehash project sources in the background without changing provenance."""

    progress = Signal(int, int, object, object, str)
    succeeded = Signal(object)
    failed = Signal(str, str)
    cancelled = Signal()
    finished = Signal()

    def __init__(self, project: AnalysisProject) -> None:
        super().__init__()
        self.project = project.model_copy(deep=True)
        self._cancelled = Event()

    @Slot()
    def run(self) -> None:
        try:
            outcomes: list[HashVerificationResult] = []
            total = len(self.project.samples)
            for index, sample in enumerate(self.project.samples, start=1):
                if self._cancelled.is_set():
                    raise ProcessingCancelled("SHA-256 verification cancelled")
                expected = (
                    sample.source_provenance.sha256
                    if sample.source_provenance is not None
                    else None
                )
                if not sample.path.is_file():
                    outcomes.append(
                        HashVerificationResult(
                            str(sample.id), sample.path, expected, None, "missing"
                        )
                    )
                    continue
                if expected is None:
                    outcomes.append(
                        HashVerificationResult(
                            str(sample.id), sample.path, None, None, "no_saved_hash"
                        )
                    )
                    continue
                size = sample.path.stat().st_size

                def report(
                    bytes_read: int,
                    current_index=index,
                    current_sample=sample,
                    current_size=size,
                ):
                    self.progress.emit(
                        current_index,
                        total,
                        bytes_read,
                        current_size,
                        current_sample.sample_name,
                    )

                actual = sha256_file(
                    sample.path,
                    is_cancelled=self._cancelled.is_set,
                    progress=report,
                )
                outcomes.append(
                    HashVerificationResult(
                        sample_id=str(sample.id),
                        path=sample.path,
                        expected_sha256=expected,
                        actual_sha256=actual,
                        status="verified" if actual == expected else "mismatch",
                    )
                )
            self.succeeded.emit(
                HashVerificationBundle(
                    results=tuple(outcomes), verified_at_utc=datetime.now(UTC)
                )
            )
        except ProcessingCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(str(error), traceback.format_exc())
        finally:
            self.finished.emit()

    @Slot()
    def cancel(self) -> None:
        self._cancelled.set()


class MetadataWorker(QObject):
    """Inspect mzML metadata away from the GUI thread."""

    metadata = Signal(object, object)
    progress = Signal(int, int, str)
    failed = Signal(str, str)
    finished = Signal()

    def __init__(
        self,
        paths: list[Path],
        backend: MzMLBackend = MzMLBackend.PYMZML,
    ) -> None:
        super().__init__()
        self.paths = paths
        self.backend = backend
        self._cancelled = Event()

    @Slot()
    def run(self) -> None:
        reader = create_mzml_reader(self.backend)
        try:
            for index, path in enumerate(self.paths, start=1):
                if self._cancelled.is_set():
                    break
                self.progress.emit(index, len(self.paths), path.name)
                try:
                    self.metadata.emit(
                        path,
                        capture_source_provenance(
                            path, reader=reader, is_cancelled=self._cancelled.is_set
                        ),
                    )
                except Exception as error:  # displayed with the affected file
                    self.failed.emit(str(path), str(error))
        finally:
            self.finished.emit()

    @Slot()
    def cancel(self) -> None:
        self._cancelled.set()


class ProcessingWorker(QObject):
    """Stream and process included files with scan-level cancellation checks."""

    progress = Signal(int, int, int, str)
    log = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str, str)
    cancelled = Signal()
    finished = Signal()

    def __init__(self, project: AnalysisProject, thresholds: WarningThresholds) -> None:
        super().__init__()
        self.project = project.model_copy(deep=True)
        self.thresholds = thresholds
        self._cancelled = Event()

    @Slot()
    def run(self) -> None:
        try:
            analyte = next(
                item
                for item in self.project.analytes
                if item.id == self.project.active_analyte_id
            )
            included = [sample for sample in self.project.samples if sample.included]
            results: dict[str, FileProcessingResult] = {}
            failures: dict[str, str] = {}
            provenance: dict[str, SourceFileProvenance] = {}
            reader = create_mzml_reader(self.project.processing.mzml_backend)
            for file_index, sample in enumerate(included, start=1):
                if self._cancelled.is_set():
                    raise ProcessingCancelled("processing cancelled")
                self.log.emit(f"Processing {sample.sample_name}: {sample.path}")
                scans_seen = 0

                def spectra(current_sample=sample, current_index=file_index):
                    nonlocal scans_seen
                    for scan in reader.iter_spectra(current_sample.path):
                        scans_seen += 1
                        if scans_seen == 1 or scans_seen % 25 == 0:
                            self.progress.emit(
                                current_index,
                                len(included),
                                scans_seen,
                                current_sample.sample_name,
                            )
                        yield scan

                try:
                    self.log.emit(
                        f"Recording source metadata and SHA-256: {sample.sample_name}"
                    )
                    provenance[str(sample.id)] = capture_source_provenance(
                        sample.path,
                        reader=reader,
                        is_cancelled=self._cancelled.is_set,
                    )
                    quantifier = (
                        analyte.quantifier_window_ids[0]
                        if analyte.quantifier_mode is QuantifierMode.SINGLE
                        else None
                    )
                    derived = (
                        analyte.quantifier_window_ids
                        if analyte.quantifier_mode is QuantifierMode.SUM
                        else ()
                    )
                    settings = settings_for_sample(self.project.processing, sample)
                    results[str(sample.id)] = process_file(
                        spectra(),
                        analyte.windows,
                        settings,
                        self.thresholds,
                        quantifier_window_id=quantifier,
                        derived_window_ids=derived,
                        is_cancelled=self._cancelled.is_set,
                    )
                except ProcessingCancelled:
                    raise
                except Exception as error:  # retained as an explicit file failure
                    failures[str(sample.id)] = str(error)
                    self.log.emit(f"Excluded {sample.sample_name}: {error}")
            if self._cancelled.is_set():
                raise ProcessingCancelled("processing cancelled")
            self.succeeded.emit(
                ProcessingBundle(
                    results=results,
                    failures=failures,
                    provenance=provenance,
                    processed_at_utc=datetime.now(UTC),
                )
            )
        except ProcessingCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(str(error), traceback.format_exc())
        finally:
            self.finished.emit()

    @Slot()
    def cancel(self) -> None:
        self._cancelled.set()


class StabilityRecommendationWorker(QObject):
    """Stream complete files and recommend fixed-duration stable periods."""

    progress = Signal(int, int, int, str)
    succeeded = Signal(object)
    failed = Signal(str, str)
    cancelled = Signal()
    finished = Signal()

    def __init__(self, project: AnalysisProject) -> None:
        super().__init__()
        self.project = project.model_copy(deep=True)
        self._cancelled = Event()

    @Slot()
    def run(self) -> None:
        try:
            analyte = next(
                item
                for item in self.project.analytes
                if item.id == self.project.active_analyte_id
            )
            duration = (
                self.project.processing.time_end_seconds
                - self.project.processing.time_start_seconds
            )
            quantifier_ids = analyte.quantifier_window_ids
            quantifier_windows = [
                window
                for window in analyte.windows
                if window.enabled and window.id in quantifier_ids
            ]
            analyte_bounds = [window_bounds(window) for window in quantifier_windows]
            trace_mode = self.project.processing.stability_trace_mode
            trace_bounds: list[tuple[float, float]] = []
            if trace_mode is StabilityTraceMode.ANALYTE_SIC:
                trace_bounds = analyte_bounds
            elif trace_mode is StabilityTraceMode.REFERENCE_SIC:
                reference_id = self.project.processing.stability_reference_window_id
                reference = next(
                    window for window in analyte.windows if window.id == reference_id
                )
                trace_bounds = [window_bounds(reference)]
            included = [sample for sample in self.project.samples if sample.included]
            reader = create_mzml_reader(self.project.processing.mzml_backend)
            assessments: dict[str, SampleStabilityAssessment] = {}
            failures: dict[str, str] = {}
            for file_index, sample in enumerate(included, start=1):
                times: list[float] = []
                trace_responses: list[float] = []
                analyte_responses: list[float] = []
                try:
                    for count, scan in enumerate(
                        reader.iter_spectra(sample.path), start=1
                    ):
                        if self._cancelled.is_set():
                            raise ProcessingCancelled("stability assessment cancelled")
                        if count == 1 or count % 25 == 0:
                            self.progress.emit(
                                file_index, len(included), count, sample.sample_name
                            )
                        if scan.ms_level != self.project.processing.ms_level:
                            continue
                        analyte_response = sum(
                            float(
                                np.sum(
                                    scan.intensity[
                                        (scan.mz >= lower) & (scan.mz <= upper)
                                    ],
                                    dtype=np.float64,
                                )
                            )
                            for lower, upper in analyte_bounds
                        )
                        trace_response = (
                            float(np.sum(scan.intensity, dtype=np.float64))
                            if trace_mode is StabilityTraceMode.TIC
                            else sum(
                                float(
                                    np.sum(
                                        scan.intensity[
                                            (scan.mz >= lower) & (scan.mz <= upper)
                                        ],
                                        dtype=np.float64,
                                    )
                                )
                                for lower, upper in trace_bounds
                            )
                        )
                        times.append(scan.elapsed_time_seconds)
                        trace_responses.append(trace_response)
                        analyte_responses.append(analyte_response)
                    processing = self.project.processing
                    candidates = recommend_stable_intervals(
                        times,
                        trace_responses,
                        duration,
                        minimum_scans=processing.stability_minimum_scans,
                        candidate_count=processing.stability_candidate_count,
                        exclude_before_seconds=(
                            processing.stability_exclude_before_seconds
                        ),
                        exclude_after_seconds=processing.stability_exclude_after_seconds,
                        limits=StabilityLimits(
                            max_robust_cv_percent=(
                                processing.stability_max_robust_cv_percent
                            ),
                            max_relative_drift_percent=(
                                processing.stability_max_relative_drift_percent
                            ),
                            max_zero_fraction=processing.stability_max_zero_fraction,
                            minimum_response=processing.stability_minimum_response,
                        ),
                    )
                    diagnostics = tuple(
                        interval_diagnostics(
                            times,
                            analyte_responses,
                            candidate.start_seconds,
                            candidate.end_seconds,
                        )
                        for candidate in candidates
                    )
                    ambiguity_limit = processing.stability_ambiguity_score_delta_percent
                    ambiguous = False
                    if ambiguity_limit is not None and len(candidates) > 1:
                        best_scale = max(abs(candidates[0].score), 1e-12)
                        score_delta = (
                            100.0
                            * abs(candidates[1].score - candidates[0].score)
                            / best_scale
                        )
                        ambiguous = score_delta <= ambiguity_limit
                    assessments[str(sample.id)] = SampleStabilityAssessment(
                        candidates,
                        diagnostics,
                        tuple(times),
                        tuple(trace_responses),
                        tuple(analyte_responses),
                        ambiguous,
                    )
                except ProcessingCancelled:
                    raise
                except Exception as error:
                    failures[str(sample.id)] = str(error)
            self.succeeded.emit(
                StabilityRecommendationBundle(assessments, failures, datetime.now(UTC))
            )
        except ProcessingCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(str(error), traceback.format_exc())
        finally:
            self.finished.emit()

    @Slot()
    def cancel(self) -> None:
        self._cancelled.set()


def capture_source_provenance(
    path: Path,
    *,
    reader: MzMLReader | None = None,
    backend: MzMLBackend = MzMLBackend.PYMZML,
    is_cancelled=None,
) -> SourceFileProvenance:
    """Stream metadata and file bytes into a reproducibility snapshot."""

    resolved = path.expanduser().resolve()
    stat = resolved.stat()
    metadata = (reader or create_mzml_reader(backend)).inspect(resolved)
    digest = sha256_file(resolved, is_cancelled=is_cancelled)
    return SourceFileProvenance(
        file_size_bytes=stat.st_size,
        modified_time_ns=stat.st_mtime_ns,
        modified_time_utc=datetime.fromtimestamp(stat.st_mtime, UTC),
        sha256=digest,
        spectrum_count=metadata.spectrum_count or 0,
        ms_levels=sorted(metadata.ms_levels),
        is_centroided=metadata.is_centroided,
        captured_at_utc=datetime.now(UTC),
    )


def sha256_file(path: Path, *, is_cancelled=None, progress=None) -> str:
    """Stream a file into SHA-256 with optional progress and cancellation."""

    digest = sha256()
    bytes_read = 0
    with path.expanduser().resolve().open("rb") as source:
        while chunk := source.read(1024 * 1024):
            if is_cancelled is not None and is_cancelled():
                raise ProcessingCancelled("SHA-256 verification cancelled")
            digest.update(chunk)
            bytes_read += len(chunk)
            if progress is not None:
                progress(bytes_read)
    if progress is not None and bytes_read == 0:
        progress(0)
    return digest.hexdigest()
