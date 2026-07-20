"""Tests for versioned and traceable project persistence."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

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
    SummaryMethod,
    ToleranceUnit,
    WeightingMode,
)
from direct_infusion_quant.persistence import (
    CURRENT_SCHEMA_VERSION,
    InvalidProjectFileError,
    ProjectIntegrityError,
    UnsupportedProjectVersionError,
    load_project,
    save_project,
)


def example_project() -> AnalysisProject:
    light = ExtractionWindow(
        name="light z2",
        target_mz=500.25,
        tolerance=10,
        tolerance_unit=ToleranceUnit.PPM,
    )
    heavy = ExtractionWindow(
        name="heavy z2",
        target_mz=504.25,
        tolerance=0.05,
        tolerance_unit=ToleranceUnit.DA,
    )
    analyte = AnalyteTarget(
        name="peptide A",
        windows=[light, heavy],
        quantifier_mode=QuantifierMode.SUM,
        quantifier_window_ids=[light.id, heavy.id],
    )
    return AnalysisProject(
        name="traceable calibration",
        samples=[
            SampleRecord(
                path=Path("data/standard-1.mzML"),
                sample_name="standard 1",
                sample_type=SampleType.STANDARD,
                concentration=2.5,
                concentration_unit="mg/L",
                dilution_factor=4.0,
                replicate_group="level-1",
                time_start_seconds=20.0,
                source_provenance=SourceFileProvenance(
                    file_size_bytes=123456,
                    modified_time_ns=1_700_000_000_000_000_000,
                    modified_time_utc=datetime(2023, 11, 14, tzinfo=UTC),
                    sha256="a" * 64,
                    spectrum_count=321,
                    ms_levels=[1, 2],
                    is_centroided=True,
                    captured_at_utc=datetime(2026, 7, 18, tzinfo=UTC),
                ),
            )
        ],
        analytes=[analyte],
        active_analyte_id=analyte.id,
        processing=ProcessingSettings(
            ms_level=2,
            mzml_backend=MzMLBackend.PYOPENMS,
            time_start_seconds=12.5,
            time_end_seconds=45.0,
            summary_method=SummaryMethod.TRIMMED_MEAN,
            trim_fraction=0.2,
            stability_trace_mode="tic",
            stability_minimum_scans=15,
            stability_max_robust_cv_percent=12.0,
            stability_max_relative_drift_percent=8.0,
            stability_max_zero_fraction=0.05,
            stability_exclude_before_seconds=5.0,
            stability_exclude_after_seconds=240.0,
            stability_candidate_count=3,
            stability_ambiguity_score_delta_percent=5.0,
            stability_intervals_confirmed=True,
        ),
        calibration=CalibrationSettings(
            blank_correction=BlankCorrectionMethod.NONE,
            regression_model=RegressionModel.LINEAR,
            weighting=WeightingMode.INVERSE_X,
            force_through_zero=True,
            large_residual_absolute=100.0,
            large_residual_percent=15.0,
            upper_flattening_slope_ratio=0.4,
        ),
        last_processing_timestamp_utc=datetime(2026, 7, 18, 12, 30, tzinfo=UTC),
    )


def test_project_round_trip_preserves_all_settings(tmp_path: Path) -> None:
    project = example_project()
    path = tmp_path / "nested" / "analysis.diq.json"
    save_project(project, path)
    reopened = load_project(path)
    assert reopened == project
    assert reopened.application_version == project.application_version
    assert reopened.processing == project.processing
    assert reopened.processing.mzml_backend is MzMLBackend.PYOPENMS
    assert reopened.processing.stability_trace_mode.value == "tic"
    assert reopened.processing.stability_intervals_confirmed is True
    assert reopened.calibration == project.calibration
    assert reopened.samples[0].dilution_factor == 4.0
    assert reopened.samples[0].concentration_unit == "mg/L"
    assert reopened.samples[0].time_start_seconds == 20.0
    assert reopened.samples[0].source_provenance == (
        project.samples[0].source_provenance
    )
    assert reopened.last_processing_timestamp_utc == (
        project.last_processing_timestamp_utc
    )
    assert reopened.analytes[0].quantifier_window_ids == (
        project.analytes[0].quantifier_window_ids
    )

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schema_version"] == CURRENT_SCHEMA_VERSION
    assert document["saved_at_utc"].endswith("+00:00")
    assert len(document["project_sha256"]) == 64


def test_project_round_trip_preserves_explicit_polynomial_model(
    tmp_path: Path,
) -> None:
    project = example_project()
    project.calibration = CalibrationSettings(
        blank_correction=BlankCorrectionMethod.NONE,
        regression_model=RegressionModel.CUBIC,
    )
    path = tmp_path / "cubic.diq.json"
    save_project(project, path)
    assert load_project(path).calibration.regression_model is RegressionModel.CUBIC


def test_modified_settings_fail_integrity_check(tmp_path: Path) -> None:
    path = tmp_path / "analysis.diq.json"
    save_project(example_project(), path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["project"]["processing"]["time_end_seconds"] = 99.0
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ProjectIntegrityError):
        load_project(path)


def test_future_schema_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "analysis.diq.json"
    save_project(example_project(), path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["schema_version"] = CURRENT_SCHEMA_VERSION + 1
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(UnsupportedProjectVersionError):
        load_project(path)


def test_malformed_json_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "broken.diq.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(InvalidProjectFileError):
        load_project(path)
