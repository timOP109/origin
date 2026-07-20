"""Regressions for metadata inspection before processing is configured."""

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from direct_infusion_quant.gui.main_window import MainWindow
from direct_infusion_quant.gui.pages import TargetsPage, TimePage
from direct_infusion_quant.gui.workers import MetadataWorker
from direct_infusion_quant.models import (
    MzMLBackend,
    ProcessingSettings,
    SourceFileProvenance,
    SummaryMethod,
)
from direct_infusion_quant.persistence import load_project


def _provenance(path: Path) -> SourceFileProvenance:
    stat = path.stat()
    return SourceFileProvenance(
        file_size_bytes=stat.st_size,
        modified_time_ns=stat.st_mtime_ns,
        modified_time_utc=datetime.fromtimestamp(stat.st_mtime, UTC),
        sha256=sha256(path.read_bytes()).hexdigest(),
        spectrum_count=12,
        ms_levels=[1],
        is_centroided=True,
        captured_at_utc=datetime.now(UTC),
    )


def test_fresh_import_can_inspect_metadata_before_processing_is_configured(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    """Metadata inspection must not require a target or a finite time interval."""

    mzml = tmp_path / "fresh-import.mzML"
    mzml.touch()
    window = MainWindow()
    qtbot.addWidget(window)
    window.add_mzml_files([mzml])

    assert window.project.processing.time_end_seconds is None
    assert window.targets_page.table.rowCount() == 0
    started: list[object] = []
    validation_errors: list[Exception] = []
    monkeypatch.setattr(window, "_start_worker", started.append)
    monkeypatch.setattr(window, "_show_validation_error", validation_errors.append)

    window.inspect_metadata()

    assert validation_errors == []
    assert len(started) == 1
    assert isinstance(started[0], MetadataWorker)
    assert started[0].paths == [mzml.resolve()]


def test_metadata_inspection_ignores_incomplete_target_and_uses_selected_backend(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    """Only file paths and the explicit backend are inputs to metadata inspection."""

    mzml = tmp_path / "incomplete-target.mzML"
    mzml.touch()
    window = MainWindow()
    qtbot.addWidget(window)
    window.add_mzml_files([mzml])
    window.targets_page.name.setText("unfinished peptide")
    window.targets_page.table.insertRow(0)
    window.files_page.load_backend(MzMLBackend.PYOPENMS)
    started: list[object] = []
    monkeypatch.setattr(window, "_start_worker", started.append)

    window.inspect_metadata()

    assert len(started) == 1
    worker = started[0]
    assert isinstance(worker, MetadataWorker)
    assert worker.paths == [mzml.resolve()]
    assert worker.backend is MzMLBackend.PYOPENMS


def test_metadata_provenance_survives_later_valid_sync_and_save(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    """Early metadata is retained when processing settings become valid later."""

    mzml = tmp_path / "early-metadata.mzML"
    mzml.write_bytes(b"centroided fixture")
    project_path = tmp_path / "metadata-round-trip.diq.json"
    window = MainWindow()
    qtbot.addWidget(window)
    window.add_mzml_files([mzml])
    started: list[object] = []
    monkeypatch.setattr(window, "_start_worker", started.append)
    monkeypatch.setattr(
        window,
        "_show_validation_error",
        lambda error: pytest.fail(f"metadata inspection was blocked: {error}"),
    )

    window.inspect_metadata()
    assert len(started) == 1
    provenance = _provenance(mzml)
    window._metadata_received(mzml.resolve(), provenance)

    window.time_page.end.setValue(30)
    assert window.save_project_path(project_path)
    reopened = load_project(project_path)
    assert reopened.samples[0].source_provenance == provenance


@pytest.mark.parametrize("malformation", ["missing-cell", "invalid-number"])
def test_malformed_target_row_reports_row_specific_value_error(
    qtbot, malformation: str
) -> None:
    page = TargetsPage()
    qtbot.addWidget(page)
    page.name.setText("peptide")
    page.add_window()
    if malformation == "missing-cell":
        page.table.takeItem(0, 3)
    else:
        page.table.item(0, 3).setText("not-a-number")

    with pytest.raises(ValueError, match=r"(?i)row 1"):
        page.analyte()


def test_unset_processing_end_survives_time_page_round_trip(qtbot) -> None:
    page = TimePage()
    qtbot.addWidget(page)
    page.load_settings(ProcessingSettings(time_end_seconds=None))

    round_tripped = page.settings(SummaryMethod.MEDIAN, MzMLBackend.PYMZML)

    assert round_tripped.time_end_seconds is None


def test_positive_processing_end_survives_time_page_round_trip(qtbot) -> None:
    page = TimePage()
    qtbot.addWidget(page)
    page.load_settings(ProcessingSettings(time_start_seconds=5, time_end_seconds=75))

    round_tripped = page.settings(SummaryMethod.MEDIAN, MzMLBackend.PYMZML)

    assert round_tripped.time_start_seconds == 5
    assert round_tripped.time_end_seconds == 75
