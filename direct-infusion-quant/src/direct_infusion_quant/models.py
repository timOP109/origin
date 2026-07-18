"""Validated project and scientific settings models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from direct_infusion_quant import __version__


class SampleType(StrEnum):
    """Role of an imported sample in an analysis."""

    BLANK = "blank"
    STANDARD = "standard"
    QC = "qc"
    UNKNOWN = "unknown"


class ToleranceUnit(StrEnum):
    """Unit used to define an extraction window."""

    DA = "Da"
    PPM = "ppm"


class SummaryMethod(StrEnum):
    """File-level aggregation of scan responses."""

    MEDIAN = "median"
    MEAN = "mean"
    TRIMMED_MEAN = "trimmed_mean"


class MzMLBackend(StrEnum):
    """Explicit mzML reader backend recorded with processing settings."""

    PYMZML = "pymzml"
    PYOPENMS = "pyopenms"


class QuantifierMode(StrEnum):
    """How candidate extraction windows form the quantifier response."""

    SINGLE = "single"
    SUM = "sum"


class WeightingMode(StrEnum):
    """Supported calibration weighting modes."""

    NONE = "none"
    INVERSE_X = "1/x"
    INVERSE_X_SQUARED = "1/x^2"


class BlankCorrectionMethod(StrEnum):
    """Supported file-response blank correction methods."""

    POOLED_MEDIAN = "pooled_median"
    NONE = "none"


class ProjectModel(BaseModel):
    """Base configuration shared by persisted project models."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class SourceFileProvenance(ProjectModel):
    """Reproducibility snapshot of an mzML source at processing time."""

    file_size_bytes: int = Field(ge=0)
    modified_time_ns: int = Field(ge=0)
    modified_time_utc: datetime
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    spectrum_count: int = Field(ge=0)
    ms_levels: list[int]
    is_centroided: bool | None
    captured_at_utc: datetime


class ExtractionWindow(ProjectModel):
    """A named centroid extraction window around a target m/z."""

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1)
    target_mz: float = Field(gt=0)
    tolerance: float = Field(gt=0)
    tolerance_unit: ToleranceUnit
    charge: int | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def validate_charge(self) -> ExtractionWindow:
        if self.charge == 0:
            raise ValueError("charge must be non-zero when supplied")
        return self


class AnalyteTarget(ProjectModel):
    """An analyte with candidate windows and an explicit quantifier selection."""

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1)
    molecular_weight: float | None = Field(default=None, gt=0)
    notes: str = ""
    windows: list[ExtractionWindow] = Field(default_factory=list)
    quantifier_mode: QuantifierMode | None = None
    quantifier_window_ids: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_quantifier_selection(self) -> AnalyteTarget:
        window_ids = {window.id for window in self.windows}
        selected = self.quantifier_window_ids
        if len(window_ids) != len(self.windows):
            raise ValueError("extraction window IDs must be unique")
        if len({window.name for window in self.windows}) != len(self.windows):
            raise ValueError("extraction window names must be unique")
        if any(window_id not in window_ids for window_id in selected):
            raise ValueError("quantifier selection references an unknown window")
        if self.quantifier_mode is None and selected:
            raise ValueError("quantifier mode is required when windows are selected")
        if self.quantifier_mode is QuantifierMode.SINGLE and len(selected) != 1:
            raise ValueError("single quantifier mode requires exactly one window")
        if self.quantifier_mode is QuantifierMode.SUM and len(selected) < 2:
            raise ValueError("sum quantifier mode requires at least two windows")
        return self


class SampleRecord(ProjectModel):
    """User classification and metadata for one imported mzML file."""

    id: UUID = Field(default_factory=uuid4)
    path: Path
    sample_name: str = Field(min_length=1)
    sample_type: SampleType
    included: bool = True
    concentration: float | None = Field(default=None, ge=0)
    concentration_unit: str | None = None
    dilution_factor: float = Field(default=1.0, gt=0)
    replicate_group: str | None = None
    source_provenance: SourceFileProvenance | None = None

    @model_validator(mode="after")
    def validate_standard_concentration(self) -> SampleRecord:
        if self.sample_type is SampleType.STANDARD:
            if self.concentration is None:
                raise ValueError("standard samples require a concentration")
            if not self.concentration_unit:
                raise ValueError("standard samples require a concentration unit")
        return self


class ProcessingSettings(ProjectModel):
    """Common extraction settings applied to every file in an analysis."""

    ms_level: int = Field(default=1, ge=1)
    mzml_backend: MzMLBackend = MzMLBackend.PYMZML
    time_start_seconds: float = Field(default=0.0, ge=0)
    time_end_seconds: float | None = Field(default=None, gt=0)
    summary_method: SummaryMethod = SummaryMethod.MEDIAN
    trim_fraction: float = Field(default=0.1, ge=0, lt=0.5)

    @model_validator(mode="after")
    def validate_time_interval(self) -> ProcessingSettings:
        if (
            self.time_end_seconds is not None
            and self.time_end_seconds <= self.time_start_seconds
        ):
            raise ValueError("time interval end must be greater than its start")
        return self


class CalibrationSettings(ProjectModel):
    """Explicit version-one linear calibration choices."""

    blank_correction: BlankCorrectionMethod = BlankCorrectionMethod.POOLED_MEDIAN
    weighting: WeightingMode = WeightingMode.NONE
    force_through_zero: bool = False
    large_residual_absolute: float | None = Field(default=None, gt=0)
    large_residual_percent: float | None = Field(default=None, gt=0)
    upper_flattening_slope_ratio: float | None = Field(default=None, gt=0, lt=1)


class AnalysisProject(ProjectModel):
    """Versioned analysis project supporting one active analyte in version one."""

    schema_version: int = Field(default=1, ge=1)
    application_version: str = __version__
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1)
    samples: list[SampleRecord] = Field(default_factory=list)
    analytes: list[AnalyteTarget] = Field(default_factory=list)
    active_analyte_id: UUID | None = None
    processing: ProcessingSettings = Field(default_factory=ProcessingSettings)
    calibration: CalibrationSettings = Field(default_factory=CalibrationSettings)
    last_processing_timestamp_utc: datetime | None = None

    @model_validator(mode="after")
    def validate_active_analyte(self) -> AnalysisProject:
        analyte_ids = {analyte.id for analyte in self.analytes}
        if len(analyte_ids) != len(self.analytes):
            raise ValueError("analyte IDs must be unique")
        if (
            self.active_analyte_id is not None
            and self.active_analyte_id not in analyte_ids
        ):
            raise ValueError("active analyte ID does not reference a project analyte")
        return self
