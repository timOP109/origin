"""Validation tests for persisted project models."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from direct_infusion_quant.models import (
    AnalysisProject,
    AnalyteTarget,
    ExtractionWindow,
    ProcessingSettings,
    QuantifierMode,
    SampleRecord,
    SampleType,
    ToleranceUnit,
)


def make_window(name: str = "quantifier") -> ExtractionWindow:
    return ExtractionWindow(
        name=name,
        target_mz=500.0,
        tolerance=0.1,
        tolerance_unit=ToleranceUnit.DA,
    )


def test_standard_requires_concentration_and_unit() -> None:
    with pytest.raises(ValidationError):
        SampleRecord(
            path=Path("standard.mzML"),
            sample_name="standard",
            sample_type=SampleType.STANDARD,
        )


def test_window_requires_positive_mz_and_tolerance() -> None:
    with pytest.raises(ValidationError):
        ExtractionWindow(
            name="invalid",
            target_mz=0,
            tolerance=-1,
            tolerance_unit=ToleranceUnit.PPM,
        )


def test_invalid_time_interval_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ProcessingSettings(time_start_seconds=10, time_end_seconds=10)


def test_project_active_analyte_must_exist() -> None:
    with pytest.raises(ValidationError):
        AnalysisProject(name="project", active_analyte_id=make_window().id)


def test_explicit_single_quantifier_is_valid() -> None:
    window = make_window()
    analyte = AnalyteTarget(
        name="peptide",
        windows=[window],
        quantifier_mode=QuantifierMode.SINGLE,
        quantifier_window_ids=[window.id],
    )
    project = AnalysisProject(
        name="project", analytes=[analyte], active_analyte_id=analyte.id
    )
    assert project.active_analyte_id == analyte.id
