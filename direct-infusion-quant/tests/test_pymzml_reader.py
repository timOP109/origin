"""Tests for the streaming pymzML adapter."""

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from direct_infusion_quant.io.pymzml_reader import PymzMLReader


class FakeSpectrum:
    def __init__(
        self,
        identifier: str,
        time_minutes: float,
        peaks: list[tuple[float, float]],
        *,
        ms_level: int = 1,
        centroided: bool | None = True,
    ) -> None:
        self.ID = identifier
        self.ms_level = ms_level
        self._time = time_minutes
        self._peaks = peaks
        self._centroided = centroided

    def peaks(self, peak_type: str) -> np.ndarray:
        assert peak_type == "raw"
        return np.asarray(self._peaks, dtype=np.float64)

    def scan_time_in_minutes(self) -> float:
        return self._time

    def __getitem__(self, key: str) -> bool | None:
        if key == "profile spectrum":
            return self._centroided is False or None
        if key == "centroid spectrum":
            return self._centroided is True or None
        raise KeyError(key)


class CountingReader:
    pulled = 0

    def __init__(self, path: Path) -> None:
        self.path = path

    def __iter__(self):
        for item in [
            FakeSpectrum("scan=1", 0.5, [(100.0, 5.0)]),
            FakeSpectrum("scan=2", 1.0, [(200.0, 7.0)], ms_level=2),
        ]:
            type(self).pulled += 1
            yield item


def test_iter_spectra_is_lazy_and_converts_time_to_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "test.mzML"
    path.touch()
    CountingReader.pulled = 0
    monkeypatch.setattr("pymzml.run.Reader", CountingReader)

    iterator = PymzMLReader().iter_spectra(path)
    assert CountingReader.pulled == 0
    first = next(iterator)
    assert CountingReader.pulled == 1
    assert first.native_id == "scan=1"
    assert first.elapsed_time_seconds == pytest.approx(30.0)
    np.testing.assert_array_equal(first.mz, [100.0])
    np.testing.assert_array_equal(first.intensity, [5.0])


def test_inspect_reports_levels_count_and_profile_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "profile.mzML"
    path.touch()

    def fake_reader(_path: Path) -> list[Any]:
        return [
            FakeSpectrum("scan=1", 0, [], centroided=True),
            FakeSpectrum("scan=2", 1, [], ms_level=2, centroided=False),
        ]

    monkeypatch.setattr("pymzml.run.Reader", fake_reader)
    metadata = PymzMLReader().inspect(path)
    assert metadata.spectrum_count == 2
    assert metadata.ms_levels == frozenset({1, 2})
    assert metadata.is_centroided is False
