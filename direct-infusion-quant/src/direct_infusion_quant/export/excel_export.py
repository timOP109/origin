"""Traceable Excel workbook export."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from direct_infusion_quant.calibration import CalibrationResult
from direct_infusion_quant.export.tables import result_tables
from direct_infusion_quant.models import AnalysisProject
from direct_infusion_quant.processing import FileProcessingResult


def export_excel_workbook(
    path: Path,
    project: AnalysisProject,
    results: dict[str, FileProcessingResult],
    calibration: CalibrationResult | None,
) -> None:
    """Write results and the complete settings summary using openpyxl."""

    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(destination, engine="openpyxl") as writer:
        for name, rows in result_tables(project, results, calibration).items():
            pd.DataFrame(rows).to_excel(writer, sheet_name=name[:31], index=False)
        for sheet in writer.book.worksheets:
            sheet.freeze_panes = "A2"
            for column in sheet.columns:
                width = min(max(len(str(cell.value or "")) for cell in column) + 2, 60)
                sheet.column_dimensions[column[0].column_letter].width = width
