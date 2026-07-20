"""pytest-qt smoke tests for the first usable desktop workflow."""

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHeaderView

from direct_infusion_quant.gui.main_window import MainWindow
from direct_infusion_quant.gui.pages import CONCENTRATION_UNITS, TimePage
from direct_infusion_quant.gui.workers import (
    HashVerificationWorker,
    ProcessingWorker,
    StabilityRecommendationWorker,
)
from direct_infusion_quant.io.base import MzMLMetadata, SpectrumRecord
from direct_infusion_quant.models import (
    AnalysisProject,
    AnalyteTarget,
    ExtractionWindow,
    MzMLBackend,
    ProcessingSettings,
    QuantifierMode,
    RegressionModel,
    SampleRecord,
    SampleType,
    SourceFileProvenance,
    StabilityTraceMode,
    ToleranceUnit,
)
from direct_infusion_quant.processing import WarningThresholds


def configure_valid_project(window: MainWindow, mzml_path: Path) -> None:
    window.add_mzml_files([mzml_path])
    files = window.files_page.table
    sample_type = files.cellWidget(0, 3)
    sample_type.setCurrentIndex(sample_type.findData(SampleType.STANDARD))
    files.item(0, 4).setText("1.5")
    unit = files.cellWidget(0, 5)
    unit.setCurrentIndex(unit.findData("mg/mL"))
    files.item(0, 6).setText("2")
    window.targets_page.name.setText("peptide A")
    window.targets_page.add_window()
    window.targets_page.table.item(0, 1).setCheckState(Qt.CheckState.Checked)
    window.time_page.start.setValue(0)
    window.time_page.end.setValue(10)


