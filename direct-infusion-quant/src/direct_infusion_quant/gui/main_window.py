"""Main Qt Widgets workflow for DirectInfusionQuant."""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError
from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from direct_infusion_quant.calibration import (
    CalibrationInput,
    CalibrationResult,
    calibrate_and_quantify,
)
from direct_infusion_quant.export import (
    export_csv_tables,
    export_excel_workbook,
    export_png_plots,
)
from direct_infusion_quant.gui.pages import (
    CalibrationPage,
    ExportPage,
    FilesPage,
    ResultsPage,
    TargetsPage,
    TimePage,
    _selected_summary,
)
from direct_infusion_quant.gui.relink_dialog import RelinkFilesDialog
from direct_infusion_quant.gui.workers import (
    HashVerificationBundle,
    HashVerificationWorker,
    MetadataWorker,
    ProcessingBundle,
    ProcessingWorker,
    StabilityRecommendationBundle,
    StabilityRecommendationWorker,
)
from direct_infusion_quant.models import (
    AnalysisProject,
    SampleRecord,
    StabilityAssessmentRecord,
    StabilityCandidateRecord,
    SummaryMethod,
)
from direct_infusion_quant.persistence import load_project, save_project
from direct_infusion_quant.processing import FileProcessingResult, WarningThresholds

