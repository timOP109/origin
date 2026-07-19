"""Build reproducibility and review tables from settings and results."""

from __future__ import annotations

import platform
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from direct_infusion_quant import __version__
from direct_infusion_quant.calibration import CalibrationResult
from direct_infusion_quant.models import AnalysisProject, SampleType
from direct_infusion_quant.processing import FileProcessingResult

SHEET_NAMES = (
    "Samples",
    "Targets and Windows",
    "Processing Settings",
    "Stability Assessment",
    "Scan Summary",
    "File Responses",
    "Calibration Standards",
    "Calibration Statistics",
    "Unknowns and QC",
    "Warnings",
    "Software Versions",
)

CSV_NAMES = {
    "Samples": "samples.csv",
    "Targets and Windows": "targets_and_extraction_windows.csv",
    "Processing Settings": "processing_settings.csv",
    "Stability Assessment": "stability_assessment.csv",
    "Scan Summary": "scan_summary.csv",
    "File Responses": "file_responses.csv",
    "Calibration Standards": "calibration_standards.csv",
    "Calibration Statistics": "calibration_statistics.csv",
    "Unknowns and QC": "unknowns_and_qc.csv",
    "Warnings": "warnings.csv",
    "Software Versions": "software_versions.csv",
}


def result_tables(
    project: AnalysisProject,
    results: dict[str, FileProcessingResult],
    calibration: CalibrationResult | None,
) -> dict[str, list[dict[str, Any]]]:
    """Return the requested review tables without making compliance claims."""

    samples = {str(sample.id): sample for sample in project.samples}
    tables: dict[str, list[dict[str, Any]]] = {name: [] for name in SHEET_NAMES}
    for sample in project.samples:
        provenance = sample.source_provenance
        tables["Samples"].append(
            {
                "sample_id": str(sample.id),
                "sample_name": sample.sample_name,
                "sample_type": sample.sample_type.value,
                "included": sample.included,
                "source_file_path": str(sample.path),
                "source_file_size_bytes": provenance.file_size_bytes
                if provenance
                else None,
                "source_file_modified_utc": provenance.modified_time_utc.isoformat()
                if provenance
                else None,
                "source_file_sha256": provenance.sha256 if provenance else None,
                "mzml_spectrum_count": provenance.spectrum_count
                if provenance
                else None,
                "mzml_ms_levels": ",".join(map(str, provenance.ms_levels))
                if provenance
                else None,
                "mzml_is_centroided": provenance.is_centroided if provenance else None,
                "metadata_captured_at_utc": provenance.captured_at_utc.isoformat()
                if provenance
                else None,
                "known_concentration": sample.concentration,
                "concentration_unit": sample.concentration_unit,
                "dilution_factor": sample.dilution_factor,
                "replicate_group": sample.replicate_group,
                "individual_time_start_seconds": sample.time_start_seconds,
                "effective_time_start_seconds": (
                    sample.time_start_seconds
                    if sample.time_start_seconds is not None
                    else project.processing.time_start_seconds
                ),
                "effective_time_end_seconds": (
                    (
                        sample.time_start_seconds
                        if sample.time_start_seconds is not None
                        else project.processing.time_start_seconds
                    )
                    + (
                        project.processing.time_end_seconds
                        - project.processing.time_start_seconds
                    )
                    if project.processing.time_end_seconds is not None
                    else None
                ),
            }
        )
        if sample.stability_assessment is not None:
            for candidate in sample.stability_assessment.candidates:
                tables["Stability Assessment"].append(
                    {
                        "sample_id": str(sample.id),
                        "sample_name": sample.sample_name,
                        "assessed_at_utc": (
                            sample.stability_assessment.assessed_at_utc.isoformat()
                        ),
                        "trace_mode": sample.stability_assessment.trace_mode.value,
                        "ambiguous": sample.stability_assessment.ambiguous,
                        **candidate.model_dump(mode="json"),
                    }
                )
        if provenance is not None and provenance.is_centroided is not True:
            tables["Warnings"].append(
                {
                    "scope": "source_metadata",
                    "sample_id": str(sample.id),
                    "sample_name": sample.sample_name,
                    "code": "profile_mode_file"
                    if provenance.is_centroided is False
                    else "spectrum_mode_unknown",
                    "message": "Source declares profile-mode spectra."
                    if provenance.is_centroided is False
                    else "Source centroid/profile declaration is unknown.",
                    "window_id": None,
                }
            )
    for analyte in project.analytes:
        for window in analyte.windows:
            tables["Targets and Windows"].append(
                {
                    "analyte_id": str(analyte.id),
                    "active_analyte": analyte.id == project.active_analyte_id,
                    "analyte_name": analyte.name,
                    "molecular_weight": analyte.molecular_weight,
                    "notes": analyte.notes,
                    "window_id": str(window.id),
                    "window_label": window.name,
                    "centre_mz": window.target_mz,
                    "tolerance": window.tolerance,
                    "tolerance_unit": window.tolerance_unit.value,
                    "charge": window.charge,
                    "enabled": window.enabled,
                    "quantifier_mode": analyte.quantifier_mode.value
                    if analyte.quantifier_mode
                    else None,
                    "selected_for_response": window.id in analyte.quantifier_window_ids,
                }
            )
    tables["Processing Settings"].extend(_processing_settings(project))
    for sample_id, result in results.items():
        sample = samples[sample_id]
        for scan in result.scans:
            row: dict[str, Any] = {
                "sample_id": sample_id,
                "sample_name": sample.sample_name,
                "native_scan_id": scan.native_id,
                "scan_index": scan.scan_index,
                "elapsed_acquisition_time_seconds": scan.elapsed_time_seconds,
                "explicit_sum_response": scan.derived_response,
            }
            row.update(
                {
                    f"window_{window_id}": response
                    for window_id, response in scan.window_responses.items()
                }
            )
            tables["Scan Summary"].append(row)
        for window_id, summary in result.window_summaries.items():
            tables["File Responses"].append(
                {
                    "sample_id": sample_id,
                    "sample_name": sample.sample_name,
                    "response_source": str(window_id),
                    "selected_file_response": result.quantification_response,
                    "time_start_seconds": result.time_start_seconds,
                    "time_end_seconds": result.time_end_seconds,
                    **_summary_dict(summary),
                }
            )
        if result.derived_summary is not None:
            tables["File Responses"].append(
                {
                    "sample_id": sample_id,
                    "sample_name": sample.sample_name,
                    "response_source": "explicit_sum",
                    "selected_file_response": result.quantification_response,
                    "time_start_seconds": result.time_start_seconds,
                    "time_end_seconds": result.time_end_seconds,
                    **_summary_dict(result.derived_summary),
                }
            )
        for warning in result.warnings:
            tables["Warnings"].append(
                {
                    "scope": "processing",
                    "sample_id": sample_id,
                    "sample_name": sample.sample_name,
                    "code": warning.code,
                    "message": warning.message,
                    "window_id": str(warning.window_id or ""),
                }
            )
    _calibration_tables(tables, calibration, samples)
    tables["Software Versions"].extend(_software_versions(project))
    return tables


