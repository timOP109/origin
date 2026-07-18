"""Streaming pymzML implementation of the mzML reader contract."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pymzml

from direct_infusion_quant.io.base import MzMLMetadata, MzMLReader, SpectrumRecord


class PymzMLReadError(RuntimeError):
    """Raised when pymzML cannot provide required spectrum data."""


class PymzMLReader(MzMLReader):
    """Read spectra sequentially through pymzML without retaining the file."""

    def inspect(self, path: Path) -> MzMLMetadata:
        """Stream through a file to collect MS levels and representation metadata."""

        resolved = _validate_path(path)
        count = 0
        ms_levels: set[int] = set()
        centroid_states: set[bool | None] = set()
        try:
            for spectrum in pymzml.run.Reader(resolved):
                count += 1
                if spectrum.ms_level is not None:
                    ms_levels.add(int(spectrum.ms_level))
                centroid_states.add(_centroid_state(spectrum))
        except Exception as error:
            raise PymzMLReadError(
                f"Could not inspect mzML file '{resolved}'."
            ) from error

        is_centroided: bool | None
        if False in centroid_states:
            is_centroided = False
        elif centroid_states == {True}:
            is_centroided = True
        else:
            is_centroided = None
        return MzMLMetadata(
            path=resolved,
            spectrum_count=count,
            ms_levels=frozenset(ms_levels),
            is_centroided=is_centroided,
        )

    def iter_spectra(self, path: Path) -> Iterator[SpectrumRecord]:
        """Yield one backend-neutral spectrum at a time in acquisition order."""

        resolved = _validate_path(path)
        try:
            for index, spectrum in enumerate(pymzml.run.Reader(resolved)):
                ms_level = spectrum.ms_level
                if ms_level is None:
                    raise PymzMLReadError(
                        f"Spectrum {spectrum.ID!r} has no MS-level metadata."
                    )
                peaks = np.asarray(spectrum.peaks("raw"), dtype=np.float64)
                if peaks.size == 0:
                    mz = np.empty(0, dtype=np.float64)
                    intensity = np.empty(0, dtype=np.float64)
                elif peaks.ndim == 2 and peaks.shape[1] == 2:
                    mz = np.ascontiguousarray(peaks[:, 0], dtype=np.float64)
                    intensity = np.ascontiguousarray(peaks[:, 1], dtype=np.float64)
                else:
                    raise PymzMLReadError(
                        f"Spectrum {spectrum.ID!r} has invalid centroid arrays."
                    )
                elapsed_seconds = float(spectrum.scan_time_in_minutes()) * 60.0
                if not np.isfinite(elapsed_seconds):
                    raise PymzMLReadError(
                        f"Spectrum {spectrum.ID!r} has invalid acquisition time."
                    )
                yield SpectrumRecord(
                    native_id=str(spectrum.ID),
                    index=index,
                    ms_level=int(ms_level),
                    elapsed_time_seconds=elapsed_seconds,
                    mz=mz,
                    intensity=intensity,
                )
        except PymzMLReadError:
            raise
        except Exception as error:
            raise PymzMLReadError(f"Could not read mzML file '{resolved}'.") from error


def _validate_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"mzML file does not exist: {resolved}")
    return resolved


def _centroid_state(spectrum: Any) -> bool | None:
    if spectrum["profile spectrum"] is not None:
        return False
    if spectrum["centroid spectrum"] is not None:
        return True
    return None