LOGGER = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Ordered desktop workflow around validated scientific services."""

    PAGE_NAMES = [
        "1. Files and Samples",
        "2. Targets",
        "3. Time Window and Stability",
        "4. Processing Results",
        "5. Calibration",
        "6. Export",
    ]

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DirectInfusionQuant")
        self.resize(1400, 900)
        self.project = AnalysisProject(name="Untitled analysis")
        self.project_path: Path | None = None
        self.processing_results: dict[str, FileProcessingResult] = {}
        self.processing_failures: dict[str, str] = {}
        self.calibration_result: CalibrationResult | None = None
        self._thread: QThread | None = None
        self._worker = None
        self._relink_dialog: RelinkFilesDialog | None = None
        self._loading_pages = False

        self.files_page = FilesPage()
        self.targets_page = TargetsPage()
        self.time_page = TimePage()
        self.results_page = ResultsPage()
        self.calibration_page = CalibrationPage()
        self.export_page = ExportPage()
        self.pages = [
            self.files_page,
            self.targets_page,
            self.time_page,
            self.results_page,
            self.calibration_page,
            self.export_page,
        ]
        self.navigation = QListWidget()
        self.navigation.addItems(self.PAGE_NAMES)
        self.navigation.setFixedWidth(230)
        self.stack = QStackedWidget()
        for page in self.pages:
            self.stack.addWidget(page)
        self.navigation.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.navigation.setCurrentRow(0)

        splitter = QSplitter()
        splitter.addWidget(self.navigation)
        splitter.addWidget(self.stack)
        splitter.setStretchFactor(1, 1)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(110)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress_label = QLabel("Ready")
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.setToolTip(
            "Enabled during background work; processing checks cancellation "
            "between scans and files."
        )
        self.cancel_button.clicked.connect(self.cancel_work)
        status_row = QHBoxLayout()
        status_row.addWidget(self.progress_label)
        status_row.addWidget(self.progress, 1)
        status_row.addWidget(self.cancel_button)
        central_layout = QVBoxLayout()
        central_layout.addWidget(splitter, 1)
        central_layout.addLayout(status_row)
        central_layout.addWidget(self.log_view)
        central = QWidget()
        central.setLayout(central_layout)
        self.setCentralWidget(central)

        self._create_menu()
        self._connect_pages()
        self._load_project_into_pages()
        self._update_availability()
        self._update_hash_action()

    def _create_menu(self) -> None:
        self.file_menu = QMenu("&File", self)
        self.menuBar().addMenu(self.file_menu)
        actions = [
            ("&New", self.new_project),
            ("&Open Project…", self.open_project_dialog),
            ("&Save Project", self.save_project_action),
            ("&Import mzML…", self.import_mzml_dialog),
        ]
        for text, callback in actions:
            action = QAction(text, self)
            action.triggered.connect(callback)
            self.file_menu.addAction(action)
        self.relink_action = QAction("&Relink Missing mzML Files…", self)
        self.relink_action.setEnabled(False)
        self.relink_action.setToolTip(
            "Enabled when one or more project source files cannot be found."
        )
        self.relink_action.triggered.connect(self.show_relink_dialog)
        self.file_menu.addAction(self.relink_action)
        self.verify_hash_action = QAction("Verify SHA-256 &Now…", self)
        self.verify_hash_action.setEnabled(False)
        self.verify_hash_action.setToolTip(
            "Rehash source files byte-for-byte and compare with saved provenance."
        )
        self.verify_hash_action.triggered.connect(self.verify_sha256_now)
        self.file_menu.addAction(self.verify_hash_action)
        for text, callback in [
            ("&Export…", lambda: self.navigation.setCurrentRow(5)),
            ("E&xit", self.close),
        ]:
            action = QAction(text, self)
            action.triggered.connect(callback)
            self.file_menu.addAction(action)

    def _connect_pages(self) -> None:
        self.files_page.add_requested.connect(self.import_mzml_dialog)
        self.files_page.inspect_requested.connect(self.inspect_metadata)
        self.files_page.backend_changed.connect(self._backend_changed)
        self.files_page.table.itemChanged.connect(self._sample_table_changed)
        self.files_page.calibration_inputs_changed.connect(self._update_availability)
        self.targets_page.table.itemChanged.connect(self._target_changed)
        self.targets_page.definition_changed.connect(self._target_changed)
        self.targets_page.name.textChanged.connect(self._target_changed)
        self.time_page.process_requested.connect(self.start_processing)
        self.time_page.recommendation_requested.connect(self.assess_stable_periods)
        self.time_page.intervals_confirmed.toggled.connect(
            lambda: self._update_availability()
        )
        self.time_page.cancel_requested.connect(self.cancel_work)
        self.calibration_page.run_requested.connect(self.run_calibration)
        self.export_page.csv_requested.connect(self.export_csv)
        self.export_page.excel_requested.connect(self.export_excel)
        self.export_page.project_requested.connect(self.save_project_as)
        self.export_page.plots_requested.connect(self.export_plots)

    def _backend_changed(self, _backend) -> None:
        self.time_page.intervals_confirmed.setChecked(False)
        if not self.processing_results and self.calibration_result is None:
            return
        self.processing_results.clear()
        self.processing_failures.clear()
        self.calibration_result = None
        self.results_page.file_table.setRowCount(0)
        self.results_page.scan_table.setRowCount(0)
        self.calibration_page.summary.setText("Calibration not fitted")
        self._update_availability()
        self._log(
            "Reader backend changed; previous processing and calibration results "
            "were cleared. Reprocess all files with the selected backend."
        )

    def _sample_table_changed(self, item) -> None:
        if not self._loading_pages and item.column() in {0, 8}:
            self.time_page.intervals_confirmed.setChecked(False)
        if not self._loading_pages and item.column() in {0, 3, 4, 5}:
            self._update_availability()

    def _target_changed(self, *_args) -> None:
        if not self._loading_pages:
            self.time_page.intervals_confirmed.setChecked(False)

    def new_project(self) -> None:
        if self._thread is not None:
            self._show_error(
                "Work is running", "Cancel background work before creating a project."
            )
            return
        self.project = AnalysisProject(name="Untitled analysis")
        self.project_path = None
        self.processing_results.clear()
        self.processing_failures.clear()
        self.calibration_result = None
        self._load_project_into_pages()
        self._log("Created a new analysis project.")

    def import_mzml_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import centroided mzML files", "", "mzML files (*.mzML *.mzML.gz)"
        )
        if paths:
            self.add_mzml_files([Path(path) for path in paths])

    def add_mzml_files(self, paths: list[Path]) -> None:
        self.files_page.add_paths(paths)
        self.time_page.intervals_confirmed.setChecked(False)
        self.processing_results.clear()
        self.calibration_result = None
        self._update_availability()
        self._log(f"Added {len(paths)} mzML path(s).")

    def inspect_metadata(self) -> None:
        try:
            paths = self.files_page.included_paths()
            backend = self.files_page.selected_backend()
        except ValueError as error:
            self._show_validation_error(error)
            return
        if not paths:
            self._show_error("No files", "Add and include at least one mzML file.")
            return
        self.project.processing.mzml_backend = backend
        worker = MetadataWorker(paths, backend)
        worker.metadata.connect(self._metadata_received)
        worker.failed.connect(self._metadata_failed)
        worker.progress.connect(self._metadata_progress)
        self._start_worker(worker)

    def start_processing(self) -> None:
        try:
            project = self._sync_project_from_pages()
            if not project.samples or not any(
                sample.included for sample in project.samples
            ):
                raise ValueError("Add and include at least one mzML file.")
            if project.processing.time_end_seconds is None:
                raise ValueError(
                    "Set a default acquisition-time end before processing files."
                )
            if project.active_analyte_id is None:
                raise ValueError("Define an active analyte and explicit quantifier.")
        except (ValidationError, ValueError) as error:
            self._show_validation_error(error)
            return
        self.project = project
        thresholds = WarningThresholds(
            high_rsd_percent=(
                self.time_page.rsd_limit.value()
                if self.time_page.rsd_enabled.isChecked()
                else None
            ),
            frequent_zero_fraction=(
                self.time_page.zero_limit.value() / 100.0
                if self.time_page.zero_enabled.isChecked()
                else None
            ),
        )
        self.processing_results.clear()
        self.processing_failures.clear()
        self.calibration_result = None
        worker = ProcessingWorker(project, thresholds)
        worker.progress.connect(self._processing_progress)
        worker.log.connect(self._log)
        worker.succeeded.connect(self._processing_succeeded)
        worker.failed.connect(self._worker_failed)
        worker.cancelled.connect(
            lambda: self._log("Processing cancelled; results unchanged.")
        )
        self._start_worker(worker)

    def assess_stable_periods(self) -> None:
        """Report fixed-duration per-file recommendations without applying them."""

        try:
            project = self._sync_project_from_pages()
            if project.processing.time_end_seconds is None:
                raise ValueError("Set a finite default interval first.")
            if project.active_analyte_id is None:
                raise ValueError("Define an active analyte and explicit quantifier.")
        except (ValidationError, ValueError) as error:
            self._show_validation_error(error)
            return
        worker = StabilityRecommendationWorker(project)
        worker.progress.connect(self._processing_progress)
        worker.succeeded.connect(self._stability_recommendations_succeeded)
        worker.failed.connect(self._worker_failed)
        worker.cancelled.connect(
            lambda: self._log("Stable-period assessment cancelled.")
        )
        self._start_worker(worker)

    def _stability_recommendations_succeeded(
        self, bundle: StabilityRecommendationBundle
    ) -> None:
        project = self._sync_project_from_pages()
        duration = (
            project.processing.time_end_seconds - project.processing.time_start_seconds
        )
        lines = [
            f"Fixed duration for every recommendation: {duration:g} s.",
            f"Stability trace: {project.processing.stability_trace_mode.value}.",
            "Recommendations were not applied:",
        ]
        samples = {str(sample.id): sample for sample in project.samples}
        for sample_id, assessment in bundle.assessments.items():
            name = samples[sample_id].sample_name
            samples[sample_id].stability_assessment = StabilityAssessmentRecord(
                assessed_at_utc=bundle.assessed_at_utc,
                trace_mode=project.processing.stability_trace_mode,
                ambiguous=assessment.ambiguous,
                candidates=[
                    StabilityCandidateRecord(
                        rank=rank,
                        start_seconds=candidate.start_seconds,
                        end_seconds=candidate.end_seconds,
                        scan_count=candidate.scan_count,
                        trace_median_response=candidate.median_response,
                        trace_robust_cv_percent=candidate.robust_cv_percent,
                        trace_relative_drift_percent=(candidate.relative_drift_percent),
                        trace_zero_fraction=candidate.zero_fraction,
                        trace_signal_fraction=(candidate.signal_fraction_of_reference),
                        score=candidate.score,
                        limit_failures=list(candidate.limit_failures),
                        analyte_median_response=analyte.median_response,
                        analyte_robust_cv_percent=analyte.robust_cv_percent,
                        analyte_relative_drift_percent=(analyte.relative_drift_percent),
                        analyte_zero_fraction=analyte.zero_fraction,
                    )
                    for rank, (candidate, analyte) in enumerate(
                        zip(
                            assessment.candidates,
                            assessment.analyte_diagnostics,
                            strict=True,
                        ),
                        start=1,
                    )
                ],
            )
            lines.append(
                f"{name}:"
                + (" AMBIGUOUS CANDIDATE RANKING" if assessment.ambiguous else "")
            )
            for rank, (candidate, analyte) in enumerate(
                zip(
                    assessment.candidates,
                    assessment.analyte_diagnostics,
                    strict=True,
                ),
                start=1,
            ):
                limit_text = (
                    "meets configured limits"
                    if candidate.meets_limits
                    else "fails " + ", ".join(candidate.limit_failures)
                )
                lines.append(
                    f"  {rank}. {candidate.start_seconds:.2f}–"
                    f"{candidate.end_seconds:.2f} s; trace CV "
                    f"{candidate.robust_cv_percent:.1f}%; trace drift "
                    f"{candidate.relative_drift_percent:.1f}%; trace zeros "
                    f"{candidate.zero_fraction:.1%}; analyte CV "
                    f"{analyte.robust_cv_percent:.1f}%; analyte drift "
                    f"{analyte.relative_drift_percent:.1f}%; score "
                    f"{candidate.score:.2f}; {limit_text}."
                )
        for sample_id, error in bundle.failures.items():
            lines.append(f"{samples[sample_id].sample_name}: unavailable ({error}).")
        message = "\n".join(lines)
        self.project = project
        self.time_page.plot_stability_assessment(
            bundle.assessments,
            {sample_id: sample.sample_name for sample_id, sample in samples.items()},
        )
        self._log(message)
        QMessageBox.information(self, "Stable-period assessment", message)

    def run_calibration(self) -> None:
        if not self.processing_results:
            self._show_error(
                "No processing results", "Process files before fitting calibration."
            )
            return
        try:
            self.project = self._sync_project_from_pages()
            if not self.project.processing.stability_intervals_confirmed:
                raise ValueError(
                    "Review and explicitly confirm every included sample's "
                    "fixed-duration interval before calibration."
                )
            method = self.calibration_page.response_method.currentData()
            self.project.processing.summary_method = method
            self.project.calibration = self.calibration_page.settings()
            inputs: list[CalibrationInput] = []
            for sample in self.project.samples:
                result = self.processing_results.get(str(sample.id))
                if result is None:
                    continue
                summary = _selected_summary(result, self.project)
                response = {
                    SummaryMethod.MEDIAN: summary.median,
                    SummaryMethod.MEAN: summary.mean,
                    SummaryMethod.TRIMMED_MEAN: summary.trimmed_mean,
                }[method]
                inputs.append(
                    CalibrationInput(
                        sample_id=str(sample.id),
                        sample_type=sample.sample_type,
                        file_response=response,
                        concentration=sample.concentration,
                        concentration_unit=sample.concentration_unit,
                        dilution_factor=sample.dilution_factor,
                        replicate_group=sample.replicate_group,
                    )
                )
            self.calibration_result = calibrate_and_quantify(
                inputs, self.project.calibration
            )
            self.calibration_page.show_result(self.calibration_result, self.project)
            for warning in self.calibration_result.warnings:
                self._log(f"Calibration warning [{warning.code}]: {warning.message}")
            self._update_availability()
            self._log("Calibration completed.")
        except Exception as error:
            self._show_validation_error(error)

    def open_project_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open DirectInfusionQuant project", "", "Project JSON (*.json)"
        )
        if path:
            self.open_project_path(Path(path))

    def open_project_path(self, path: Path) -> bool:
        try:
            project = load_project(path)
        except Exception as error:
            self._show_error("Could not open project", str(error))
            return False
        self.project = project
        self.project_path = path.resolve()
        self.processing_results.clear()
        self.processing_failures.clear()
        self.calibration_result = None
        self._load_project_into_pages()
        missing = [
            sample.path for sample in project.samples if not sample.path.exists()
        ]
        self._log(f"Opened project: {self.project_path}")
        if missing:
            self._log(
                f"Warning: {len(missing)} source mzML file(s) are missing; "
                "settings were restored without assuming file availability."
            )
            self.relink_action.setEnabled(True)
            QTimer.singleShot(0, self.show_relink_dialog)
        else:
            self.relink_action.setEnabled(False)
        return True

    def show_relink_dialog(self) -> None:
        """Show missing sources and accept only explicit, existing replacements."""

        try:
            self.project = self._sync_project_from_pages()
        except (ValidationError, ValueError) as error:
            self._show_validation_error(error)
            return
        missing = [
            sample for sample in self.project.samples if not sample.path.exists()
        ]
        if not missing:
            self.relink_action.setEnabled(False)
            self._log("All project source mzML files are currently available.")
            return
        if self._relink_dialog is not None and self._relink_dialog.isVisible():
            self._relink_dialog.raise_()
            self._relink_dialog.activateWindow()
            return
        dialog = RelinkFilesDialog(missing, self)
        dialog.relink_requested.connect(self.apply_relinks)
        dialog.finished.connect(lambda: setattr(self, "_relink_dialog", None))
        self._relink_dialog = dialog
        dialog.open()

    def apply_relinks(self, replacements: dict[UUID, Path]) -> None:
        """Apply validated source-path replacements and invalidate derived results."""

        changed = 0
        for sample in self.project.samples:
            replacement = replacements.get(sample.id)
            if replacement is None:
                continue
            resolved = replacement.expanduser().resolve()
            if not resolved.is_file():
                self._log(f"Relink rejected for {sample.sample_name}: file is missing.")
                continue
            sample.path = resolved
            sample.source_provenance = None
            changed += 1
        if changed == 0:
            return
        self.processing_results.clear()
        self.processing_failures.clear()
        self.calibration_result = None
        self.files_page.load_samples(self.project.samples)
        remaining = [
            sample for sample in self.project.samples if not sample.path.exists()
        ]
        self.relink_action.setEnabled(bool(remaining))
        self._update_availability()
        self._update_hash_action()
        self._log(
            f"Relinked {changed} source file(s); in-memory results were cleared. "
            "Save the project explicitly to preserve the new paths."
        )

    def verify_sha256_now(self) -> None:
        """Explicitly rehash sources without updating saved reference values."""

        if not self.project.samples:
            self._show_error("No source files", "Import or reopen source files first.")
            return
        worker = HashVerificationWorker(self.project)
        worker.progress.connect(self._hash_progress)
        worker.succeeded.connect(self._hash_verification_succeeded)
        worker.failed.connect(self._worker_failed)
        worker.cancelled.connect(
            lambda: self._log("SHA-256 verification cancelled; no hashes changed.")
        )
        self._start_worker(worker)

    def _hash_progress(
        self,
        file_index: int,
        total: int,
        bytes_read: int,
        file_size: int,
        sample_name: str,
    ) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(file_index - 1)
        self.progress_label.setText(
            f"Verifying {file_index}/{total}: {sample_name}; "
            f"{bytes_read / (1024 * 1024):.1f}/{file_size / (1024 * 1024):.1f} MiB"
        )

    def _hash_verification_succeeded(self, bundle: HashVerificationBundle) -> None:
        sample_names = {
            str(sample.id): sample.sample_name for sample in self.project.samples
        }
        counts: dict[str, int] = {}
        for result in bundle.results:
            counts[result.status] = counts.get(result.status, 0) + 1
            name = sample_names[result.sample_id]
            if result.status == "verified":
                message = "SHA-256 verified byte-for-byte"
                warning = False
            elif result.status == "mismatch":
                message = "SHA-256 MISMATCH: source contents differ"
                warning = True
            elif result.status == "missing":
                message = "SHA-256 not verified: source file is missing"
                warning = True
            else:
                message = "SHA-256 not verified: no saved reference hash"
                warning = True
            self.files_page.set_status(UUID(result.sample_id), message, warning)
            self._log(f"{name}: {message}")
        self._log(
            "SHA-256 verification completed without changing saved hashes: "
            + ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
        )

    def save_project_action(self) -> None:
        if self.project_path is None:
            self.save_project_as()
        else:
            self.save_project_path(self.project_path)

    def save_project_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save DirectInfusionQuant project",
            "analysis.diq.json",
            "JSON (*.json)",
        )
        if path:
            self.save_project_path(Path(path))

    def save_project_path(self, path: Path) -> bool:
        try:
            self.project = self._sync_project_from_pages()
            save_project(self.project, path)
        except Exception as error:
            self._show_error("Could not save project", str(error))
            return False
        self.project_path = path.resolve()
        self._log(f"Saved project: {self.project_path}")
        return True

    def export_csv(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Export CSV tables")
        if not directory:
            return
        try:
            paths = export_csv_tables(
                Path(directory),
                self._sync_project_from_pages(),
                self.processing_results,
                self.calibration_result,
            )
            self._log(f"Exported {len(paths)} CSV tables to {directory}.")
        except Exception as error:
            self._show_error("CSV export failed", str(error))

    def export_excel(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Excel workbook", "analysis-results.xlsx", "Excel (*.xlsx)"
        )
        if not path:
            return
        try:
            export_excel_workbook(
                Path(path),
                self._sync_project_from_pages(),
                self.processing_results,
                self.calibration_result,
            )
            self._log(f"Exported Excel workbook: {path}")
        except Exception as error:
            self._show_error("Excel export failed", str(error))

    def export_plots(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Export plots as PNG")
        if not directory:
            return
        destination = Path(directory)
        try:
            figures = {}
            if self.processing_results:
                figures["spray-response"] = self.time_page.canvas.figure
            if self.calibration_result is not None:
                figures["calibration"] = self.calibration_page.calibration_canvas.figure
                figures["residuals"] = self.calibration_page.residual_canvas.figure
            export_png_plots(destination, figures)
            self._log(f"Exported PNG plots to {destination}.")
        except Exception as error:
            self._show_error("Plot export failed", str(error))

    def cancel_work(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.progress_label.setText("Cancelling…")

    def _start_worker(self, worker) -> None:
        if self._thread is not None:
            self._show_error(
                "Work already running", "Wait for or cancel the active task."
            )
            return
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        worker.finished.connect(self._worker_completed)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, 0)
        self.progress_label.setText("Working…")
        thread.start()

    def _worker_completed(self) -> None:
        self.cancel_button.setEnabled(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.progress_label.setText("Ready")

    def _thread_finished(self) -> None:
        self._thread = None
        self._worker = None

    def _processing_succeeded(self, bundle: ProcessingBundle) -> None:
        for sample in self.project.samples:
            provenance = bundle.provenance.get(str(sample.id))
            if provenance is not None:
                sample.source_provenance = provenance
                self._metadata_received(sample.path, provenance)
        self.project.last_processing_timestamp_utc = bundle.processed_at_utc
        self.processing_results = bundle.results
        self.processing_failures = bundle.failures
        self.results_page.show_results(bundle.results, bundle.failures, self.project)
        names = {str(sample.id): sample.sample_name for sample in self.project.samples}
        self.time_page.plot_results(bundle.results, names, self.project)
        for sample_id, error in bundle.failures.items():
            self.files_page.set_status(
                UUID(sample_id), f"Excluded: {error}", warning=True
            )
        self.navigation.setCurrentRow(3)
        self._update_availability()
        self._log(
            f"Processing complete: {len(bundle.results)} file(s), "
            f"{len(bundle.failures)} visible exclusion(s)."
        )

    def _processing_progress(
        self, file_index: int, total: int, scans: int, sample_name: str
    ) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(file_index - 1)
        self.progress_label.setText(
            f"File {file_index}/{total}: {sample_name}; {scans} spectra read"
        )

    def _metadata_received(self, path: Path, provenance) -> None:
        metadata = provenance
        self.files_page.set_source_provenance(path, provenance)
        for sample in self.project.samples:
            if sample.path.expanduser().resolve() == path.expanduser().resolve():
                sample.source_provenance = provenance
        try:
            self.project.samples = self._samples_from_files_page()
        except (AttributeError, TypeError, ValidationError, ValueError) as error:
            # Metadata inspection deliberately remains independent of draft sample
            # classification and concentration fields. The file row retains the
            # provenance for the next successful project synchronization.
            LOGGER.debug("metadata provenance pending project sync: %s", error)
        mode = (
            "centroided"
            if metadata.is_centroided is True
            else "PROFILE MODE WARNING"
            if metadata.is_centroided is False
            else "centroid/profile mode unknown"
        )
        text = (
            f"{metadata.spectrum_count} spectra; MS levels "
            f"{sorted(metadata.ms_levels)}; {mode}"
        )
        self.files_page.set_metadata(path, text, metadata.is_centroided is not True)
        if metadata.is_centroided is not True:
            self._log(f"Metadata warning for {path.name}: {mode}")
        self._update_hash_action()

    def _metadata_failed(self, path: str, error: str) -> None:
        self.files_page.set_metadata(Path(path), f"Inspection failed: {error}", True)
        self._log(f"Metadata inspection error for {path}: {error}")

    def _metadata_progress(self, index: int, total: int, name: str) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(index - 1)
        self.progress_label.setText(f"Inspecting {index}/{total}: {name}")

    def _worker_failed(self, message: str, technical: str) -> None:
        LOGGER.error("background_worker_failed\n%s", technical)
        self._log(f"Error: {message}")
        self._show_error("Background processing failed", message)

    def _samples_from_files_page(self) -> list[SampleRecord]:
        samples = self.files_page.samples()
        for row, sample in enumerate(samples):
            self.files_page.table.item(row, 1).setData(
                Qt.ItemDataRole.UserRole + 1, str(sample.id)
            )
        existing_samples = {sample.id: sample for sample in self.project.samples}
        for sample in samples:
            existing_sample = existing_samples.get(sample.id)
            if (
                existing_sample is not None
                and existing_sample.path == sample.path
                and existing_sample.source_provenance is not None
                and sample.source_provenance is None
            ):
                sample.source_provenance = existing_sample.source_provenance
            if existing_sample is not None and existing_sample.path == sample.path:
                sample.stability_assessment = existing_sample.stability_assessment
        return samples

    def _sync_project_from_pages(self) -> AnalysisProject:
        samples = self._samples_from_files_page()
        existing = self.project.analytes[0] if self.project.analytes else None
        analytes = []
        active_id = None
        if self.targets_page.name.text().strip() or self.targets_page.table.rowCount():
            analyte = self.targets_page.analyte(existing.id if existing else None)
            analytes = [analyte]
            active_id = analyte.id
            self.time_page.load_reference_windows(analyte)
        method = self.calibration_page.response_method.currentData()
        processing = self.time_page.settings(method, self.files_page.selected_backend())
        return AnalysisProject(
            schema_version=self.project.schema_version,
            application_version=self.project.application_version,
            id=self.project.id,
            name=self.project.name,
            samples=samples,
            analytes=analytes,
            active_analyte_id=active_id,
            processing=processing,
            calibration=self.calibration_page.settings(),
            last_processing_timestamp_utc=self.project.last_processing_timestamp_utc,
        )

    def _load_project_into_pages(self) -> None:
        self._loading_pages = True
        try:
            self.files_page.load_samples(self.project.samples)
            self.files_page.load_backend(self.project.processing.mzml_backend)
            analyte = self.project.analytes[0] if self.project.analytes else None
            self.targets_page.load_analyte(analyte)
            self.time_page.load_reference_windows(analyte)
            self.time_page.load_settings(self.project.processing)
            self.calibration_page.load_settings(
                self.project.calibration, self.project.processing
            )
        finally:
            self._loading_pages = False
        self.results_page.file_table.setRowCount(0)
        self.results_page.scan_table.setRowCount(0)
        self.calibration_page.summary.setText("Calibration not fitted")
        self._update_availability()
        self._update_hash_action()

    def _update_availability(self) -> None:
        self.calibration_page.set_standard_level_count(
            self.files_page.standard_level_count()
        )
        self.export_page.set_availability(
            bool(self.processing_results), self.calibration_result is not None
        )
        self.calibration_page.set_run_availability(
            bool(self.processing_results),
            self.time_page.intervals_confirmed.isChecked(),
        )

    def _update_hash_action(self) -> None:
        available = any(
            sample.source_provenance is not None for sample in self.project.samples
        )
        self.verify_hash_action.setEnabled(available)
        self.verify_hash_action.setToolTip(
            "Rehash source files byte-for-byte and compare with saved provenance."
            if available
            else "No saved source SHA-256 records are available yet."
        )

    def _show_validation_error(self, error: Exception) -> None:
        self._log(f"Validation error: {error}")
        self._show_error("Invalid analysis settings", str(error))

    def _show_error(self, title: str, message: str) -> None:
        LOGGER.error("%s: %s", title, message)
        QMessageBox.critical(self, title, message)

    def _log(self, message: str) -> None:
        LOGGER.info(message)
        self.log_view.appendPlainText(message)
        self.statusBar().showMessage(message, 5000)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._thread is not None:
            self.cancel_work()
            event.ignore()
            self._log("Cancellation requested; close again when work has stopped.")
            return
        event.accept()