def _processing_settings(project: AnalysisProject) -> list[dict[str, Any]]:
    processing = project.processing
    calibration = project.calibration
    return [
        {"setting": "mzml_reader_backend", "value": processing.mzml_backend.value},
        {"setting": "selected_ms_level", "value": processing.ms_level},
        {
            "setting": "default_time_start_seconds",
            "value": processing.time_start_seconds,
        },
        {
            "setting": "default_time_end_seconds",
            "value": processing.time_end_seconds,
        },
        {"setting": "response_statistic", "value": processing.summary_method.value},
        {"setting": "trim_fraction_per_tail", "value": processing.trim_fraction},
        {
            "setting": "stability_trace_mode",
            "value": processing.stability_trace_mode.value,
        },
        {
            "setting": "stability_reference_window_id",
            "value": processing.stability_reference_window_id,
        },
        {
            "setting": "stability_minimum_scans",
            "value": processing.stability_minimum_scans,
        },
        {
            "setting": "stability_candidate_count",
            "value": processing.stability_candidate_count,
        },
        {
            "setting": "stability_ambiguity_score_delta_percent",
            "value": processing.stability_ambiguity_score_delta_percent,
        },
        {
            "setting": "stability_max_robust_cv_percent",
            "value": processing.stability_max_robust_cv_percent,
        },
        {
            "setting": "stability_max_relative_drift_percent",
            "value": processing.stability_max_relative_drift_percent,
        },
        {
            "setting": "stability_max_zero_fraction",
            "value": processing.stability_max_zero_fraction,
        },
        {
            "setting": "stability_minimum_response",
            "value": processing.stability_minimum_response,
        },
        {
            "setting": "stability_exclude_before_seconds",
            "value": processing.stability_exclude_before_seconds,
        },
        {
            "setting": "stability_exclude_after_seconds",
            "value": processing.stability_exclude_after_seconds,
        },
        {
            "setting": "stability_intervals_confirmed",
            "value": processing.stability_intervals_confirmed,
        },
        {"setting": "blank_method", "value": calibration.blank_correction.value},
        {"setting": "regression_model", "value": "linear"},
        {"setting": "weighting", "value": calibration.weighting.value},
        {
            "setting": "force_through_zero",
            "value": calibration.force_through_zero,
        },
        {
            "setting": "processing_timestamp_utc",
            "value": project.last_processing_timestamp_utc.isoformat()
            if project.last_processing_timestamp_utc
            else None,
        },
    ]


