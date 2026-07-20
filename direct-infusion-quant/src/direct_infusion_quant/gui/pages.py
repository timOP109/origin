"""Qt Widgets pages for the DirectInfusionQuant workflow."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from direct_infusion_quant.calibration import CalibrationResult
from direct_infusion_quant.models import (
    AnalysisProject,
    AnalyteTarget,
    BlankCorrectionMethod,
    CalibrationSettings,
    ExtractionWindow,
    MzMLBackend,
    ProcessingSettings,
    QuantifierMode,
    RegressionModel,
    SampleRecord,
    SampleType,
    SourceFileProvenance,
    StabilityTraceMode,
    SummaryMethod,
    ToleranceUnit,
    WeightingMode,
)
from direct_infusion_quant.processing import FileProcessingResult

CONCENTRATION_UNITS = (
    "pg/mL",
    "ng/mL",
    "µg/mL",
    "mg/mL",
    "pM",
    "nM",
    "µM",
    "mM",
    "M",
)

SAMPLE_TYPE_LABELS = {
    SampleType.UNKNOWN: "Unknown (sample)",
    SampleType.STANDARD: "Standard",
    SampleType.BLANK: "Blank",
    SampleType.QC: "QC",
}


def _item(text: object = "", *, editable: bool = True) -> QTableWidgetItem:
    item = QTableWidgetItem("" if text is None else str(text))
    if not editable:
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


class PlotCanvas(FigureCanvasQTAgg):
    """Small reusable Matplotlib canvas."""

    def __init__(self) -> None:
        self.figure = Figure(figsize=(5, 3), tight_layout=True)
        super().__init__(self.figure)


class FilesPage(QWidget):
    """Imported files, sample classifications, and metadata status."""

    add_requested = Signal()
    inspect_requested = Signal()
    backend_changed = Signal(object)
    calibration_inputs_changed = Signal()

    HEADERS = [
        "Included",
        "File",
        "Sample name",
        "Sample type",
        "Concentration",
        "Unit",
        "Dilution factor",
        "Replicate group",
        "Individual start (s)",
        "Metadata / warnings",
    ]
    SOURCE_PROVENANCE_ROLE = Qt.ItemDataRole.UserRole + 2

    def __init__(self) -> None:
        super().__init__()
        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        header = self.table.horizontalHeader()
        header.setMinimumSectionSize(70)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        for column in (1, 2, 9):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        add_button = QPushButton("Add mzML files…")
        add_button.clicked.connect(self.add_requested)
        self.remove_button = QPushButton("Remove selected")
        self.remove_button.clicked.connect(self.remove_selected)
        inspect_button = QPushButton("Inspect metadata")
        inspect_button.clicked.connect(self.inspect_requested)
        inspect_button.setToolTip(
            "Read mzML metadata in a background thread and report profile-mode files."
        )
        self.backend = QComboBox()
        for backend in MzMLBackend:
            self.backend.addItem(backend.value, backend)
        self.backend.currentIndexChanged.connect(
            lambda: self.backend_changed.emit(self.selected_backend())
        )
        self.backend.setToolTip(
            "Explicit mzML reader backend. pymzML is the default; pyOpenMS uses "
            "an isolated worker process and must be installed separately."
        )
        buttons = QHBoxLayout()
        buttons.addWidget(add_button)
        buttons.addWidget(self.remove_button)
        buttons.addWidget(inspect_button)
        buttons.addWidget(QLabel("Reader backend"))
        buttons.addWidget(self.backend)
        buttons.addStretch()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Files and Samples"))
        layout.addLayout(buttons)
        layout.addWidget(self.table)

    def add_paths(self, paths: list[Path]) -> None:
        existing = {
            Path(self.table.item(row, 1).data(Qt.ItemDataRole.UserRole))
            for row in range(self.table.rowCount())
        }
        for path in paths:
            resolved = path.resolve()
            if resolved in existing:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            included = _item("Yes")
            included.setFlags(included.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            included.setCheckState(Qt.CheckState.Checked)
            self.table.setItem(row, 0, included)
            file_item = _item(resolved.name, editable=False)
            file_item.setData(Qt.ItemDataRole.UserRole, str(resolved))
            file_item.setToolTip(str(resolved))
            self.table.setItem(row, 1, file_item)
            self.table.setItem(row, 2, _item(resolved.stem))
            self.table.setItem(row, 3, _item(editable=False))
            self._set_sample_type(row, SampleType.UNKNOWN)
            self.table.setItem(row, 4, _item())
            self.table.setItem(row, 5, _item(editable=False))
            self._set_concentration_unit(row, None)
            self.table.setItem(row, 6, _item("1"))
            self.table.setItem(row, 7, _item())
            self.table.setItem(row, 8, _item())
            self.table.setItem(row, 9, _item("Not inspected", editable=False))
            existing.add(resolved)

    def remove_selected(self) -> None:
        rows = sorted(
            {index.row() for index in self.table.selectedIndexes()}, reverse=True
        )
        for row in rows:
            self.table.removeRow(row)
        if rows:
            self.calibration_inputs_changed.emit()

    def set_metadata(self, path: Path, text: str, warning: bool = False) -> None:
        for row in range(self.table.rowCount()):
            if Path(self.table.item(row, 1).data(Qt.ItemDataRole.UserRole)) == path:
                item = _item(text, editable=False)
                if warning:
                    item.setForeground(Qt.GlobalColor.darkYellow)
                self.table.setItem(row, 9, item)
                return

    def set_source_provenance(
        self, path: Path, provenance: SourceFileProvenance
    ) -> None:
        """Retain inspected provenance on its file row until project synchronization."""

        resolved = path.expanduser().resolve()
        for row in range(self.table.rowCount()):
            file_item = self.table.item(row, 1)
            if file_item is None:
                continue
            row_path = Path(file_item.data(Qt.ItemDataRole.UserRole))
            if row_path.expanduser().resolve() == resolved:
                file_item.setData(self.SOURCE_PROVENANCE_ROLE, provenance)
                return

    def included_paths(self) -> list[Path]:
        """Return paths needed for metadata inspection without parsing other fields."""

        paths: list[Path] = []
        for row in range(self.table.rowCount()):
            included_item = self.table.item(row, 0)
            file_item = self.table.item(row, 1)
            if included_item is None or file_item is None:
                raise ValueError(f"File row {row + 1} is incomplete")
            if included_item.checkState() != Qt.CheckState.Checked:
                continue
            path_data = file_item.data(Qt.ItemDataRole.UserRole)
            if not path_data:
                raise ValueError(f"File row {row + 1} has no source path")
            paths.append(Path(path_data))
        return paths

    def set_status(self, sample_id: UUID, text: str, warning: bool = False) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            if item.data(Qt.ItemDataRole.UserRole + 1) == str(sample_id):
                self.set_metadata(
                    Path(item.data(Qt.ItemDataRole.UserRole)), text, warning=warning
                )

    def samples(self) -> list[SampleRecord]:
        samples: list[SampleRecord] = []
        for row in range(self.table.rowCount()):
            file_item = self.table.item(row, 1)
            concentration_text = self.table.item(row, 4).text().strip()
            sample_id = file_item.data(Qt.ItemDataRole.UserRole + 1)
            identity = {"id": UUID(sample_id)} if sample_id else {}
            samples.append(
                SampleRecord(
                    **identity,
                    path=Path(file_item.data(Qt.ItemDataRole.UserRole)),
                    sample_name=self.table.item(row, 2).text().strip(),
                    sample_type=SampleType(self.table.cellWidget(row, 3).currentData()),
                    included=(
                        self.table.item(row, 0).checkState() == Qt.CheckState.Checked
                    ),
                    concentration=float(concentration_text)
                    if concentration_text
                    else None,
                    concentration_unit=self.table.cellWidget(row, 5).currentData(),
                    dilution_factor=float(self.table.item(row, 6).text()),
                    replicate_group=self.table.item(row, 7).text().strip() or None,
                    time_start_seconds=(
                        float(self.table.item(row, 8).text())
                        if self.table.item(row, 8).text().strip()
                        else None
                    ),
                    source_provenance=file_item.data(self.SOURCE_PROVENANCE_ROLE),
                )
            )
        return samples

    def load_samples(self, samples: list[SampleRecord]) -> None:
        self.table.setRowCount(0)
        self.add_paths([sample.path for sample in samples])
        for row, sample in enumerate(samples):
            self.table.item(row, 0).setCheckState(
                Qt.CheckState.Checked if sample.included else Qt.CheckState.Unchecked
            )
            file_item = self.table.item(row, 1)
            file_item.setData(Qt.ItemDataRole.UserRole + 1, str(sample.id))
            file_item.setData(self.SOURCE_PROVENANCE_ROLE, sample.source_provenance)
            self.table.item(row, 2).setText(sample.sample_name)
            self._set_sample_type(row, sample.sample_type)
            self.table.item(row, 4).setText(
                "" if sample.concentration is None else str(sample.concentration)
            )
            self._set_concentration_unit(row, sample.concentration_unit)
            self.table.item(row, 6).setText(str(sample.dilution_factor))
            self.table.item(row, 7).setText(sample.replicate_group or "")
            self.table.item(row, 8).setText(
                ""
                if sample.time_start_seconds is None
                else str(sample.time_start_seconds)
            )
            provenance = sample.source_provenance
            if provenance is not None:
                mode = (
                    "centroided"
                    if provenance.is_centroided is True
                    else "profile mode"
                    if provenance.is_centroided is False
                    else "centroid/profile mode unknown"
                )
                captured = provenance.captured_at_utc.isoformat()
                saved_text = (
                    f"Saved metadata: {provenance.spectrum_count} spectra; "
                    f"MS levels {provenance.ms_levels}; {mode}"
                )
                metadata_item = self.table.item(row, 9)
                metadata_item.setText(saved_text)
                metadata_item.setToolTip(
                    f"Saved metadata captured {captured}; not freshly inspected "
                    "in this session."
                )
                if provenance.is_centroided is not True:
                    metadata_item.setForeground(Qt.GlobalColor.darkYellow)
            if not sample.path.exists():
                missing_text = "Source file is missing"
                if provenance is not None:
                    missing_text += f"; {saved_text}"
                self.table.item(row, 9).setText(missing_text)
                self.table.item(row, 9).setForeground(Qt.GlobalColor.red)
            elif provenance is not None:
                stat = sample.path.stat()
                if (
                    stat.st_size != provenance.file_size_bytes
                    or stat.st_mtime_ns != provenance.modified_time_ns
                ):
                    self.table.item(row, 9).setText(
                        "Source file size or modification time changed; " + saved_text
                    )
                    self.table.item(row, 9).setForeground(Qt.GlobalColor.darkYellow)

    def selected_backend(self) -> MzMLBackend:
        """Return the backend explicitly displayed in the selector."""

        return MzMLBackend(self.backend.currentData())

    def load_backend(self, backend: MzMLBackend) -> None:
        """Restore a persisted backend choice without automatic substitution."""

        self.backend.setCurrentIndex(self.backend.findData(backend))

    def standard_level_count(self) -> int:
        """Return the number of valid, distinct displayed standard levels."""

        levels: set[float] = set()
        for row in range(self.table.rowCount()):
            included_item = self.table.item(row, 0)
            type_widget = self.table.cellWidget(row, 3)
            concentration_item = self.table.item(row, 4)
            if (
                included_item is None
                or included_item.checkState() != Qt.CheckState.Checked
                or type_widget is None
                or concentration_item is None
            ):
                continue
            sample_type = SampleType(type_widget.currentData())
            text = concentration_item.text().strip()
            if sample_type is SampleType.STANDARD and text:
                try:
                    levels.add(float(text))
                except ValueError:
                    continue
        return len(levels)

    def _set_sample_type(self, row: int, sample_type: SampleType) -> None:
        combo = QComboBox()
        for value, label in SAMPLE_TYPE_LABELS.items():
            combo.addItem(label, value)
        combo.setCurrentIndex(combo.findData(sample_type))
        combo.currentIndexChanged.connect(self.calibration_inputs_changed)
        combo.setToolTip("Select the sample's role in calibration and quantification.")
        self.table.setCellWidget(row, 3, combo)

    def _set_concentration_unit(self, row: int, unit: str | None) -> None:
        combo = QComboBox()
        combo.addItem("Not set", None)
        for value in CONCENTRATION_UNITS:
            combo.addItem(value, value)
        if unit is not None and combo.findData(unit) < 0:
            combo.addItem(f"{unit} (saved project)", unit)
        combo.setCurrentIndex(combo.findData(unit))
        combo.currentIndexChanged.connect(self.calibration_inputs_changed)
        combo.setToolTip(
            "Select one concentration unit and use it unchanged for all standards. "
            "The application does not automatically convert units."
        )
        self.table.setCellWidget(row, 5, combo)


class TargetsPage(QWidget):
    """Analyte definition and explicit quantifier selection."""

    HEADERS = [
        "Enabled",
        "Quantifier",
        "Label",
        "Centre m/z",
        "Tolerance",
        "Unit",
        "Charge",
    ]
    definition_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.name = QLineEdit()
        self.molecular_weight = QDoubleSpinBox()
        self.molecular_weight.setRange(0, 1_000_000)
        self.molecular_weight.setSpecialValueText("Not set")
        self.notes = QPlainTextEdit()
        self.notes.setMaximumHeight(70)
        form = QFormLayout()
        form.addRow("Analyte name", self.name)
        form.addRow("Molecular weight (optional)", self.molecular_weight)
        form.addRow("Notes", self.notes)
        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        header = self.table.horizontalHeader()
        header.setMinimumSectionSize(70)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.quantifier_mode = QComboBox()
        self.quantifier_mode.addItem(
            "Choose one quantifier window", QuantifierMode.SINGLE
        )
        self.quantifier_mode.addItem("Sum selected windows", QuantifierMode.SUM)
        self.quantifier_mode.setToolTip(
            "Windows are never combined unless 'Sum selected windows' is chosen."
        )
        add_button = QPushButton("Add window")
        add_button.clicked.connect(lambda _checked=False: self.add_window())
        remove_button = QPushButton("Remove selected")
        remove_button.clicked.connect(self.remove_selected)
        row = QHBoxLayout()
        row.addWidget(add_button)
        row.addWidget(remove_button)
        row.addWidget(QLabel("Quantifier mode"))
        row.addWidget(self.quantifier_mode)
        row.addStretch()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Targets"))
        layout.addLayout(form)
        layout.addLayout(row)
        layout.addWidget(self.table)
        self.table.itemChanged.connect(self._window_item_changed)
        self.quantifier_mode.currentIndexChanged.connect(self._quantifier_mode_changed)

    def add_window(self, window: ExtractionWindow | None = None) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        enabled = _item(editable=False)
        enabled.setFlags(enabled.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        enabled.setCheckState(
            Qt.CheckState.Checked
            if window is None or window.enabled
            else Qt.CheckState.Unchecked
        )
        selected = _item(editable=False)
        selected.setFlags(selected.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        selected.setCheckState(Qt.CheckState.Unchecked)
        self.table.setItem(row, 0, enabled)
        self.table.setItem(row, 1, selected)
        label = _item(window.name if window else f"window {row + 1}")
        if window:
            label.setData(Qt.ItemDataRole.UserRole, str(window.id))
        self.table.setItem(row, 2, label)
        self.table.setItem(row, 3, _item(window.target_mz if window else "500"))
        self.table.setItem(row, 4, _item(window.tolerance if window else "0.1"))
        self.table.setItem(
            row,
            5,
            _item(editable=False),
        )
        unit = QComboBox()
        for tolerance_unit in ToleranceUnit:
            unit.addItem(tolerance_unit.value, tolerance_unit)
        unit.setCurrentIndex(
            unit.findData(window.tolerance_unit if window else ToleranceUnit.DA)
        )
        unit.currentIndexChanged.connect(self.definition_changed)
        unit.setToolTip("Tolerance half-width expressed in Da or ppm.")
        self.table.setCellWidget(row, 5, unit)
        self.table.setItem(row, 6, _item(editable=False))
        charge = QComboBox()
        charge.addItem("Not specified", None)
        for value in range(1, 16):
            charge.addItem(f"+{value}", value)
        charge.setCurrentIndex(charge.findData(window.charge if window else None))
        charge.currentIndexChanged.connect(self.definition_changed)
        charge.setToolTip(
            "Optional positive-ion charge-state annotation (+1 to +15). It does "
            "not change the numerical m/z extraction window."
        )
        self.table.setCellWidget(row, 6, charge)
        self._set_quantifier_enabled(row, enabled.checkState() == Qt.CheckState.Checked)
        self.definition_changed.emit()

    def remove_selected(self) -> None:
        rows = sorted(
            {index.row() for index in self.table.selectedIndexes()}, reverse=True
        )
        for row in rows:
            self.table.removeRow(row)
        if rows:
            self.definition_changed.emit()

    def analyte(self, existing_id: UUID | None = None) -> AnalyteTarget:
        windows: list[ExtractionWindow] = []
        selected: list[UUID] = []
        for row in range(self.table.rowCount()):
            items = [
                self.table.item(row, column) for column in range(len(self.HEADERS))
            ]
            missing = [
                self.HEADERS[column]
                for column, item in enumerate(items)
                if item is None
            ]
            if missing:
                raise ValueError(
                    f"Extraction-window row {row + 1} is incomplete; missing "
                    + ", ".join(missing)
                )
            (
                enabled_item,
                selected_item,
                label_item,
                mz_item,
                tolerance_item,
                unit_item,
                charge_item,
            ) = items
            try:
                window_id = label_item.data(Qt.ItemDataRole.UserRole)
                identity = {"id": UUID(window_id)} if window_id else {}
                window = ExtractionWindow(
                    **identity,
                    name=label_item.text().strip(),
                    target_mz=float(mz_item.text()),
                    tolerance=float(tolerance_item.text()),
                    tolerance_unit=self.table.cellWidget(row, 5).currentData(),
                    charge=self.table.cellWidget(row, 6).currentData(),
                    enabled=(enabled_item.checkState() == Qt.CheckState.Checked),
                )
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"Extraction-window row {row + 1} is invalid: {error}"
                ) from error
            if not window_id:
                label_item.setData(Qt.ItemDataRole.UserRole, str(window.id))
            windows.append(window)
            if selected_item.checkState() == Qt.CheckState.Checked:
                selected.append(window.id)
        molecular_weight = self.molecular_weight.value()
        identity = {"id": existing_id} if existing_id else {}
        return AnalyteTarget(
            **identity,
            name=self.name.text().strip(),
            molecular_weight=molecular_weight if molecular_weight > 0 else None,
            notes=self.notes.toPlainText(),
            windows=windows,
            quantifier_mode=self.quantifier_mode.currentData(),
            quantifier_window_ids=selected,
        )

    def load_analyte(self, analyte: AnalyteTarget | None) -> None:
        self.table.setRowCount(0)
        if analyte is None:
            self.name.clear()
            self.molecular_weight.setValue(0)
            self.notes.clear()
            return
        self.name.setText(analyte.name)
        self.molecular_weight.setValue(analyte.molecular_weight or 0)
        self.notes.setPlainText(analyte.notes)
        mode_index = self.quantifier_mode.findData(analyte.quantifier_mode)
        self.quantifier_mode.setCurrentIndex(max(mode_index, 0))
        for window in analyte.windows:
            self.add_window(window)
            if window.id in analyte.quantifier_window_ids:
                self.table.item(self.table.rowCount() - 1, 1).setCheckState(
                    Qt.CheckState.Checked
                )

    def _window_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            enabled = item.checkState() == Qt.CheckState.Checked
            self._set_quantifier_enabled(item.row(), enabled)
            if not enabled:
                quantifier = self.table.item(item.row(), 1)
                quantifier.setCheckState(Qt.CheckState.Unchecked)
        elif (
            item.column() == 1
            and item.checkState() == Qt.CheckState.Checked
            and QuantifierMode(self.quantifier_mode.currentData())
            is QuantifierMode.SINGLE
        ):
            self.table.blockSignals(True)
            try:
                for row in range(self.table.rowCount()):
                    if row != item.row():
                        self.table.item(row, 1).setCheckState(Qt.CheckState.Unchecked)
            finally:
                self.table.blockSignals(False)
        self.definition_changed.emit()

    def _quantifier_mode_changed(self) -> None:
        if QuantifierMode(self.quantifier_mode.currentData()) is QuantifierMode.SINGLE:
            checked = [
                row
                for row in range(self.table.rowCount())
                if self.table.item(row, 1).checkState() == Qt.CheckState.Checked
            ]
            for row in checked[1:]:
                self.table.item(row, 1).setCheckState(Qt.CheckState.Unchecked)
        self.definition_changed.emit()

    def _set_quantifier_enabled(self, row: int, enabled: bool) -> None:
        item = self.table.item(row, 1)
        if item is None:
            return
        flags = item.flags()
        item.setFlags(
            flags | Qt.ItemFlag.ItemIsEnabled
            if enabled
            else flags & ~Qt.ItemFlag.ItemIsEnabled
        )


class IntervalCanvas(PlotCanvas):
    """Spray-response plot with draggable default interval lines."""

    interval_changed = Signal(float, float)

    def __init__(self) -> None:
        super().__init__()
        self.axes = self.figure.add_subplot(111)
        self.start_line = None
        self.end_line = None
        self._dragging = None
        self.mpl_connect("button_press_event", self._press)
        self.mpl_connect("motion_notify_event", self._move)
        self.mpl_connect("button_release_event", self._release)

    def set_interval(self, start: float, end: float) -> None:
        for line in (self.start_line, self.end_line):
            if line is not None and line in self.axes.lines:
                line.remove()
        self.start_line = self.axes.axvline(start, color="tab:green", linestyle="--")
        self.end_line = self.axes.axvline(end, color="tab:red", linestyle="--")
        self.draw_idle()

    def _press(self, event) -> None:
        if event.xdata is None:
            return
        start = float(self.start_line.get_xdata()[0]) if self.start_line else 0.0
        end = float(self.end_line.get_xdata()[0]) if self.end_line else 0.0
        self._dragging = (
            "start" if abs(event.xdata - start) <= abs(event.xdata - end) else "end"
        )

    def _move(self, event) -> None:
        if self._dragging is None or event.xdata is None:
            return
        line = self.start_line if self._dragging == "start" else self.end_line
        line.set_xdata([event.xdata, event.xdata])
        self.draw_idle()

    def _release(self, _event) -> None:
        if self._dragging is None:
            return
        self._dragging = None
        start = float(self.start_line.get_xdata()[0])
        end = float(self.end_line.get_xdata()[0])
        if start < end:
            self.interval_changed.emit(start, end)


class TimePage(QWidget):
    """Fixed-duration acquisition interval and spray-response preview."""

    BASE_PLOT_HEIGHT = 360
    STABILITY_PANEL_HEIGHT = 145
    STABILITY_PLOT_PADDING = 80

    process_requested = Signal()
    cancel_requested = Signal()
    recommendation_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._loading = False
        self.ms_level = QSpinBox()
        self.ms_level.setRange(1, 20)
        self.start = QDoubleSpinBox()
        self.end = QDoubleSpinBox()
        for control in (self.start, self.end):
            control.setRange(0, 1_000_000)
            control.setDecimals(4)
            control.setSuffix(" s")
        self.end.setSpecialValueText("Not set")
        self.trim_fraction = QDoubleSpinBox()
        self.trim_fraction.setRange(0, 0.49)
        self.trim_fraction.setSingleStep(0.05)
        self.trim_fraction.setValue(0.1)
        self.rsd_enabled = QCheckBox("Warn above scan-level RSD")
        self.rsd_limit = QDoubleSpinBox()
        self.rsd_limit.setRange(0.01, 1_000_000)
        self.rsd_limit.setSuffix(" %")
        self.rsd_limit.setValue(30)
        self.rsd_limit.setEnabled(False)
        self.rsd_enabled.toggled.connect(self.rsd_limit.setEnabled)
        self.zero_enabled = QCheckBox("Warn above zero-response fraction")
        self.zero_limit = QDoubleSpinBox()
        self.zero_limit.setRange(0, 100)
        self.zero_limit.setSuffix(" %")
        self.zero_limit.setValue(50)
        self.zero_limit.setEnabled(False)
        self.zero_enabled.toggled.connect(self.zero_limit.setEnabled)
        self.trace_mode = QComboBox()
        self.trace_mode.addItem(
            "Reference/internal-standard SIC (preferred)",
            StabilityTraceMode.REFERENCE_SIC,
        )
        self.trace_mode.addItem("Total ion current (TIC)", StabilityTraceMode.TIC)
        self.trace_mode.addItem("Analyte SIC", StabilityTraceMode.ANALYTE_SIC)
        self.trace_mode.setToolTip(
            "Analyte SIC can bias interval placement toward higher analyte response. "
            "Prefer an independent reference SIC or TIC when scientifically suitable."
        )
        self.reference_window = QComboBox()
        self.reference_window.setToolTip(
            "Explicit extraction window used only as the spray-stability reference."
        )
        self.trace_mode.currentIndexChanged.connect(self._update_reference_enabled)
        self.minimum_scans = QSpinBox()
        self.minimum_scans.setRange(3, 1_000_000)
        self.minimum_scans.setValue(10)
        self.candidate_count = QSpinBox()
        self.candidate_count.setRange(1, 10)
        self.candidate_count.setValue(3)
        self.ambiguity_enabled = QCheckBox("Flag close candidate scores")
        self.ambiguity_limit = QDoubleSpinBox()
        self.ambiguity_limit.setRange(0.001, 1_000_000)
        self.ambiguity_limit.setSuffix(" % difference")
        self.ambiguity_limit.setEnabled(False)
        self.ambiguity_enabled.toggled.connect(self.ambiguity_limit.setEnabled)
        self.stability_cv_enabled = QCheckBox("Limit robust CV")
        self.stability_cv_limit = QDoubleSpinBox()
        self.stability_cv_limit.setRange(0.001, 1_000_000)
        self.stability_cv_limit.setSuffix(" %")
        self.stability_cv_limit.setEnabled(False)
        self.stability_cv_enabled.toggled.connect(self.stability_cv_limit.setEnabled)
        self.drift_enabled = QCheckBox("Limit relative drift")
        self.drift_limit = QDoubleSpinBox()
        self.drift_limit.setRange(0.001, 1_000_000)
        self.drift_limit.setSuffix(" %")
        self.drift_limit.setEnabled(False)
        self.drift_enabled.toggled.connect(self.drift_limit.setEnabled)
        self.stability_zero_enabled = QCheckBox("Limit zero-response fraction")
        self.stability_zero_limit = QDoubleSpinBox()
        self.stability_zero_limit.setRange(0, 100)
        self.stability_zero_limit.setSuffix(" %")
        self.stability_zero_limit.setEnabled(False)
        self.stability_zero_enabled.toggled.connect(
            self.stability_zero_limit.setEnabled
        )
        self.minimum_response_enabled = QCheckBox("Require median trace response")
        self.minimum_response = QDoubleSpinBox()
        self.minimum_response.setRange(0.000001, 1e18)
        self.minimum_response.setDecimals(6)
        self.minimum_response.setEnabled(False)
        self.minimum_response_enabled.toggled.connect(self.minimum_response.setEnabled)
        self.exclude_before_enabled = QCheckBox("Exclude startup before")
        self.exclude_before = QDoubleSpinBox()
        self.exclude_before.setRange(0, 1_000_000)
        self.exclude_before.setSuffix(" s")
        self.exclude_before.setEnabled(False)
        self.exclude_before_enabled.toggled.connect(self.exclude_before.setEnabled)
        self.exclude_after_enabled = QCheckBox("Exclude shutdown after")
        self.exclude_after = QDoubleSpinBox()
        self.exclude_after.setRange(0.0001, 1_000_000)
        self.exclude_after.setSuffix(" s")
        self.exclude_after.setEnabled(False)
        self.exclude_after_enabled.toggled.connect(self.exclude_after.setEnabled)
        self.intervals_confirmed = QCheckBox(
            "I reviewed and approved every included sample's fixed-duration interval"
        )
        self.intervals_confirmed.setToolTip(
            "Required before calibration. Assessment recommendations are never "
            "accepted automatically."
        )
        form = QFormLayout()
        form.addRow("MS level", self.ms_level)
        form.addRow("Default start", self.start)
        form.addRow("Default end", self.end)
        form.addRow("Trim fraction per tail", self.trim_fraction)
        form.addRow("Stability trace", self.trace_mode)
        form.addRow("Reference window", self.reference_window)
        form.addRow("Candidate periods", self.candidate_count)
        form.addRow(self.ambiguity_enabled, self.ambiguity_limit)
        form.addRow("Minimum scans per candidate", self.minimum_scans)
        form.addRow(self.stability_cv_enabled, self.stability_cv_limit)
        form.addRow(self.drift_enabled, self.drift_limit)
        form.addRow(self.stability_zero_enabled, self.stability_zero_limit)
        form.addRow(self.minimum_response_enabled, self.minimum_response)
        form.addRow(self.exclude_before_enabled, self.exclude_before)
        form.addRow(self.exclude_after_enabled, self.exclude_after)
        form.addRow(self.rsd_enabled, self.rsd_limit)
        form.addRow(self.zero_enabled, self.zero_limit)
        process = QPushButton("Process files")
        process.clicked.connect(self.process_requested)
        recommend = QPushButton("Assess stable periods")
        recommend.clicked.connect(self.recommendation_requested)
        recommend.setToolTip(
            "Stream complete files and report ranked, non-overlapping fixed-duration "
            "periods. Recommendations are not applied."
        )
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.cancel_requested)
        cancel.setToolTip(
            "Enabled while processing; cancellation is checked between scans."
        )
        buttons = QHBoxLayout()
        buttons.addWidget(process)
        buttons.addWidget(recommend)
        buttons.addWidget(cancel)
        buttons.addStretch()
        self.canvas = IntervalCanvas()
        self.canvas.axes.set_xlabel("Elapsed acquisition time (s)")
        self.canvas.axes.set_ylabel("Summed intensity")
        self.canvas.set_interval(self.start.value(), self.end.value())
        self.canvas.interval_changed.connect(self._from_plot)
        self.start.valueChanged.connect(self._to_plot)
        self.end.valueChanged.connect(self._to_plot)
        self.plot_scroll = QScrollArea()
        self.plot_scroll.setWidgetResizable(True)
        self.plot_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.plot_scroll.setMinimumHeight(300)
        self.plot_scroll.setWidget(self.canvas)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Time Window and Stability"))
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.intervals_confirmed)
        layout.addWidget(self.plot_scroll, 1)
        watched = [
            self.ms_level,
            self.start,
            self.end,
            self.trace_mode,
            self.reference_window,
            self.candidate_count,
            self.minimum_scans,
            self.ambiguity_enabled,
            self.ambiguity_limit,
            self.stability_cv_enabled,
            self.stability_cv_limit,
            self.drift_enabled,
            self.drift_limit,
            self.stability_zero_enabled,
            self.stability_zero_limit,
            self.minimum_response_enabled,
            self.minimum_response,
            self.exclude_before_enabled,
            self.exclude_before,
            self.exclude_after_enabled,
            self.exclude_after,
        ]
        for control in watched:
            if isinstance(control, QComboBox):
                control.currentIndexChanged.connect(self._mark_unconfirmed)
            elif isinstance(control, QCheckBox):
                control.toggled.connect(self._mark_unconfirmed)
            else:
                control.valueChanged.connect(self._mark_unconfirmed)
        self._update_reference_enabled()

    def settings(
        self, method: SummaryMethod, backend: MzMLBackend
    ) -> ProcessingSettings:
        end_seconds = self.end.value()
        return ProcessingSettings(
            ms_level=self.ms_level.value(),
            mzml_backend=backend,
            time_start_seconds=self.start.value(),
            time_end_seconds=end_seconds if end_seconds > 0 else None,
            summary_method=method,
            trim_fraction=self.trim_fraction.value(),
            stability_trace_mode=self.trace_mode.currentData(),
            stability_reference_window_id=self.reference_window.currentData(),
            stability_minimum_scans=self.minimum_scans.value(),
            stability_max_robust_cv_percent=(
                self.stability_cv_limit.value()
                if self.stability_cv_enabled.isChecked()
                else None
            ),
            stability_max_relative_drift_percent=(
                self.drift_limit.value() if self.drift_enabled.isChecked() else None
            ),
            stability_max_zero_fraction=(
                self.stability_zero_limit.value() / 100.0
                if self.stability_zero_enabled.isChecked()
                else None
            ),
            stability_minimum_response=(
                self.minimum_response.value()
                if self.minimum_response_enabled.isChecked()
                else None
            ),
            stability_exclude_before_seconds=(
                self.exclude_before.value()
                if self.exclude_before_enabled.isChecked()
                else None
            ),
            stability_exclude_after_seconds=(
                self.exclude_after.value()
                if self.exclude_after_enabled.isChecked()
                else None
            ),
            stability_candidate_count=self.candidate_count.value(),
            stability_ambiguity_score_delta_percent=(
                self.ambiguity_limit.value()
                if self.ambiguity_enabled.isChecked()
                else None
            ),
            stability_intervals_confirmed=self.intervals_confirmed.isChecked(),
        )

    def load_settings(self, settings: ProcessingSettings) -> None:
        self._loading = True
        self.ms_level.setValue(settings.ms_level)
        self.start.setValue(settings.time_start_seconds)
        self.end.setValue(
            settings.time_end_seconds if settings.time_end_seconds is not None else 0
        )
        self.trim_fraction.setValue(settings.trim_fraction)
        self.trace_mode.setCurrentIndex(
            self.trace_mode.findData(settings.stability_trace_mode)
        )
        self.reference_window.setCurrentIndex(
            self.reference_window.findData(settings.stability_reference_window_id)
        )
        self.minimum_scans.setValue(settings.stability_minimum_scans)
        self.candidate_count.setValue(settings.stability_candidate_count)
        self._load_optional(
            self.ambiguity_enabled,
            self.ambiguity_limit,
            settings.stability_ambiguity_score_delta_percent,
        )
        self._load_optional(
            self.stability_cv_enabled,
            self.stability_cv_limit,
            settings.stability_max_robust_cv_percent,
        )
        self._load_optional(
            self.drift_enabled,
            self.drift_limit,
            settings.stability_max_relative_drift_percent,
        )
        self._load_optional(
            self.stability_zero_enabled,
            self.stability_zero_limit,
            None
            if settings.stability_max_zero_fraction is None
            else settings.stability_max_zero_fraction * 100.0,
        )
        self._load_optional(
            self.minimum_response_enabled,
            self.minimum_response,
            settings.stability_minimum_response,
        )
        self._load_optional(
            self.exclude_before_enabled,
            self.exclude_before,
            settings.stability_exclude_before_seconds,
        )
        self._load_optional(
            self.exclude_after_enabled,
            self.exclude_after,
            settings.stability_exclude_after_seconds,
        )
        self.intervals_confirmed.setChecked(settings.stability_intervals_confirmed)
        self._loading = False
        self._update_reference_enabled()

    def load_reference_windows(self, analyte: AnalyteTarget | None) -> None:
        was_loading = self._loading
        self._loading = True
        selected = self.reference_window.currentData()
        self.reference_window.clear()
        if analyte is not None:
            for window in analyte.windows:
                self.reference_window.addItem(window.name, window.id)
        index = self.reference_window.findData(selected)
        if index >= 0:
            self.reference_window.setCurrentIndex(index)
        self._loading = was_loading

    def _update_reference_enabled(self) -> None:
        self.reference_window.setEnabled(
            self.trace_mode.currentData() is StabilityTraceMode.REFERENCE_SIC
        )

    def _mark_unconfirmed(self, *_args) -> None:
        if not self._loading:
            self.intervals_confirmed.setChecked(False)

    @staticmethod
    def _load_optional(enabled, control, value: float | None) -> None:
        enabled.setChecked(value is not None)
        if value is not None:
            control.setValue(value)

    def plot_results(
        self,
        results: dict[str, FileProcessingResult],
        names: dict[str, str],
        project: AnalysisProject,
    ) -> None:
        self.canvas.setMinimumHeight(self.BASE_PLOT_HEIGHT)
        self.canvas.figure.clear()
        self.canvas.axes = self.canvas.figure.add_subplot(111)
        for sample_id, result in results.items():
            values = [_scan_selected_response(scan, project) for scan in result.scans]
            self.canvas.axes.plot(
                [scan.elapsed_time_seconds for scan in result.scans],
                values,
                label=names.get(sample_id, sample_id),
                linewidth=0.9,
            )
        self.canvas.axes.set_xlabel("Elapsed acquisition time (s)")
        self.canvas.axes.set_ylabel("Summed intensity")
        if results:
            self.canvas.axes.legend(fontsize="small")
        self.canvas.set_interval(self.start.value(), self.end.value())
        self.canvas.figure.tight_layout()

    def plot_stability_assessment(
        self, assessments: dict, names: dict[str, str]
    ) -> None:
        """Plot complete stability traces and every ranked candidate."""

        self.canvas.figure.clear()
        items = list(assessments.items())
        self.canvas.setMinimumHeight(
            max(
                self.BASE_PLOT_HEIGHT,
                len(items) * self.STABILITY_PANEL_HEIGHT + self.STABILITY_PLOT_PADDING,
            )
        )
        axes = []
        for index, (sample_id, assessment) in enumerate(items, start=1):
            axis = self.canvas.figure.add_subplot(
                len(items), 1, index, sharex=axes[0] if axes else None
            )
            axes.append(axis)
            axis.plot(
                assessment.times_seconds,
                assessment.trace_responses,
                linewidth=0.8,
            )
            for rank, candidate in enumerate(assessment.candidates, start=1):
                axis.axvspan(
                    candidate.start_seconds,
                    candidate.end_seconds,
                    alpha=max(0.08, 0.25 - 0.05 * (rank - 1)),
                    label=f"Candidate {rank}",
                )
            axis.set_title(
                names.get(sample_id, sample_id), loc="left", fontsize=9, pad=3
            )
            axis.tick_params(axis="both", labelsize=8)
            axis.ticklabel_format(
                axis="y", style="sci", scilimits=(-3, 4), useMathText=True
            )
            axis.grid(axis="y", alpha=0.2, linewidth=0.5)
            if index < len(items):
                axis.tick_params(axis="x", labelbottom=False)
        if axes:
            axes[-1].set_xlabel("Elapsed acquisition time (s)")
            self.canvas.axes = axes[0]
            handles, labels = axes[0].get_legend_handles_labels()
            if handles:
                self.canvas.figure.legend(
                    handles,
                    labels,
                    loc="upper center",
                    ncol=len(handles),
                    fontsize="x-small",
                    frameon=False,
                )
        self.canvas.figure.supylabel("Stability-trace response", x=0.01, fontsize=9)
        self.canvas.figure.tight_layout(rect=(0.035, 0.02, 1.0, 0.95), h_pad=1.0)
        self.canvas.draw_idle()

    def _from_plot(self, start: float, end: float) -> None:
        self.start.setValue(start)
        self.end.setValue(end)

    def _to_plot(self) -> None:
        if self.start.value() < self.end.value():
            self.canvas.set_interval(self.start.value(), self.end.value())


class ResultsPage(QWidget):
    """Scan-level and file-level responses with visible exclusions."""

    FILE_HEADERS = [
        "Sample",
        "Status",
        "Scans",
        "Median",
        "Mean",
        "Trimmed mean",
        "SD",
        "RSD %",
        "Warnings",
    ]
    SCAN_HEADERS = ["Sample", "Scan ID", "Index", "Elapsed time (s)", "Response"]

    def __init__(self) -> None:
        super().__init__()
        self.file_table = QTableWidget(0, len(self.FILE_HEADERS))
        self.file_table.setHorizontalHeaderLabels(self.FILE_HEADERS)
        self.scan_table = QTableWidget(0, len(self.SCAN_HEADERS))
        self.scan_table.setHorizontalHeaderLabels(self.SCAN_HEADERS)
        for table in (self.file_table, self.scan_table):
            table.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeMode.Stretch
            )
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tabs = QTabWidget()
        tabs.addTab(self.file_table, "File-level")
        tabs.addTab(self.scan_table, "Scan-level (all retained scans)")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Processing Results"))
        layout.addWidget(tabs)

    def show_results(
        self,
        results: dict[str, FileProcessingResult],
        failures: dict[str, str],
        project: AnalysisProject,
    ) -> None:
        self.file_table.setRowCount(0)
        self.scan_table.setRowCount(0)
        samples = {str(sample.id): sample for sample in project.samples}
        for sample_id, sample in samples.items():
            row = self.file_table.rowCount()
            self.file_table.insertRow(row)
            result = results.get(sample_id)
            status = (
                "Included"
                if result
                else "Excluded by user"
                if not sample.included
                else failures.get(sample_id, "Not processed")
            )
            values: list[object] = [sample.sample_name, status]
            if result:
                summary = _selected_summary(result, project)
                values.extend(
                    [
                        summary.scan_count,
                        summary.median,
                        summary.mean,
                        summary.trimmed_mean,
                        summary.sample_standard_deviation,
                        summary.rsd_percent,
                        "; ".join(warning.message for warning in result.warnings),
                    ]
                )
                for scan in result.scans:
                    scan_row = self.scan_table.rowCount()
                    self.scan_table.insertRow(scan_row)
                    response = _scan_selected_response(scan, project)
                    for column, value in enumerate(
                        [
                            sample.sample_name,
                            scan.native_id,
                            scan.scan_index,
                            scan.elapsed_time_seconds,
                            response,
                        ]
                    ):
                        self.scan_table.setItem(
                            scan_row, column, _item(value, editable=False)
                        )
            else:
                values.extend(["", "", "", "", "", "", ""])
            for column, value in enumerate(values):
                self.file_table.setItem(row, column, _item(value, editable=False))


class CalibrationPage(QWidget):
    """Calibration settings, plots, diagnostics, and quantified samples."""

    run_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._standard_level_count = 0
        self.response_method = QComboBox()
        for method in SummaryMethod:
            self.response_method.addItem(method.value.replace("_", " ").title(), method)
        self.blank_method = QComboBox()
        for method in BlankCorrectionMethod:
            self.blank_method.addItem(method.value.replace("_", " ").title(), method)
        self.weighting = QComboBox()
        for method in WeightingMode:
            self.weighting.addItem(method.value, method)
        self.regression = QComboBox()
        self.regression.addItem("Linear", RegressionModel.LINEAR)
        self.regression.addItem("Quadratic", RegressionModel.QUADRATIC)
        self.regression.addItem("Cubic (advanced)", RegressionModel.CUBIC)
        self.regression.addItem("Quartic (advanced)", RegressionModel.QUARTIC)
        self.regression.setToolTip(
            "Linear is the default. Higher orders require explicit method "
            "justification and sufficient distinct standard levels. Models are "
            "never selected automatically from R²."
        )
        self.regression.currentIndexChanged.connect(self._update_model_controls)
        self.force_zero = QCheckBox("Force calibration through zero")
        self.force_zero.setToolTip(
            "Off by default; enable only when explicitly required."
        )
        self.residual_limit_enabled = QCheckBox("Absolute residual warning limit")
        self.residual_limit = QDoubleSpinBox()
        self.residual_limit.setRange(0.000001, 1e15)
        self.residual_limit.setEnabled(False)
        self.residual_limit_enabled.toggled.connect(self.residual_limit.setEnabled)
        self.flattening_enabled = QCheckBox("Upper-range slope-ratio warning limit")
        self.flattening_limit = QDoubleSpinBox()
        self.flattening_limit.setRange(0.001, 0.999)
        self.flattening_limit.setValue(0.5)
        self.flattening_limit.setEnabled(False)
        self.flattening_enabled.toggled.connect(self.flattening_limit.setEnabled)
        form = QFormLayout()
        form.addRow("Response statistic", self.response_method)
        form.addRow("Blank method", self.blank_method)
        form.addRow("Regression", self.regression)
        form.addRow("Weighting", self.weighting)
        form.addRow("Intercept", self.force_zero)
        form.addRow(self.residual_limit_enabled, self.residual_limit)
        form.addRow(self.flattening_enabled, self.flattening_limit)
        self.run_button = QPushButton("Fit calibration")
        self.run_button.clicked.connect(self.run_requested)
        self.summary = QLabel("Calibration not fitted")
        self.calibration_canvas = PlotCanvas()
        self.residual_canvas = PlotCanvas()
        plots = QSplitter()
        plots.addWidget(self.calibration_canvas)
        plots.addWidget(self.residual_canvas)
        self.standards = QTableWidget(0, 5)
        self.standards.setHorizontalHeaderLabels(
            [
                "Sample",
                "Known",
                "Predicted response",
                "Residual",
                "Back-calculated (% error)",
            ]
        )
        self.unknowns = QTableWidget(0, 4)
        self.unknowns.setHorizontalHeaderLabels(
            ["Sample", "Type", "Measured", "After dilution"]
        )
        tabs = QTabWidget()
        tabs.addTab(self.standards, "Back-calculated standards")
        tabs.addTab(self.unknowns, "QC and unknowns")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Calibration"))
        layout.addLayout(form)
        layout.addWidget(self.run_button)
        layout.addWidget(self.summary)
        layout.addWidget(plots)
        layout.addWidget(tabs)
        self._update_model_controls()

    def settings(self) -> CalibrationSettings:
        return CalibrationSettings(
            blank_correction=self.blank_method.currentData(),
            regression_model=RegressionModel(self.regression.currentData()),
            weighting=self.weighting.currentData(),
            force_through_zero=self.force_zero.isChecked(),
            large_residual_absolute=(
                self.residual_limit.value()
                if self.residual_limit_enabled.isChecked()
                else None
            ),
            upper_flattening_slope_ratio=(
                self.flattening_limit.value()
                if self.flattening_enabled.isChecked()
                else None
            ),
        )

    def set_run_availability(self, has_results: bool, confirmed: bool) -> None:
        model = RegressionModel(self.regression.currentData())
        required_levels = model.degree + 3 if model.degree > 1 else 0
        levels_available = self._standard_level_count >= required_levels
        self.run_button.setEnabled(has_results and confirmed and levels_available)
        if not has_results:
            reason = "Process files before fitting calibration."
        elif not confirmed:
            reason = (
                "Review and explicitly confirm every included sample interval first."
            )
        elif not levels_available:
            reason = (
                f"{model.value.title()} calibration requires at least "
                f"{required_levels} distinct standard levels; "
                f"{self._standard_level_count} are currently configured."
            )
        else:
            reason = "Fit calibration using the confirmed fixed-duration intervals."
        self.run_button.setToolTip(reason)

    def set_standard_level_count(self, count: int) -> None:
        """Disable advanced orders that lack enough distinct standard levels."""

        self._standard_level_count = count
        model = self.regression.model()
        for index in range(self.regression.count()):
            regression = RegressionModel(self.regression.itemData(index))
            required = regression.degree + 3 if regression.degree > 1 else 0
            available = count >= required
            item = model.item(index)
            if item is not None:
                item.setEnabled(available)
                item.setToolTip(
                    ""
                    if available
                    else f"Requires at least {required} distinct standard levels."
                )

    def load_settings(
        self, calibration: CalibrationSettings, processing: ProcessingSettings
    ) -> None:
        self.response_method.setCurrentIndex(
            self.response_method.findData(processing.summary_method)
        )
        self.blank_method.setCurrentIndex(
            self.blank_method.findData(calibration.blank_correction)
        )
        self.regression.setCurrentIndex(
            self.regression.findData(calibration.regression_model)
        )
        self.weighting.setCurrentIndex(self.weighting.findData(calibration.weighting))
        self.force_zero.setChecked(calibration.force_through_zero)
        self.residual_limit_enabled.setChecked(
            calibration.large_residual_absolute is not None
        )
        if calibration.large_residual_absolute is not None:
            self.residual_limit.setValue(calibration.large_residual_absolute)
        self.flattening_enabled.setChecked(
            calibration.upper_flattening_slope_ratio is not None
        )
        if calibration.upper_flattening_slope_ratio is not None:
            self.flattening_limit.setValue(calibration.upper_flattening_slope_ratio)
        self._update_model_controls()

    def show_result(self, result: CalibrationResult, project: AnalysisProject) -> None:
        sample_by_id = {str(sample.id): sample for sample in project.samples}
        coefficient_text = ", ".join(
            f"c{power}={value:.6g}"
            for power, value in enumerate(result.polynomial_coefficients)
        )
        self.summary.setText(
            f"{result.regression_model.value.title()}; {coefficient_text}; "
            f"R² {_format_optional(result.r_squared)}; RMSE {result.rmse:.6g}; "
            f"residual df {result.residual_degrees_of_freedom}; "
            f"warnings {len(result.warnings)}"
        )
        standards = [
            sample
            for sample in result.samples
            if sample.sample_type is SampleType.STANDARD
        ]
        self.calibration_canvas.figure.clear()
        axes = self.calibration_canvas.figure.add_subplot(111)
        x = [sample_by_id[sample.sample_id].concentration for sample in standards]
        y = [sample.blank_corrected_response for sample in standards]
        axes.scatter(x, y, label="Standards")
        if x:
            curve_x = np.linspace(min(x), max(x), 300)
            axes.plot(curve_x, result.predict_response(curve_x))
        axes.set_xlabel(f"Concentration ({result.concentration_unit})")
        axes.set_ylabel("Response")
        self.calibration_canvas.draw_idle()
        self.residual_canvas.figure.clear()
        residual_axes = self.residual_canvas.figure.add_subplot(111)
        residual_axes.axhline(0, color="black", linewidth=0.8)
        residual_axes.scatter(x, [sample.residual for sample in standards])
        residual_axes.set_xlabel(f"Concentration ({result.concentration_unit})")
        residual_axes.set_ylabel("Residual")
        self.residual_canvas.draw_idle()
        self.standards.setRowCount(0)
        self.unknowns.setRowCount(0)
        for sample in result.samples:
            source = sample_by_id[sample.sample_id]
            if sample.sample_type is SampleType.STANDARD:
                row = self.standards.rowCount()
                self.standards.insertRow(row)
                values = [
                    source.sample_name,
                    source.concentration,
                    sample.predicted_response,
                    sample.residual,
                    f"{_format_optional(sample.back_calculated_concentration)} "
                    f"({_format_optional(sample.back_calculation_percent_error)}%)",
                ]
                for column, value in enumerate(values):
                    self.standards.setItem(row, column, _item(value, editable=False))
            elif sample.sample_type in {SampleType.QC, SampleType.UNKNOWN}:
                row = self.unknowns.rowCount()
                self.unknowns.insertRow(row)
                values = [
                    source.sample_name,
                    sample.sample_type.value,
                    sample.measured_concentration,
                    sample.dilution_corrected_concentration,
                ]
                for column, value in enumerate(values):
                    self.unknowns.setItem(row, column, _item(value, editable=False))

    def _update_model_controls(self) -> None:
        linear = (
            RegressionModel(self.regression.currentData()) is RegressionModel.LINEAR
        )
        if not linear:
            self.force_zero.setChecked(False)
        self.force_zero.setEnabled(linear)
        self.force_zero.setToolTip(
            "Off by default; enable only when explicitly required."
            if linear
            else "Force through zero is restricted to linear calibration."
        )


class ExportPage(QWidget):
    """Export controls with explanations for unavailable actions."""

    csv_requested = Signal()
    excel_requested = Signal()
    project_requested = Signal()
    plots_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.csv_button = QPushButton("Export CSV tables…")
        self.excel_button = QPushButton("Export Excel workbook…")
        self.project_button = QPushButton("Save project JSON…")
        self.plots_button = QPushButton("Export plots as PNG…")
        self.csv_button.clicked.connect(self.csv_requested)
        self.excel_button.clicked.connect(self.excel_requested)
        self.project_button.clicked.connect(self.project_requested)
        self.plots_button.clicked.connect(self.plots_requested)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Export"))
        layout.addWidget(
            QLabel(
                "Exports retain scan-level responses and include processing and "
                "calibration settings. Source mzML data are never embedded."
            )
        )
        for button in (
            self.csv_button,
            self.excel_button,
            self.project_button,
            self.plots_button,
        ):
            layout.addWidget(button)
        layout.addStretch()

    def set_availability(self, has_processing: bool, has_calibration: bool) -> None:
        for button in (self.csv_button, self.excel_button):
            button.setEnabled(has_processing)
            button.setToolTip(
                ""
                if has_processing
                else "Process files before exporting result tables."
            )
        self.plots_button.setEnabled(has_processing or has_calibration)
        self.plots_button.setToolTip(
            ""
            if self.plots_button.isEnabled()
            else "Process files or fit calibration first."
        )


def _selected_summary(result: FileProcessingResult, project: AnalysisProject):
    analyte = next(
        item for item in project.analytes if item.id == project.active_analyte_id
    )
    if analyte.quantifier_mode is QuantifierMode.SUM:
        assert result.derived_summary is not None
        return result.derived_summary
    return result.window_summaries[analyte.quantifier_window_ids[0]]


def _scan_selected_response(scan, project: AnalysisProject) -> float:
    analyte = next(
        item for item in project.analytes if item.id == project.active_analyte_id
    )
    if analyte.quantifier_mode is QuantifierMode.SUM:
        assert scan.derived_response is not None
        return scan.derived_response
    return scan.window_responses[analyte.quantifier_window_ids[0]]


def _format_optional(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.6g}"
