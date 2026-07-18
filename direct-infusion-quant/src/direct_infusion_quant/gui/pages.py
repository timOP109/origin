"""Qt Widgets pages for the DirectInfusionQuant workflow."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

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
    SampleRecord,
    SampleType,
    SummaryMethod,
    ToleranceUnit,
    WeightingMode,
)
from direct_infusion_quant.processing import FileProcessingResult


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

    HEADERS = [
        "Included",
        "File",
        "Sample name",
        "Sample type",
        "Concentration",
        "Unit",
        "Dilution factor",
        "Replicate group",
        "Metadata / warnings",
    ]

    def __init__(self) -> None:
        super().__init__()
        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
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
            self.table.setItem(row, 3, _item(SampleType.UNKNOWN.value))
            self.table.setItem(row, 4, _item())
            self.table.setItem(row, 5, _item())
            self.table.setItem(row, 6, _item("1"))
            self.table.setItem(row, 7, _item())
            self.table.setItem(row, 8, _item("Not inspected", editable=False))
            existing.add(resolved)

    def remove_selected(self) -> None:
        rows = sorted(
            {index.row() for index in self.table.selectedIndexes()}, reverse=True
        )
        for row in rows:
            self.table.removeRow(row)

    def set_metadata(self, path: Path, text: str, warning: bool = False) -> None:
        for row in range(self.table.rowCount()):
            if Path(self.table.item(row, 1).data(Qt.ItemDataRole.UserRole)) == path:
                item = _item(text, editable=False)
                if warning:
                    item.setForeground(Qt.GlobalColor.darkYellow)
                self.table.setItem(row, 8, item)
                return

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
                    sample_type=SampleType(self.table.item(row, 3).text().strip()),
                    included=(
                        self.table.item(row, 0).checkState() == Qt.CheckState.Checked
                    ),
                    concentration=float(concentration_text)
                    if concentration_text
                    else None,
                    concentration_unit=self.table.item(row, 5).text().strip() or None,
                    dilution_factor=float(self.table.item(row, 6).text()),
                    replicate_group=self.table.item(row, 7).text().strip() or None,
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
            self.table.item(row, 2).setText(sample.sample_name)
            self.table.item(row, 3).setText(sample.sample_type.value)
            self.table.item(row, 4).setText(
                "" if sample.concentration is None else str(sample.concentration)
            )
            self.table.item(row, 5).setText(sample.concentration_unit or "")
            self.table.item(row, 6).setText(str(sample.dilution_factor))
            self.table.item(row, 7).setText(sample.replicate_group or "")
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
                metadata_item = self.table.item(row, 8)
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
                self.table.item(row, 8).setText(missing_text)
                self.table.item(row, 8).setForeground(Qt.GlobalColor.red)
            elif provenance is not None:
                stat = sample.path.stat()
                if (
                    stat.st_size != provenance.file_size_bytes
                    or stat.st_mtime_ns != provenance.modified_time_ns
                ):
                    self.table.item(row, 8).setText(
                        "Source file size or modification time changed; " + saved_text
                    )
                    self.table.item(row, 8).setForeground(Qt.GlobalColor.darkYellow)

    def selected_backend(self) -> MzMLBackend:
        """Return the backend explicitly displayed in the selector."""

        return MzMLBackend(self.backend.currentData())

    def load_backend(self, backend: MzMLBackend) -> None:
        """Restore a persisted backend choice without automatic substitution."""

        self.backend.setCurrentIndex(self.backend.findData(backend))


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
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.quantifier_mode = QComboBox()
        self.quantifier_mode.addItem(
            "Choose one quantifier window", QuantifierMode.SINGLE
        )
        self.quantifier_mode.addItem("Sum selected windows", QuantifierMode.SUM)
        self.quantifier_mode.setToolTip(
            "Windows are never combined unless 'Sum selected windows' is chosen."
        )
        add_button = QPushButton("Add window")
        add_button.clicked.connect(self.add_window)
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

    def add_window(self, window: ExtractionWindow | None = None) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        enabled = _item()
        enabled.setFlags(enabled.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        enabled.setCheckState(
            Qt.CheckState.Checked
            if window is None or window.enabled
            else Qt.CheckState.Unchecked
        )
        selected = _item()
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
            _item(window.tolerance_unit.value if window else ToleranceUnit.DA.value),
        )
        self.table.setItem(row, 6, _item(window.charge if window else ""))

    def remove_selected(self) -> None:
        rows = sorted(
            {index.row() for index in self.table.selectedIndexes()}, reverse=True
        )
        for row in rows:
            self.table.removeRow(row)

    def analyte(self, existing_id: UUID | None = None) -> AnalyteTarget:
        windows: list[ExtractionWindow] = []
        selected: list[UUID] = []
        for row in range(self.table.rowCount()):
            label_item = self.table.item(row, 2)
            window_id = label_item.data(Qt.ItemDataRole.UserRole)
            charge = self.table.item(row, 6).text().strip()
            identity = {"id": UUID(window_id)} if window_id else {}
            window = ExtractionWindow(
                **identity,
                name=label_item.text().strip(),
                target_mz=float(self.table.item(row, 3).text()),
                tolerance=float(self.table.item(row, 4).text()),
                tolerance_unit=ToleranceUnit(self.table.item(row, 5).text().strip()),
                charge=int(charge) if charge else None,
                enabled=self.table.item(row, 0).checkState() == Qt.CheckState.Checked,
            )
            windows.append(window)
            if self.table.item(row, 1).checkState() == Qt.CheckState.Checked:
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


class IntervalCanvas(PlotCanvas):
    """Spray-response plot with draggable global interval lines."""

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
    """Global acquisition-time interval and spray-response preview."""

    process_requested = Signal()
    cancel_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.ms_level = QSpinBox()
        self.ms_level.setRange(1, 20)
        self.start = QDoubleSpinBox()
        self.end = QDoubleSpinBox()
        for control in (self.start, self.end):
            control.setRange(0, 1_000_000)
            control.setDecimals(4)
            control.setSuffix(" s")
        self.end.setValue(60)
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
        form = QFormLayout()
        form.addRow("MS level", self.ms_level)
        form.addRow("Global start", self.start)
        form.addRow("Global end", self.end)
        form.addRow("Trim fraction per tail", self.trim_fraction)
        form.addRow(self.rsd_enabled, self.rsd_limit)
        form.addRow(self.zero_enabled, self.zero_limit)
        process = QPushButton("Process files")
        process.clicked.connect(self.process_requested)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.cancel_requested)
        cancel.setToolTip(
            "Enabled while processing; cancellation is checked between scans."
        )
        buttons = QHBoxLayout()
        buttons.addWidget(process)
        buttons.addWidget(cancel)
        buttons.addStretch()
        self.canvas = IntervalCanvas()
        self.canvas.axes.set_xlabel("Elapsed acquisition time (s)")
        self.canvas.axes.set_ylabel("Summed intensity")
        self.canvas.set_interval(self.start.value(), self.end.value())
        self.canvas.interval_changed.connect(self._from_plot)
        self.start.valueChanged.connect(self._to_plot)
        self.end.valueChanged.connect(self._to_plot)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Time Window and Stability"))
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.canvas)

    def settings(
        self, method: SummaryMethod, backend: MzMLBackend
    ) -> ProcessingSettings:
        return ProcessingSettings(
            ms_level=self.ms_level.value(),
            mzml_backend=backend,
            time_start_seconds=self.start.value(),
            time_end_seconds=self.end.value(),
            summary_method=method,
            trim_fraction=self.trim_fraction.value(),
        )

    def load_settings(self, settings: ProcessingSettings) -> None:
        self.ms_level.setValue(settings.ms_level)
        self.start.setValue(settings.time_start_seconds)
        self.end.setValue(settings.time_end_seconds or 0)
        self.trim_fraction.setValue(settings.trim_fraction)

    def plot_results(
        self,
        results: dict[str, FileProcessingResult],
        names: dict[str, str],
        project: AnalysisProject,
    ) -> None:
        self.canvas.axes.clear()
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
        self.response_method = QComboBox()
        for method in SummaryMethod:
            self.response_method.addItem(method.value.replace("_", " ").title(), method)
        self.blank_method = QComboBox()
        for method in BlankCorrectionMethod:
            self.blank_method.addItem(method.value.replace("_", " ").title(), method)
        self.weighting = QComboBox()
        for method in WeightingMode:
            self.weighting.addItem(method.value, method)
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
        form.addRow("Regression", QLabel("Linear"))
        form.addRow("Weighting", self.weighting)
        form.addRow("Intercept", self.force_zero)
        form.addRow(self.residual_limit_enabled, self.residual_limit)
        form.addRow(self.flattening_enabled, self.flattening_limit)
        run = QPushButton("Fit calibration")
        run.clicked.connect(self.run_requested)
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
        layout.addWidget(run)
        layout.addWidget(self.summary)
        layout.addWidget(plots)
        layout.addWidget(tabs)

    def settings(self) -> CalibrationSettings:
        return CalibrationSettings(
            blank_correction=self.blank_method.currentData(),
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

    def load_settings(
        self, calibration: CalibrationSettings, processing: ProcessingSettings
    ) -> None:
        self.response_method.setCurrentIndex(
            self.response_method.findData(processing.summary_method)
        )
        self.blank_method.setCurrentIndex(
            self.blank_method.findData(calibration.blank_correction)
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

    def show_result(self, result: CalibrationResult, project: AnalysisProject) -> None:
        sample_by_id = {str(sample.id): sample for sample in project.samples}
        self.summary.setText(
            f"Slope {result.slope:.6g}; intercept {result.intercept:.6g}; "
            f"R² {_format_optional(result.r_squared)}; RMSE {result.rmse:.6g}; "
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
            limits = [min(x), max(x)]
            axes.plot(
                limits, [result.intercept + result.slope * value for value in limits]
            )
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