def _calibration_tables(
    tables: dict[str, list[dict[str, Any]]],
    calibration: CalibrationResult | None,
    samples: dict[str, Any],
) -> None:
    if calibration is None:
        return
    tables["Calibration Statistics"].append(
        {
            "slope": calibration.slope,
            "intercept": calibration.intercept,
            "slope_standard_error": calibration.slope_standard_error,
            "intercept_standard_error": calibration.intercept_standard_error,
            "r_squared": calibration.r_squared,
            "rmse": calibration.rmse,
            "residual_standard_error": calibration.residual_standard_error,
            "blank_response": calibration.blank_response,
            "concentration_unit": calibration.concentration_unit,
        }
    )
    for result in calibration.samples:
        source = samples[result.sample_id]
        base = {
            "sample_id": result.sample_id,
            "sample_name": source.sample_name,
            "sample_type": result.sample_type.value,
            "raw_response": result.raw_response,
            "blank_corrected_response": result.blank_corrected_response,
            "dilution_factor": source.dilution_factor,
            "concentration_unit": result.concentration_unit,
        }
        if result.sample_type is SampleType.STANDARD:
            tables["Calibration Standards"].append(
                {
                    **base,
                    "known_concentration": source.concentration,
                    "predicted_response": result.predicted_response,
                    "residual": result.residual,
                    "back_calculated_concentration": (
                        result.back_calculated_concentration
                    ),
                    "back_calculation_percent_error": (
                        result.back_calculation_percent_error
                    ),
                    "replicate_group": source.replicate_group,
                }
            )
        elif result.sample_type in {SampleType.UNKNOWN, SampleType.QC}:
            tables["Unknowns and QC"].append(
                {
                    **base,
                    "measured_concentration": result.measured_concentration,
                    "dilution_corrected_concentration": (
                        result.dilution_corrected_concentration
                    ),
                    "replicate_group": source.replicate_group,
                }
            )
    for warning in calibration.warnings:
        source = samples.get(warning.sample_id) if warning.sample_id else None
        tables["Warnings"].append(
            {
                "scope": "calibration",
                "sample_id": warning.sample_id,
                "sample_name": source.sample_name if source else None,
                "code": warning.code,
                "message": warning.message,
                "window_id": None,
            }
        )


def _software_versions(project: AnalysisProject) -> list[dict[str, Any]]:
    rows = [
        {"software": "DirectInfusionQuant", "version": __version__},
        {
            "software": "Project-recorded DirectInfusionQuant",
            "version": project.application_version,
        },
        {"software": "Python", "version": sys.version.split()[0]},
        {"software": "Operating system", "version": platform.platform()},
    ]
    for package in (
        "PySide6",
        "pymzml",
        "pyopenms",
        "numpy",
        "pandas",
        "scipy",
        "statsmodels",
        "matplotlib",
        "openpyxl",
        "pydantic",
    ):
        try:
            package_version = version(package)
        except PackageNotFoundError:
            package_version = "not installed"
        rows.append({"software": package, "version": package_version})
    rows.append(
        {
            "software": "Processing timestamp (UTC)",
            "version": project.last_processing_timestamp_utc.isoformat()
            if project.last_processing_timestamp_utc
            else "not processed",
        }
    )
    rows.append(
        {
            "software": "Record purpose",
            "version": "Supports reproducibility and review; no regulatory claim.",
        }
    )
    return rows


def _summary_dict(summary) -> dict[str, Any]:
    return {
        "scan_count": summary.scan_count,
        "median": summary.median,
        "mean": summary.mean,
        "trimmed_mean": summary.trimmed_mean,
        "sample_standard_deviation": summary.sample_standard_deviation,
        "rsd_percent": summary.rsd_percent,
        "zero_response_scan_count": summary.zero_response_scan_count,
    }