def test_main_window_has_ordered_workflow_and_explained_disabled_exports(
    qtbot,
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    assert [
        window.navigation.item(index).text()
        for index in range(window.navigation.count())
    ] == MainWindow.PAGE_NAMES
    for index in range(6):
        window.navigation.setCurrentRow(index)
        assert window.stack.currentIndex() == index
    assert not window.export_page.csv_button.isEnabled()
    assert "Process files" in window.export_page.csv_button.toolTip()
    menu_actions = [
        action.text().replace("&", "")
        for action in window.menuBar().actions()[0].menu().actions()
    ]
    assert menu_actions == [
        "New",
        "Open Project…",
        "Save Project",
        "Import mzML…",
        "Relink Missing mzML Files…",
        "Verify SHA-256 Now…",
        "Export…",
        "Exit",
    ]


def test_controlled_sample_target_and_calibration_choices(
    qtbot, tmp_path: Path
) -> None:
    path = tmp_path / "controls.mzML"
    path.touch()
    window = MainWindow()
    qtbot.addWidget(window)
    window.add_mzml_files([path])

    sample_type = window.files_page.table.cellWidget(0, 3)
    assert not sample_type.isEditable()
    assert [sample_type.itemText(index) for index in range(sample_type.count())] == [
        "Unknown (sample)",
        "Standard",
        "Blank",
        "QC",
    ]
    concentration_unit = window.files_page.table.cellWidget(0, 5)
    assert not concentration_unit.isEditable()
    assert [
        concentration_unit.itemData(index)
        for index in range(1, concentration_unit.count())
    ] == list(CONCENTRATION_UNITS)

    add_button = next(
        button
        for button in window.targets_page.findChildren(
            type(window.export_page.csv_button)
        )
        if button.text() == "Add window"
    )
    qtbot.mouseClick(add_button, Qt.MouseButton.LeftButton)
    assert window.targets_page.table.rowCount() == 1
    assert (
        window.targets_page.table.item(0, 0).flags() & Qt.ItemFlag.ItemIsUserCheckable
    )
    assert (
        window.targets_page.table.item(0, 1).flags() & Qt.ItemFlag.ItemIsUserCheckable
    )
    tolerance_unit = window.targets_page.table.cellWidget(0, 5)
    charge = window.targets_page.table.cellWidget(0, 6)
    assert [tolerance_unit.itemText(i) for i in range(tolerance_unit.count())] == [
        "Da",
        "ppm",
    ]
    assert charge.itemData(0) is None
    assert charge.itemData(charge.count() - 1) == 15
    target_header = window.targets_page.table.horizontalHeader()
    assert target_header.sectionResizeMode(2) is QHeaderView.ResizeMode.Stretch
    assert target_header.sectionResizeMode(6) is QHeaderView.ResizeMode.ResizeToContents

    window.calibration_page.set_standard_level_count(6)
    quartic_index = window.calibration_page.regression.findData(RegressionModel.QUARTIC)
    quartic_item = window.calibration_page.regression.model().item(quartic_index)
    assert not quartic_item.isEnabled()
    window.calibration_page.set_standard_level_count(7)
    assert quartic_item.isEnabled()

    window.targets_page.table.selectRow(0)
    window.targets_page.remove_selected()
    assert window.targets_page.table.rowCount() == 0


def test_new_window_ids_survive_repeated_project_synchronization(
    qtbot, tmp_path: Path
) -> None:
    mzml = tmp_path / "new-window.mzML"
    mzml.touch()
    window = MainWindow()
    qtbot.addWidget(window)
    configure_valid_project(window, mzml)

    first = window._sync_project_from_pages()
    window.project = first
    second = window._sync_project_from_pages()

    first_window = first.analytes[0].windows[0]
    second_window = second.analytes[0].windows[0]
    assert second_window.id == first_window.id
    assert second.analytes[0].quantifier_window_ids == [first_window.id]


def test_edit_save_and_reopen_restores_settings_without_embedding_mzml(
    qtbot, tmp_path: Path
) -> None:
    mzml = tmp_path / "large-source.mzML"
    mzml.write_bytes(b"external-data-marker")
    project_path = tmp_path / "analysis.diq.json"
    window = MainWindow()
    qtbot.addWidget(window)
    configure_valid_project(window, mzml)

    assert window.save_project_path(project_path)
    saved_text = project_path.read_text(encoding="utf-8")
    assert "external-data-marker" not in saved_text
    assert str(mzml.resolve()).replace("\\", "\\\\") in saved_text

    window.new_project()
    assert window.files_page.table.rowCount() == 0
    assert window.open_project_path(project_path)
    assert window.files_page.table.rowCount() == 1
    assert window.targets_page.name.text() == "peptide A"
    assert window.files_page.table.item(0, 6).text() == "2.0"
    assert window.time_page.end.value() == 10


def test_reopen_marks_missing_source_without_discarding_settings(
    qtbot, tmp_path: Path
) -> None:
    mzml = tmp_path / "will-go-missing.mzML"
    mzml.touch()
    project_path = tmp_path / "analysis.diq.json"
    window = MainWindow()
    qtbot.addWidget(window)
    configure_valid_project(window, mzml)
    assert window.save_project_path(project_path)
    mzml.unlink()

    reopened = MainWindow()
    qtbot.addWidget(reopened)
    assert reopened.open_project_path(project_path)
    assert reopened.files_page.table.item(0, 9).text() == "Source file is missing"
    assert reopened.targets_page.name.text() == "peptide A"
    qtbot.waitUntil(
        lambda: (
            reopened._relink_dialog is not None and reopened._relink_dialog.isVisible()
        ),
        timeout=1000,
    )
    dialog = reopened._relink_dialog
    assert dialog is not None
    assert reopened.relink_action.isEnabled()

    replacement_directory = tmp_path / "moved"
    replacement_directory.mkdir()
    replacement = replacement_directory / mzml.name
    replacement.touch()
    assert dialog.match_directory(replacement_directory) == 1
    qtbot.mouseClick(dialog.apply_button, Qt.MouseButton.LeftButton)
    assert reopened.project.samples[0].path == replacement.resolve()
    assert reopened.project.processing.time_end_seconds == 10
    assert reopened.project.analytes[0].name == "peptide A"
    assert not reopened.relink_action.isEnabled()
    assert reopened.files_page.table.item(0, 9).text() == "Not inspected"

    assert reopened.save_project_path(project_path)
    reopened.new_project()
    assert reopened.open_project_path(project_path)
    assert reopened.project.samples[0].path == replacement.resolve()
    assert reopened._relink_dialog is None


def test_processing_worker_returns_scan_results_without_gui_access(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "synthetic.mzML"
    path.touch()
    target = ExtractionWindow(
        name="quantifier",
        target_mz=500,
        tolerance=0.1,
        tolerance_unit=ToleranceUnit.DA,
    )
    analyte = AnalyteTarget(
        name="peptide",
        windows=[target],
        quantifier_mode=QuantifierMode.SINGLE,
        quantifier_window_ids=[target.id],
    )
    sample = SampleRecord(
        path=path,
        sample_name="unknown",
        sample_type=SampleType.UNKNOWN,
    )
    project = AnalysisProject(
        name="worker",
        samples=[sample],
        analytes=[analyte],
        active_analyte_id=analyte.id,
        processing=ProcessingSettings(time_start_seconds=0, time_end_seconds=2),
    )

    class FakeReader:
        def inspect(self, path: Path):
            return MzMLMetadata(path, 3, frozenset({1}), True)

        def iter_spectra(self, _path: Path):
            for index, response in enumerate([5.0, 7.0, 9.0]):
                yield SpectrumRecord(
                    native_id=f"scan={index}",
                    index=index,
                    ms_level=1,
                    elapsed_time_seconds=float(index),
                    mz=np.asarray([500.0]),
                    intensity=np.asarray([response]),
                )

    monkeypatch.setattr(
        "direct_infusion_quant.gui.workers.create_mzml_reader",
        lambda _backend: FakeReader(),
    )
    worker = ProcessingWorker(project, WarningThresholds())
    with qtbot.waitSignal(worker.succeeded, timeout=1000) as signal:
        worker.run()
    bundle = signal.args[0]
    result = bundle.results[str(sample.id)]
    assert [scan.window_responses[target.id] for scan in result.scans] == [5, 7, 9]
    assert result.quantification_response == 7
    assert bundle.provenance[str(sample.id)].sha256 == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    assert bundle.failures == {}

    page = TimePage()
    qtbot.addWidget(page)
    names = {str(sample.id): sample.sample_name}
    results = {str(sample.id): result}
    page.plot_results(results, names, project)
    page.plot_results(results, names, project)
    assert len(page.canvas.axes.lines) >= 3


def test_stability_plot_uses_scrollable_readable_sample_panels(qtbot) -> None:
    page = TimePage()
    qtbot.addWidget(page)
    assessments = {}
    names = {}
    for index in range(6):
        sample_id = f"sample-{index}"
        names[sample_id] = f"Standard {index + 1}"
        assessments[sample_id] = SimpleNamespace(
            times_seconds=np.asarray([0.0, 30.0, 60.0, 90.0]),
            trace_responses=np.asarray(
                [1000.0 + index, 1100.0 + index, 1050.0 + index, 1025.0 + index]
            ),
            candidates=[
                SimpleNamespace(start_seconds=20.0, end_seconds=50.0),
                SimpleNamespace(start_seconds=55.0, end_seconds=85.0),
            ],
        )

    page.plot_stability_assessment(assessments, names)

    axes = page.canvas.figure.axes
    assert page.plot_scroll.widget() is page.canvas
    assert len(axes) == 6
    assert page.canvas.minimumHeight() >= 6 * page.STABILITY_PANEL_HEIGHT
    assert [axis.get_title(loc="left") for axis in axes] == list(names.values())
    assert not any(label.get_visible() for label in axes[0].get_xticklabels())
    assert any(label.get_visible() for label in axes[-1].get_xticklabels())
    assert all(not axis.get_ylabel() for axis in axes)

    page.plot_results({}, {}, AnalysisProject(name="plot reset"))
    assert page.canvas.minimumHeight() == page.BASE_PLOT_HEIGHT


def test_stability_worker_uses_explicit_reference_and_returns_ranked_candidates(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "stability.mzML"
    path.touch()
    quantifier = ExtractionWindow(
        name="analyte",
        target_mz=500,
        tolerance=0.1,
        tolerance_unit=ToleranceUnit.DA,
    )
    reference = ExtractionWindow(
        name="internal standard",
        target_mz=600,
        tolerance=0.1,
        tolerance_unit=ToleranceUnit.DA,
    )
    analyte = AnalyteTarget(
        name="peptide",
        windows=[quantifier, reference],
        quantifier_mode=QuantifierMode.SINGLE,
        quantifier_window_ids=[quantifier.id],
    )
    sample = SampleRecord(
        path=path, sample_name="sample", sample_type=SampleType.UNKNOWN
    )
    project = AnalysisProject(
        name="stability",
        samples=[sample],
        analytes=[analyte],
        active_analyte_id=analyte.id,
        processing=ProcessingSettings(
            time_start_seconds=0,
            time_end_seconds=10,
            stability_trace_mode=StabilityTraceMode.REFERENCE_SIC,
            stability_reference_window_id=reference.id,
            stability_candidate_count=2,
            stability_ambiguity_score_delta_percent=1000,
        ),
    )

    class FakeReader:
        def iter_spectra(self, _path: Path):
            for index in range(61):
                reference_response = 100.0 if 20 <= index <= 30 else 50.0 + index
                yield SpectrumRecord(
                    native_id=f"scan={index}",
                    index=index,
                    ms_level=1,
                    elapsed_time_seconds=float(index),
                    mz=np.asarray([500.0, 600.0]),
                    intensity=np.asarray([1000.0 + index, reference_response]),
                )

    monkeypatch.setattr(
        "direct_infusion_quant.gui.workers.create_mzml_reader",
        lambda _backend: FakeReader(),
    )
    worker = StabilityRecommendationWorker(project)
    with qtbot.waitSignal(worker.succeeded, timeout=1000) as signal:
        worker.run()
    assessment = signal.args[0].assessments[str(sample.id)]
    assert assessment.candidates[0].start_seconds == 20
    assert len(assessment.candidates) == 2
    assert len(assessment.analyte_diagnostics) == 2
    assert assessment.ambiguous is True

    cancelled_worker = StabilityRecommendationWorker(project)
    cancelled_worker.cancel()
    with qtbot.waitSignal(cancelled_worker.cancelled, timeout=1000):
        cancelled_worker.run()


def test_main_window_keeps_thread_alive_until_processing_finishes(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "threaded.mzML"
    path.touch()
    window = MainWindow()
    qtbot.addWidget(window)
    window.add_mzml_files([path])
    window.targets_page.name.setText("thread test")
    window.targets_page.add_window()
    window.targets_page.table.item(0, 1).setCheckState(Qt.CheckState.Checked)
    window.time_page.start.setValue(0)
    window.time_page.end.setValue(2)

    class FakeReader:
        def inspect(self, source_path: Path):
            return MzMLMetadata(source_path, 3, frozenset({1}), True)

        def iter_spectra(self, _path: Path):
            for index in range(3):
                yield SpectrumRecord(
                    native_id=f"scan={index}",
                    index=index,
                    ms_level=1,
                    elapsed_time_seconds=float(index),
                    mz=np.asarray([500.0]),
                    intensity=np.asarray([index + 1.0]),
                )

    monkeypatch.setattr(
        "direct_infusion_quant.gui.workers.create_mzml_reader",
        lambda _backend: FakeReader(),
    )
    window.start_processing()
    qtbot.waitUntil(lambda: window._thread is None, timeout=3000)
    assert len(window.processing_results) == 1
    assert window.navigation.currentRow() == 3


def test_backend_selector_round_trips_through_gui(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.files_page.load_backend(MzMLBackend.PYOPENMS)
    window.time_page.end.setValue(1)

    project = window._sync_project_from_pages()
    assert project.processing.mzml_backend is MzMLBackend.PYOPENMS

    window.project = project
    window.files_page.load_backend(MzMLBackend.PYMZML)
    window._load_project_into_pages()
    assert window.files_page.selected_backend() is MzMLBackend.PYOPENMS


def test_backend_change_clears_stale_results(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.processing_results = {"stale": object()}

    window.files_page.load_backend(MzMLBackend.PYOPENMS)

    assert window.processing_results == {}
    assert "previous processing" in window.log_view.toPlainText()


def test_relevant_stability_edits_clear_interval_confirmation(
    qtbot, tmp_path: Path
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    path = tmp_path / "confirmation.mzML"
    path.touch()
    window.add_mzml_files([path])

    window.time_page.intervals_confirmed.setChecked(True)
    window.time_page.ms_level.setValue(2)
    assert not window.time_page.intervals_confirmed.isChecked()

    window.time_page.intervals_confirmed.setChecked(True)
    window.files_page.load_backend(MzMLBackend.PYOPENMS)
    assert not window.time_page.intervals_confirmed.isChecked()

    window.time_page.intervals_confirmed.setChecked(True)
    window.files_page.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    assert not window.time_page.intervals_confirmed.isChecked()


def test_saved_source_change_is_visible(qtbot, tmp_path: Path) -> None:
    path = tmp_path / "changed.mzML"
    path.write_bytes(b"before")
    stat = path.stat()
    sample = SampleRecord(
        path=path,
        sample_name="changed",
        sample_type=SampleType.UNKNOWN,
        source_provenance=SourceFileProvenance(
            file_size_bytes=stat.st_size,
            modified_time_ns=stat.st_mtime_ns,
            modified_time_utc=datetime.fromtimestamp(stat.st_mtime, UTC),
            sha256="0" * 64,
            spectrum_count=1,
            ms_levels=[1],
            is_centroided=True,
            captured_at_utc=datetime.now(UTC),
        ),
    )
    path.write_bytes(b"after-content-is-different")
    window = MainWindow()
    qtbot.addWidget(window)
    window.project = AnalysisProject(name="changed", samples=[sample])
    window._load_project_into_pages()
    assert "changed" in window.files_page.table.item(0, 9).text().lower()


def test_saved_provenance_is_labelled_without_fresh_inspection(
    qtbot, tmp_path: Path
) -> None:
    path = tmp_path / "saved.mzML"
    path.write_bytes(b"centroid source")
    stat = path.stat()
    captured = datetime(2026, 7, 18, 12, 30, tzinfo=UTC)
    sample = SampleRecord(
        path=path,
        sample_name="saved",
        sample_type=SampleType.UNKNOWN,
        source_provenance=SourceFileProvenance(
            file_size_bytes=stat.st_size,
            modified_time_ns=stat.st_mtime_ns,
            modified_time_utc=datetime.fromtimestamp(stat.st_mtime, UTC),
            sha256=sha256(path.read_bytes()).hexdigest(),
            spectrum_count=429,
            ms_levels=[1],
            is_centroided=True,
            captured_at_utc=captured,
        ),
    )
    window = MainWindow()
    qtbot.addWidget(window)
    window.project = AnalysisProject(name="saved", samples=[sample])
    window._load_project_into_pages()

    item = window.files_page.table.item(0, 9)
    assert item.text() == "Saved metadata: 429 spectra; MS levels [1]; centroided"
    assert "not freshly inspected" in item.toolTip()
    assert captured.isoformat() in item.toolTip()


def test_explicit_sha256_verification_reports_match_and_mismatch(
    qtbot, tmp_path: Path
) -> None:
    matching_path = tmp_path / "matching.mzML"
    changed_path = tmp_path / "changed.mzML"
    matching_path.write_bytes(b"matching bytes")
    changed_path.write_bytes(b"original bytes")

    def sample_with_hash(path: Path, name: str) -> SampleRecord:
        stat = path.stat()
        return SampleRecord(
            path=path,
            sample_name=name,
            sample_type=SampleType.UNKNOWN,
            source_provenance=SourceFileProvenance(
                file_size_bytes=stat.st_size,
                modified_time_ns=stat.st_mtime_ns,
                modified_time_utc=datetime.fromtimestamp(stat.st_mtime, UTC),
                sha256=sha256(path.read_bytes()).hexdigest(),
                spectrum_count=1,
                ms_levels=[1],
                is_centroided=True,
                captured_at_utc=datetime.now(UTC),
            ),
        )

    matching = sample_with_hash(matching_path, "matching")
    changed = sample_with_hash(changed_path, "changed")
    reference_hash = changed.source_provenance.sha256
    changed_path.write_bytes(b"different bytes")
    window = MainWindow()
    qtbot.addWidget(window)
    window.project = AnalysisProject(name="verify", samples=[matching, changed])
    window._load_project_into_pages()
    assert window.verify_hash_action.isEnabled()

    window.verify_sha256_now()
    qtbot.waitUntil(lambda: window._thread is None, timeout=3000)
    assert "verified byte-for-byte" in window.files_page.table.item(0, 9).text()
    assert "MISMATCH" in window.files_page.table.item(1, 9).text()
    assert window.project.samples[1].source_provenance.sha256 == reference_hash


def test_sha256_verification_can_cancel_before_hashing(qtbot, tmp_path: Path) -> None:
    path = tmp_path / "cancel.mzML"
    path.write_bytes(b"bytes")
    stat = path.stat()
    sample = SampleRecord(
        path=path,
        sample_name="cancel",
        sample_type=SampleType.UNKNOWN,
        source_provenance=SourceFileProvenance(
            file_size_bytes=stat.st_size,
            modified_time_ns=stat.st_mtime_ns,
            modified_time_utc=datetime.fromtimestamp(stat.st_mtime, UTC),
            sha256=sha256(path.read_bytes()).hexdigest(),
            spectrum_count=1,
            ms_levels=[1],
            is_centroided=True,
            captured_at_utc=datetime.now(UTC),
        ),
    )
    worker = HashVerificationWorker(AnalysisProject(name="cancel", samples=[sample]))
    worker.cancel()
    with qtbot.waitSignal(worker.cancelled, timeout=1000):
        worker.run()
