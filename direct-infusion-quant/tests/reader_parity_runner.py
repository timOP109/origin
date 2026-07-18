"""Subprocess runner that loads pyOpenMS before potentially conflicting DLLs."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PySide6.QtWidgets import QApplication  # noqa: F401

from direct_infusion_quant.io import (
    MzMLBackend,
    PyOpenMSUnavailableError,
    create_mzml_reader,
)
from direct_infusion_quant.models import (
    ExtractionWindow,
    ProcessingSettings,
    ToleranceUnit,
)
from direct_infusion_quant.processing import WarningThresholds, process_file

TIME_ATOL_SECONDS = 1e-6
MZ_ATOL_DA = 1e-8
MZ_RTOL = 1e-10
RESPONSE_ATOL = 1e-8
RESPONSE_RTOL = 1e-6


def main(path: Path) -> None:
    pymzml_reader = create_mzml_reader(MzMLBackend.PYMZML)
    pyopenms_reader = create_mzml_reader(MzMLBackend.PYOPENMS)
    pymzml_metadata = pymzml_reader.inspect(path)
    pyopenms_metadata = pyopenms_reader.inspect(path)
    assert pymzml_metadata.spectrum_count == pyopenms_metadata.spectrum_count
    assert pymzml_metadata.ms_levels == pyopenms_metadata.ms_levels
    assert pymzml_metadata.is_centroided == pyopenms_metadata.is_centroided

    pymzml_scans = list(pymzml_reader.iter_spectra(path))
    pyopenms_scans = list(pyopenms_reader.iter_spectra(path))
    assert len(pymzml_scans) == len(pyopenms_scans)
    for left, right in zip(pymzml_scans, pyopenms_scans, strict=True):
        assert left.ms_level == right.ms_level
        np.testing.assert_allclose(
            left.elapsed_time_seconds,
            right.elapsed_time_seconds,
            rtol=0,
            atol=TIME_ATOL_SECONDS,
        )
        np.testing.assert_allclose(left.mz, right.mz, rtol=MZ_RTOL, atol=MZ_ATOL_DA)
        np.testing.assert_allclose(
            left.intensity,
            right.intensity,
            rtol=RESPONSE_RTOL,
            atol=RESPONSE_ATOL,
        )

    first_scan = next(scan for scan in pymzml_scans if scan.mz.size)
    target = float(first_scan.mz[first_scan.mz.size // 2])
    window = ExtractionWindow(
        name="parity window",
        target_mz=target,
        tolerance=0.01,
        tolerance_unit=ToleranceUnit.DA,
    )
    end_time = max(scan.elapsed_time_seconds for scan in pymzml_scans) + 1e-6
    settings = ProcessingSettings(time_start_seconds=0, time_end_seconds=end_time)
    left_result = process_file(
        pymzml_scans,
        [window],
        settings,
        WarningThresholds(),
        quantifier_window_id=window.id,
    )
    right_result = process_file(
        pyopenms_scans,
        [window],
        settings,
        WarningThresholds(),
        quantifier_window_id=window.id,
    )
    left_responses = [scan.window_responses[window.id] for scan in left_result.scans]
    right_responses = [scan.window_responses[window.id] for scan in right_result.scans]
    np.testing.assert_allclose(
        left_responses,
        right_responses,
        rtol=RESPONSE_RTOL,
        atol=RESPONSE_ATOL,
    )
    print(
        f"PARITY_OK spectra={len(pymzml_scans)} "
        f"time_atol_s={TIME_ATOL_SECONDS} mz_atol_da={MZ_ATOL_DA} "
        f"response_rtol={RESPONSE_RTOL}"
    )


if __name__ == "__main__":
    try:
        main(Path(sys.argv[1]))
    except PyOpenMSUnavailableError as error:
        print(f"PYOPENMS_UNAVAILABLE: {error}")
