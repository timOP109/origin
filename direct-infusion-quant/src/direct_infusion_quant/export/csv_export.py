"""Traceable CSV table export."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from direct_infusion_quant.calibration import CalibrationResult
from direct_infusion_quant.export.tables import CSV_NAMES, result_tables
from direct_infusion_quant.models import AnalysisProject
from direct_infusion_quant.processing import FileProcessingResult


def export_csv_tables(
    directory: Path,
    project: AnalysisProject,
    results: dict[str, FileProcessingResult],
    calibration: CalibrationResult | None,
) -> list[Path]:
    """Write one UTF-8 CSV per result table and return created paths."""

    destination = directory.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    for name, rows in result_tables(project, results, calibration).items():
        path = destination / CSV_NAMES[name]
        pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8")
        created.append(path)
    return created
