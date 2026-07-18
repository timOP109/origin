"""Dialog for explicitly resolving missing mzML source paths."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from direct_infusion_quant.models import SampleRecord


class RelinkFilesDialog(QDialog):
    """Collect validated replacement paths without changing the project directly."""

    relink_requested = Signal(object)

    def __init__(self, missing_samples: list[SampleRecord], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Relink Missing mzML Files")
        self.setModal(False)
        self.resize(1000, 420)
        self.table = QTableWidget(len(missing_samples), 4)
        self.table.setHorizontalHeaderLabels(
            ["Sample", "Missing path", "Replacement path", "Status"]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        for row, sample in enumerate(missing_samples):
            name = QTableWidgetItem(sample.sample_name)
            name.setData(Qt.ItemDataRole.UserRole, str(sample.id))
            name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
            missing = QTableWidgetItem(str(sample.path))
            missing.setFlags(missing.flags() & ~Qt.ItemFlag.ItemIsEditable)
            replacement = QLineEdit()
            browse = QPushButton("Browse…")
            browse.clicked.connect(
                lambda _checked=False, current_row=row: self._browse(current_row)
            )
            replacement_widget = QWidget()
            replacement_layout = QHBoxLayout(replacement_widget)
            replacement_layout.setContentsMargins(0, 0, 0, 0)
            replacement_layout.addWidget(replacement)
            replacement_layout.addWidget(browse)
            status = QTableWidgetItem("Unresolved")
            status.setFlags(status.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, missing)
            self.table.setCellWidget(row, 2, replacement_widget)
            self.table.setItem(row, 3, status)

        choose_folder = QPushButton("Match filenames in folder…")
        choose_folder.setToolTip(
            "Propose files with exactly matching filenames from one selected folder."
        )
        choose_folder.clicked.connect(self._choose_folder)
        self.apply_button = QPushButton("Apply valid relinks")
        self.apply_button.clicked.connect(self._apply)
        cancel = QPushButton("Close without changes")
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(choose_folder)
        buttons.addStretch()
        buttons.addWidget(self.apply_button)
        buttons.addWidget(cancel)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Select replacement mzML files. Only source paths change; analysis "
                "settings are preserved and the project is not saved automatically."
            )
        )
        layout.addWidget(self.table)
        layout.addLayout(buttons)

    def set_replacement(self, sample_id: UUID, path: Path) -> None:
        """Set a proposed replacement, used by browsing and workflow tests."""

        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == str(sample_id):
                self._replacement_edit(row).setText(str(path.resolve()))
                self._validate_row(row)
                return
        raise KeyError(f"Unknown missing sample ID: {sample_id}")

    def match_directory(self, directory: Path) -> int:
        """Propose exact filename matches from a directory, without applying them."""

        if not directory.is_dir():
            return 0
        candidates = {
            path.name.casefold(): path
            for path in directory.iterdir()
            if path.is_file() and _is_mzml_path(path)
        }
        matched = 0
        for row in range(self.table.rowCount()):
            missing_name = Path(self.table.item(row, 1).text()).name.casefold()
            candidate = candidates.get(missing_name)
            if candidate is not None:
                self._replacement_edit(row).setText(str(candidate.resolve()))
                self._validate_row(row)
                matched += 1
        return matched

    def valid_relinks(self) -> dict[UUID, Path]:
        """Return only explicitly supplied paths that currently validate."""

        replacements: dict[UUID, Path] = {}
        for row in range(self.table.rowCount()):
            text = self._replacement_edit(row).text().strip()
            if not text:
                continue
            path = Path(text).expanduser().resolve()
            if path.is_file() and _is_mzml_path(path):
                sample_id = UUID(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole))
                replacements[sample_id] = path
        return replacements

    def _browse(self, row: int) -> None:
        original = Path(self.table.item(row, 1).text())
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Relink {original.name}",
            str(original.parent),
            "mzML files (*.mzML *.mzML.gz)",
        )
        if path:
            self._replacement_edit(row).setText(path)
            self._validate_row(row)

    def _choose_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "Choose folder containing replacement mzML files"
        )
        if directory:
            matched = self.match_directory(Path(directory))
            if matched == 0:
                QMessageBox.information(
                    self,
                    "No filename matches",
                    "No missing mzML filenames were found directly in that folder.",
                )

    def _validate_row(self, row: int) -> bool:
        text = self._replacement_edit(row).text().strip()
        path = Path(text).expanduser().resolve() if text else None
        valid = path is not None and path.is_file() and _is_mzml_path(path)
        status = self.table.item(row, 3)
        status.setText("Ready" if valid else "Not a readable mzML file")
        status.setForeground(
            Qt.GlobalColor.darkGreen if valid else Qt.GlobalColor.darkRed
        )
        return valid

    def _apply(self) -> None:
        for row in range(self.table.rowCount()):
            self._validate_row(row)
        replacements = self.valid_relinks()
        if not replacements:
            QMessageBox.warning(
                self,
                "No valid replacements",
                "Select at least one existing .mzML or .mzML.gz file.",
            )
            return
        self.relink_requested.emit(replacements)
        self.accept()

    def _replacement_edit(self, row: int) -> QLineEdit:
        widget = self.table.cellWidget(row, 2)
        edit = widget.findChild(QLineEdit)
        assert edit is not None
        return edit


def _is_mzml_path(path: Path) -> bool:
    name = path.name.casefold()
    return name.endswith(".mzml") or name.endswith(".mzml.gz")
