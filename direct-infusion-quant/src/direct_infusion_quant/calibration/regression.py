"""GUI-independent calibration fitting and inverse quantification."""

from __future__ import annotations

from dataclasses import dataclass
from math import comb

import numpy as np
import statsmodels.api as sm

from direct_infusion_quant.models import (
    BlankCorrectionMethod,
    CalibrationSettings,
    RegressionModel,
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

    regression_model: RegressionModel
    polynomial_coefficients: tuple[float, ...]
    coefficient_standard_errors: tuple[float | None, ...]
    residual_degrees_of_freedom: int
    design_condition_number: float
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

    def predict_response(self, concentration: float | np.ndarray) -> float | np.ndarray:
        """Evaluate the fitted response using coefficients in ascending order."""

        predicted = np.polynomial.polynomial.polyval(
            concentration, self.polynomial_coefficients
        )
        return float(predicted) if np.ndim(predicted) == 0 else predicted


def calibrate_and_quantify(
    samples: list[CalibrationInput], settings: CalibrationSettings
) -> CalibrationResult:
    """Fit the explicitly selected model and inverse-predict QC/unknown samples."""

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

    unique_levels = np.unique(x)
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

    degree = settings.regression_model.degree
    if degree > 1:
        required_levels = degree + 3
        if unique_levels.size < required_levels:
            raise CalibrationError(
                f"{settings.regression_model.value.title()} calibration requires at "
                f"least {required_levels} distinct standard levels; "
                f"{unique_levels.size} were supplied."
            )
    parameter_count = 1 if settings.force_through_zero else degree + 1
    if x.size < parameter_count:
        raise CalibrationError("insufficient standards for the selected regression")

    (
        coefficients,
        coefficient_standard_errors,
        residual_df,
        condition_number,
    ) = _fit_polynomial(x, y, settings)
    if degree == 1 and coefficients[1] == 0:
        raise CalibrationError("inverse prediction is undefined for a zero slope")
    predicted_standards = np.polynomial.polynomial.polyval(x, coefficients)
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

    if degree > 1 and not _is_monotonic_over_range(
        coefficients, float(np.min(x)), float(np.max(x))
    ):
        warning = CalibrationWarning(
            code="non_monotonic_fitted_curve",
            message=(
                "The fitted polynomial is not monotonic across the calibrated "
                "concentration range; inverse quantification is ambiguous."
            ),
        )
        raise CalibrationError(warning.message, (warning,))

    warnings.extend(_shape_warnings(x, y, coefficients, settings))
    quantified = _quantify_samples(
        samples,
        standards,
        blank_response,
        concentration_unit,
        coefficients,
        settings,
        warnings,
    )
    intercept = float(coefficients[0])
    slope = float(coefficients[1])
    intercept_se = coefficient_standard_errors[0]
    slope_se = coefficient_standard_errors[1]
    return CalibrationResult(
        regression_model=settings.regression_model,
        polynomial_coefficients=tuple(float(value) for value in coefficients),
        coefficient_standard_errors=tuple(coefficient_standard_errors),
        residual_degrees_of_freedom=residual_df,
        design_condition_number=condition_number,
        slope=slope,
        intercept=intercept,
        slope_standard_error=slope_se,
        intercept_standard_error=intercept_se,
        r_squared=r_squared,
        rmse=rmse,
        residual_standard_error=residual_standard_error,
        blank_response=blank_response,
        concentration_unit=concentration_unit,
        samples=tuple(quantified),
        warnings=tuple(warnings),
    )


def _fit_polynomial(
    x: np.ndarray, y: np.ndarray, settings: CalibrationSettings
) -> tuple[np.ndarray, tuple[float | None, ...], int, float]:
    degree = settings.regression_model.degree
    weights = _weights(x, settings.weighting)
    if settings.force_through_zero:
        design = x[:, None]
        transform = np.asarray([[0.0], [1.0]])
    elif degree == 1:
        design = sm.add_constant(x)
        transform = np.eye(2)
    else:
        centre = float((np.min(x) + np.max(x)) / 2.0)
        scale = float((np.max(x) - np.min(x)) / 2.0)
        if scale == 0:
            raise CalibrationError("polynomial calibration requires distinct levels")
        z = (x - centre) / scale
        design = np.polynomial.polynomial.polyvander(z, degree)
        transform = _scaled_to_raw_transform(degree, centre, scale)
    if np.linalg.matrix_rank(design) < design.shape[1]:
        raise CalibrationError("calibration design matrix is rank deficient")
    fitted = sm.WLS(y, design, weights=weights).fit()
    residual_df = int(x.size - design.shape[1])
    coefficients = transform @ np.asarray(fitted.params, dtype=np.float64)
    if residual_df > 0:
        covariance = transform @ np.asarray(fitted.cov_params()) @ transform.T
        standard_errors = tuple(
            _finite_or_none(float(np.sqrt(max(value, 0.0))))
            for value in np.diag(covariance)
        )
        if settings.force_through_zero:
            standard_errors = (None, standard_errors[1])
    else:
        standard_errors = tuple(None for _ in range(degree + 1))
    condition_number = float(np.linalg.cond(design))
    return coefficients, standard_errors, residual_df, condition_number


def _scaled_to_raw_transform(degree: int, centre: float, scale: float) -> np.ndarray:
    transform = np.zeros((degree + 1, degree + 1), dtype=np.float64)
    for scaled_power in range(degree + 1):
        for raw_power in range(scaled_power + 1):
            transform[raw_power, scaled_power] = (
                comb(scaled_power, raw_power)
                * (-centre) ** (scaled_power - raw_power)
                / scale**scaled_power
            )
    return transform


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
    coefficients: np.ndarray,
    settings: CalibrationSettings,
    warnings: list[CalibrationWarning],
) -> list[QuantifiedSample]:
    standard_x = np.asarray(
        [sample.concentration for sample in standards], dtype=np.float64
    )
    minimum, maximum = float(np.min(standard_x)), float(np.max(standard_x))
    quantified: list[QuantifiedSample] = []
    for sample in samples:
        corrected = sample.file_response - blank_response
        calculated, inverse_status = _inverse_prediction(
            corrected,
            coefficients,
            minimum,
            maximum,
            allow_extrapolation=settings.regression_model is RegressionModel.LINEAR,
        )
        is_standard = sample.sample_type is SampleType.STANDARD
        predicted = (
            float(
                np.polynomial.polynomial.polyval(
                    float(sample.concentration), coefficients
                )
            )
            if is_standard
            else None
        )
        residual = corrected - predicted if predicted is not None else None
        percent_error = (
            (calculated - float(sample.concentration))
            / float(sample.concentration)
            * 100.0
            if is_standard and sample.concentration != 0 and calculated is not None
            else None
        )
        should_warn_inverse = inverse_status is not None and (
            settings.regression_model is not RegressionModel.LINEAR
            or sample.sample_type is SampleType.UNKNOWN
        )
        if should_warn_inverse and sample.sample_type in {
            SampleType.STANDARD,
            SampleType.QC,
            SampleType.UNKNOWN,
        }:
            code = (
                "extrapolated_unknown"
                if sample.sample_type is SampleType.UNKNOWN
                else inverse_status
            )
            message = (
                "Unknown concentration is outside the standard range."
                if calculated is not None
                else "No unique inverse concentration inside the calibrated range."
            )
            warnings.append(
                CalibrationWarning(
                    code=code,
                    message=message,
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


def _inverse_prediction(
    response: float,
    coefficients: np.ndarray,
    minimum: float,
    maximum: float,
    *,
    allow_extrapolation: bool,
) -> tuple[float | None, str | None]:
    adjusted = np.asarray(coefficients, dtype=np.float64).copy()
    adjusted[0] -= response
    roots = np.polynomial.polynomial.polyroots(adjusted)
    real_roots = sorted(
        float(root.real)
        for root in roots
        if abs(root.imag) <= 1e-8 * max(1.0, abs(root.real))
    )
    unique_roots: list[float] = []
    for root in real_roots:
        if not unique_roots or not np.isclose(
            root, unique_roots[-1], rtol=1e-9, atol=1e-12
        ):
            unique_roots.append(root)
    range_tolerance = max(1e-12, (maximum - minimum) * 1e-9)
    in_range = [
        root
        for root in unique_roots
        if minimum - range_tolerance <= root <= maximum + range_tolerance
    ]
    if len(in_range) == 1:
        return min(max(in_range[0], minimum), maximum), None
    if allow_extrapolation and len(unique_roots) == 1:
        return unique_roots[0], "inverse_prediction_outside_range"
    return None, (
        "ambiguous_inverse_prediction"
        if len(in_range) > 1
        else "inverse_prediction_outside_range"
    )


def _is_monotonic_over_range(
    coefficients: np.ndarray, minimum: float, maximum: float
) -> bool:
    derivative = np.polynomial.polynomial.polyder(coefficients)
    grid = np.linspace(minimum, maximum, 1001)
    values = np.polynomial.polynomial.polyval(grid, derivative)
    scale = max(1.0, float(np.max(np.abs(values))))
    tolerance = scale * 1e-10
    nondecreasing = bool(np.all(values >= -tolerance) and np.any(values > tolerance))
    nonincreasing = bool(np.all(values <= tolerance) and np.any(values < -tolerance))
    return nondecreasing or nonincreasing


def _shape_warnings(
    x: np.ndarray,
    y: np.ndarray,
    coefficients: np.ndarray,
    settings: CalibrationSettings,
) -> list[CalibrationWarning]:
    warnings: list[CalibrationWarning] = []
    levels = np.unique(x)
    predicted_limits = np.polynomial.polynomial.polyval(
        [float(levels[0]), float(levels[-1])], coefficients
    )
    overall_slope = (
        float((predicted_limits[1] - predicted_limits[0]) / (levels[-1] - levels[0]))
        if levels.size > 1
        else float(coefficients[1])
    )
    if overall_slope < 0:
        warnings.append(
            CalibrationWarning(
                code="negative_slope", message="Calibration trend is negative."
            )
        )
    level_responses = [float(np.median(y[x == level])) for level in levels]
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
    if ratio_limit is not None and levels.size >= 3 and overall_slope > 0:
        top_slope = float(
            (level_responses[-1] - level_responses[-2]) / (levels[-1] - levels[-2])
        )
        if top_slope / overall_slope <= ratio_limit:
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
