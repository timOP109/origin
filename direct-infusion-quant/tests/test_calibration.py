"""Numerical tests for calibration and inverse quantification."""

import math

import pytest

from direct_infusion_quant.calibration import (
    CalibrationError,
    CalibrationInput,
    calibrate_and_quantify,
)
from direct_infusion_quant.models import (
    BlankCorrectionMethod,
    CalibrationSettings,
    SampleType,
    WeightingMode,
)


def standard(
    identifier: str, concentration: float, response: float, unit: str = "mg/L"
):
    return CalibrationInput(
        sample_id=identifier,
        sample_type=SampleType.STANDARD,
        concentration=concentration,
        concentration_unit=unit,
        file_response=response,
    )


def test_manually_calculated_unweighted_reference_dataset() -> None:
    result = calibrate_and_quantify(
        [
            standard("s1", 1, 2),
            standard("s2", 2, 4),
            standard("s3", 3, 5),
        ],
        CalibrationSettings(blank_correction=BlankCorrectionMethod.NONE),
    )
    assert result.slope == pytest.approx(1.5)
    assert result.intercept == pytest.approx(2 / 3)
    assert result.slope_standard_error == pytest.approx(math.sqrt(1 / 12))
    assert result.intercept_standard_error == pytest.approx(math.sqrt(7 / 18))
    assert result.r_squared == pytest.approx(27 / 28)
    assert result.rmse == pytest.approx(math.sqrt(1 / 18))
    assert result.residual_standard_error == pytest.approx(math.sqrt(1 / 6))
    assert [sample.predicted_response for sample in result.samples] == pytest.approx(
        [13 / 6, 11 / 3, 31 / 6]
    )
    assert [sample.residual for sample in result.samples] == pytest.approx(
        [-1 / 6, 1 / 3, -1 / 6]
    )
    assert [sample.back_calculated_concentration for sample in result.samples] == (
        pytest.approx([8 / 9, 20 / 9, 26 / 9])
    )
    assert [sample.back_calculation_percent_error for sample in result.samples] == (
        pytest.approx([-100 / 9, 100 / 9, -100 / 27])
    )


def test_pooled_median_blank_and_dilution_after_inverse_prediction() -> None:
    samples = [
        CalibrationInput("b1", SampleType.BLANK, 1.0),
        CalibrationInput("b2", SampleType.BLANK, 3.0),
        standard("s1", 1, 4),
        standard("s2", 2, 6),
        standard("s3", 3, 8),
        CalibrationInput("u1", SampleType.UNKNOWN, 7.0, dilution_factor=5.0),
        CalibrationInput("qc1", SampleType.QC, 5.0, dilution_factor=2.0),
    ]
    result = calibrate_and_quantify(samples, CalibrationSettings())
    unknown = next(sample for sample in result.samples if sample.sample_id == "u1")
    qc = next(sample for sample in result.samples if sample.sample_id == "qc1")
    assert result.blank_response == 2.0
    assert result.slope == pytest.approx(2.0)
    assert result.intercept == pytest.approx(0.0, abs=1e-12)
    assert unknown.measured_concentration == pytest.approx(2.5)
    assert unknown.dilution_corrected_concentration == pytest.approx(12.5)
    assert qc.measured_concentration == pytest.approx(1.5)
    assert qc.dilution_corrected_concentration == pytest.approx(3.0)


@pytest.mark.parametrize(
    "weighting", [WeightingMode.INVERSE_X, WeightingMode.INVERSE_X_SQUARED]
)
def test_weighting_rejects_zero_standard(weighting: WeightingMode) -> None:
    with pytest.raises(CalibrationError, match="zero concentration"):
        calibrate_and_quantify(
            [standard("s0", 0, 1), standard("s1", 1, 2), standard("s2", 2, 3)],
            CalibrationSettings(
                blank_correction=BlankCorrectionMethod.NONE, weighting=weighting
            ),
        )


def test_inconsistent_units_are_rejected_with_structured_warning() -> None:
    with pytest.raises(CalibrationError) as captured:
        calibrate_and_quantify(
            [standard("s1", 1, 2, "mg/L"), standard("s2", 2, 4, "umol/L")],
            CalibrationSettings(blank_correction=BlankCorrectionMethod.NONE),
        )
    assert captured.value.warnings[0].code == "inconsistent_concentration_units"


def test_force_zero_and_weighting_options_fit() -> None:
    samples = [standard("s1", 1, 2), standard("s2", 2, 4), standard("s3", 3, 6)]
    for weighting in WeightingMode:
        result = calibrate_and_quantify(
            samples,
            CalibrationSettings(
                blank_correction=BlankCorrectionMethod.NONE,
                weighting=weighting,
                force_through_zero=True,
            ),
        )
        assert result.slope == pytest.approx(2.0)
        assert result.intercept == 0


def test_configured_warnings_do_not_remove_samples() -> None:
    samples = [
        standard("s1", 1, 2),
        standard("s2", 2, 5),
        standard("s3", 3, 5.1),
        CalibrationInput("u1", SampleType.UNKNOWN, 20),
    ]
    result = calibrate_and_quantify(
        samples,
        CalibrationSettings(
            blank_correction=BlankCorrectionMethod.NONE,
            large_residual_absolute=0.1,
            upper_flattening_slope_ratio=0.5,
        ),
    )
    codes = {warning.code for warning in result.warnings}
    assert "possible_upper_range_flattening" in codes
    assert "large_residual" in codes
    assert "extrapolated_unknown" in codes
    assert len(result.samples) == len(samples)


def test_absent_blank_and_few_levels_warn() -> None:
    result = calibrate_and_quantify(
        [standard("s1", 1, 2), standard("s2", 2, 4)], CalibrationSettings()
    )
    assert {warning.code for warning in result.warnings} >= {
        "absent_blanks",
        "fewer_than_three_nonzero_levels",
    }
