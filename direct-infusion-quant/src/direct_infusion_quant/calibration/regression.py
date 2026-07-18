"""GUI-independent calibration fitting and inverse quantification."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import statsmodels.api as sm

from direct_infusion_quant.models import (
    BlankCorrectionMethod,
    CalibrationSettings,
    SampleType,
    WeightingMode,
)


@dataclass(frozen=True, slots=True)
class CalibrationInput:
    """Response and user-entered metadata for one calibration-project file."""

    sample_id: str
    sample_type: SampleType
    file_response: float
    concentration: float | None = None
    concentration_unit: str | None = None
    dilution_factor: float = 1.0
    replicate_group: str | None = None

    def __post_init__(self) -> None:
        if not np.isfinite(self.file_response):
            raise ValueError("file response must be finite")
        if self.dilution_factor <= 0 or not np.isfinite(self.dilution_factor):
            raise ValueError("dilution factor must be finite and greater than zero")
        if self.sample_type is SampleType.STANDARD:
            if self.concentration is None or self.concentration < 0:
                raise ValueError("standards require a non-negative concentration")
            if not self.concentration_unit:
                raise ValueError("standards require a concentration unit")


@dataclass(frozen=True, slots=True)
class CalibrationWarning:
    """Structured diagnostic that does not alter calibration membership."""

    code: str
    message: str
    sample_id: str | None = None


class CalibrationError(ValueError):
    """Raised when inputs cannot produce the requested calibration."""

    def __init__(
        self, message: str, warnings: tuple[CalibrationWarning, ...] = ()
    ) -> None:
        super().__init__(message)
        self.warnings = warnings


@dataclass(frozen=True, slots=True)
class QuantifiedSample:
    """Predictions and inverse-prediction results for one file response."""

    sample_id: str
    sample_type: SampleType
    raw_response: float
    blank_corrected_response: float
    predicted_response: float | None
    residual: float | None
    back_calculated_concentration: float | None
    back_calculation_percent_error: float | None
    measured_concentration: float | None
    dilution_corrected_concentration: float | None
    concentration_unit: str


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    """Regression coefficients, diagnostics, and per-file quantification."""

    slope: float
    intercept: float
    slope_standard_error: float | None
    intercept_standard_error: float | None
    r_squared: float | None
    rmse: float
    residual_standard_error: float | None
    blank_response: float
    concentration_unit: str
    samples: tuple[QuantifiedSample, ...]
    warnings: tuple[CalibrationWarning, ...]


def calibrate_and_quantify(
    samples: list[CalibrationInput], settings: CalibrationSettings
) -> CalibrationResult:
    """Fit a version-one calibration and inverse-predict QC/unknown samples."""

    standards = [
        sample for sample in samples if sample.sample_type is SampleType.STANDARD
    ]
    if not standards:
        raise CalibrationError("at least one standard is required")

    units = {sample.concentration_unit for sample in standards}
    if len(units) != 1:
        warning = CalibrationWarning(
            code="inconsistent_concentration_units",
            message="All standards must use one unchanged concentration unit.",
        )
        raise CalibrationError(warning.message, (warning,))
    concentration_unit = next(iter(units))
    assert concentration_unit is not None

    warnings: list[CalibrationWarning] = []
    blanks = [
        sample.file_response
        for sample in samples
        if sample.sample_type is SampleType.BLANK
    ]
    if settings.blank_correction is BlankCorrectionMethod.POOLED_MEDIAN:
        if blanks:
            blank_response = float(np.median(np.asarray(blanks, dtype=np.float64)))
        else:
            blank_response = 0.0
            warnings.append(
                CalibrationWarning(
                    code="absent_blanks",
                    message=(
                        "Pooled-median blank correction was selected, but no blanks "
                        "were supplied; responses were left uncorrected."
                    ),
                )
            )
    else:
        blank_response = 0.0

    x = np.asarray([sample.concentration for sample in standards], dtype=np.float64)
    y = np.asarray(
        [sample.file_response - blank_response for sample in standards],
        dtype=np.float64,
    )
    if settings.weighting is not WeightingMode.NONE and np.any(x == 0):
        raise CalibrationError(
            f"Weighting {settings.weighting.value} cannot be used with zero "
            "concentration."
        )

    unique_nonzero_levels = np.unique(x[x > 0])
    if unique_nonzero_levels.size < 3:
        warnings.append(
            CalibrationWarning(
                code="fewer_than_three_nonzero_levels",
                message=(
                    "Calibration contains fewer than three non-zero standard levels."
                ),
            )
        )

    parameter_count = 1 if settings.force_through_zero else 2
    if x.size < parameter_count:
        raise CalibrationError("insufficient standards for the selected regression")
    design = x[:, None] if settings.force_through_zero else sm.add_constant(x)
    weights = _weights(x, settings.weighting)
    fitted = sm.WLS(y, design, weights=weights).fit()
    residual_df = int(x.size - parameter_count)
    if settings.force_through_zero:
        intercept = 0.0
        slope = float(fitted.params[0])
        intercept_standard_error = None
        slope_standard_error = (
            _finite_or_none(fitted.bse[0]) if residual_df > 0 else None
        )
    else:
        intercept = float(fitted.params[0])
        slope = float(fitted.params[1])
        intercept_standard_error = (
            _finite_or_none(fitted.bse[0]) if residual_df > 0 else None
        )
        slope_standard_error = (
            _finite_or_none(fitted.bse[1]) if residual_df > 0 else None
        )

    predicted_standards = intercept + slope * x
    residuals = y - predicted_standards
    rmse = float(np.sqrt(np.mean(np.square(residuals))))
    residual_standard_error = (
        float(np.sqrt(np.sum(np.square(residuals)) / residual_df))
        if residual_df > 0
        else None
    )
    total_sum_squares = float(np.sum(np.square(y - np.mean(y))))
    r_squared = (
        float(1.0 - np.sum(np.square(residuals)) / total_sum_squares)
        if total_sum_squares > 0
        else None
    )

    warnings.extend(_shape_warnings(x, y, slope, settings))
    quantified = _quantify_samples(
        samples,
        standards,
        blank_response,
        concentration_unit,
        slope,
        intercept,
        settings,
        warnings,
    )
    return CalibrationResult(
        slope=slope,
        intercept=intercept,
        slope_standard_error=slope_standard_error,
        intercept_standard_error=intercept_standard_error,
        r_squared=r_squared,
        rmse=rmse,
        residual_standard_error=residual_standard_error,
        blank_response=blank_response,
        concentration_unit=concentration_unit,
        samples=tuple(quantified),
        warnings=tuple(warnings),
    )


def _weights(x: np.ndarray, weighting: WeightingMode) -> np.ndarray:
    if weighting is WeightingMode.INVERSE_X:
        return 1.0 / x
    if weighting is WeightingMode.INVERSE_X_SQUARED:
        return 1.0 / np.square(x)
    return np.ones_like(x)


def _quantify_samples(
    samples: list[CalibrationInput],
    standards: list[CalibrationInput],
    blank_response: float,
    concentration_unit: str,
    slope: float,
    intercept: float,
    settings: CalibrationSettings,
    warnings: list[CalibrationWarning],
) -> list[QuantifiedSample]:
    if slope == 0:
        raise CalibrationError("inverse prediction is undefined for a zero slope")
    standard_x = np.asarray(
        [sample.concentration for sample in standards], dtype=np.float64
    )
    minimum, maximum = float(np.min(standard_x)), float(np.max(standard_x))
    quantified: list[QuantifiedSample] = []
    for sample in samples:
        corrected = sample.file_response - blank_response
        calculated = (corrected - intercept) / slope
        is_standard = sample.sample_type is SampleType.STANDARD
        predicted = (
            intercept + slope * float(sample.concentration) if is_standard else None
        )
        residual = corrected - predicted if predicted is not None else None
        percent_error = (
            (calculated - float(sample.concentration))
            / float(sample.concentration)
            * 100.0
            if is_standard and sample.concentration != 0
            else None
        )
        if (
            sample.sample_type is SampleType.UNKNOWN
            and not minimum <= calculated <= maximum
        ):
            warnings.append(
                CalibrationWarning(
                    code="extrapolated_unknown",
                    message="Unknown concentration is outside the standard range.",
                    sample_id=sample.sample_id,
                )
            )
        if is_standard:
            _large_residual_warning(sample, residual, predicted, settings, warnings)
        measured = (
            calculated
            if sample.sample_type in {SampleType.QC, SampleType.UNKNOWN}
            else None
        )
        quantified.append(
            QuantifiedSample(
                sample_id=sample.sample_id,
                sample_type=sample.sample_type,
                raw_response=sample.file_response,
                blank_corrected_response=corrected,
                predicted_response=predicted,
                residual=residual,
                back_calculated_concentration=calculated if is_standard else None,
                back_calculation_percent_error=percent_error,
                measured_concentration=measured,
                dilution_corrected_concentration=(
                    measured * sample.dilution_factor if measured is not None else None
                ),
                concentration_unit=concentration_unit,
            )
        )
    return quantified


def _shape_warnings(
    x: np.ndarray,
    y: np.ndarray,
    slope: float,
    settings: CalibrationSettings,
) -> list[CalibrationWarning]:
    warnings: list[CalibrationWarning] = []
    if slope < 0:
        warnings.append(
            CalibrationWarning(
                code="negative_slope", message="Calibration slope is negative."
            )
        )
    level_responses = [float(np.median(y[x == level])) for level in np.unique(x)]
    if any(
        right <= left
        for left, right in zip(level_responses, level_responses[1:], strict=False)
    ):
        warnings.append(
            CalibrationWarning(
                code="non_monotonic_response",
                message="Median standard response is not strictly increasing by level.",
            )
        )
    ratio_limit = settings.upper_flattening_slope_ratio
    levels = np.unique(x)
    if ratio_limit is not None and levels.size >= 3 and slope > 0:
        top_slope = (level_responses[-1] - level_responses[-2]) / (
            levels[-1] - levels[-2]
        )
        if top_slope / slope <= ratio_limit:
            warnings.append(
                CalibrationWarning(
                    code="possible_upper_range_flattening",
                    message=(
                        "Upper-range local slope is below the configured ratio limit."
                    ),
                )
            )
    return warnings


def _large_residual_warning(
    sample: CalibrationInput,
    residual: float | None,
    predicted: float | None,
    settings: CalibrationSettings,
    warnings: list[CalibrationWarning],
) -> None:
    assert residual is not None and predicted is not None
    absolute_large = (
        settings.large_residual_absolute is not None
        and abs(residual) >= settings.large_residual_absolute
    )
    percent_large = (
        settings.large_residual_percent is not None
        and predicted != 0
        and abs(residual / predicted * 100.0) >= settings.large_residual_percent
    )
    if absolute_large or percent_large:
        warnings.append(
            CalibrationWarning(
                code="large_residual",
                message="Standard residual exceeds a configured method limit.",
                sample_id=sample.sample_id,
            )
        )


def _finite_or_none(value: float) -> float | None:
    return float(value) if np.isfinite(value) else None
