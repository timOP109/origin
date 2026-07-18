"""Optional on-disc pyOpenMS implementation of the mzML reader contract."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np

from direct_infusion_quant.io.base import MzMLMetadata, MzMLReader, SpectrumRecord


class PyOpenMSUnavailableError(RuntimeError):
    """Raised when the explicitly selected optional backend is not installed."""


class PyOpenMSReadError(RuntimeError):
    """Raised when pyOpenMS cannot provide required mzML spectrum data."""


class PyOpenMSReader(MzMLReader):
    """Read indexed mzML spectra through pyOpenMS without loading all peaks."""

    def inspect(self, path: Path) -> MzMLMetadata:
        """Read spectrum headers through the on-disc experiment metadata map."""

        resolved = _validate_path(path)
        oms = _import_pyopenms()
        experiment = _open_experiment(oms, resolved)
        try:
            metadata = experiment.getMetaData()
            count = int(experiment.getNrSpectra())
            ms_levels: set[int] = set()
            centroid_states: set[bool | None] = set()
            for index in range(count):
                spectrum = metadata.getSpectrum(index)
                ms_levels.add(int(spectrum.getMSLevel()))
                centroid_states.add(_centroid_state(oms, spectrum))
        except Exception as error:
            raise PyOpenMSReadError(
                f"Could not inspect mzML file '{resolved}' with pyOpenMS."
            ) from error
        return MzMLMetadata(
            path=resolved,
            spectrum_count=count,
            ms_levels=frozenset(ms_levels),
            is_centroided=_combined_centroid_state(centroid_states),
        )

    def iter_spectra(self, path: Path) -> Iterator[SpectrumRecord]:
        """Yield one backend-neutral spectrum at a time from an on-disc file."""

        resolved = _validate_path(path)
        oms = _import_pyopenms()
        experiment = _open_experiment(oms, resolved)
        try:
            for index in range(int(experiment.getNrSpectra())):
                spectrum = experiment.getSpectrum(index)
                mz_values, intensity_values = spectrum.get_peaks()
                mz = np.ascontiguousarray(mz_values, dtype=np.float64)
                intensity = np.ascontiguousarray(intensity_values, dtype=np.float64)
                elapsed_seconds = float(spectrum.getRT())
                if not np.isfinite(elapsed_seconds):
                    raise PyOpenMSReadError(
                        f"Spectrum {spectrum.getNativeID()!r} has invalid "
                        "acquisition time."
                    )
                yield SpectrumRecord(
                    native_id=str(spectrum.getNativeID()),
                    index=index,
                    ms_level=int(spectrum.getMSLevel()),
                    elapsed_time_seconds=elapsed_seconds,
                    mz=mz,
                    intensity=intensity,
                )
        except PyOpenMSReadError:
            raise
        except Exception as error:
            raise PyOpenMSReadError(
                f"Could not read mzML file '{resolved}' with pyOpenMS."
            ) from error


def _import_pyopenms() -> ModuleType:
    try:
        return importlib.import_module("pyopenms")
    except (ImportError, OSError) as error:
        raise PyOpenMSUnavailableError(
            "The pyOpenMS backend was selected explicitly, but pyOpenMS is not "
            "installed or its native libraries could not be loaded. Install a "
            "compatible DirectInfusionQuant 'pyopenms' extra."
        ) from error


def _open_experiment(oms: ModuleType, path: Path) -> Any:
    try:
        experiment = oms.OnDiscMSExperiment()
        if not experiment.openFile(str(path)):
            raise PyOpenMSReadError(
                "pyOpenMS OnDiscMSExperiment requires a readable indexed mzML "
                f"file: '{path}'."
            )
        return experiment
    except PyOpenMSReadError:
        raise
    except Exception as error:
        raise PyOpenMSReadError(
            f"Could not open mzML file '{path}' with pyOpenMS."
        ) from error


def _centroid_state(oms: ModuleType, spectrum: Any) -> bool | None:
    spectrum_type = int(spectrum.getType())
    types = oms.SpectrumSettings.SpectrumType
    if spectrum_type == int(types.CENTROID):
        return True
    if spectrum_type == int(types.PROFILE):
        return False
    return None


def _combined_centroid_state(states: set[bool | None]) -> bool | None:
    if False in states:
        return False
    if states == {True}:
        return True
    return None


def _validate_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"mzML file does not exist: {resolved}")
    return resolved
