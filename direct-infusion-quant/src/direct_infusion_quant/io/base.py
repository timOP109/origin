"""Backend-neutral mzML reader contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True, slots=True)
class MzMLMetadata:
    """Metadata needed before processing an mzML file."""

    path: Path
    spectrum_count: int | None
    ms_levels: frozenset[int]
    is_centroided: bool | None


@dataclass(frozen=True, slots=True)
class SpectrumRecord:
    """Backend-neutral representation of one spectrum."""

    native_id: str
    index: int
    ms_level: int
    elapsed_time_seconds: float
    mz: NDArray[np.float64]
    intensity: NDArray[np.float64]


class MzMLReader(ABC):
    """Interface implemented by current and future mzML backends."""

    @abstractmethod
    def inspect(self, path: Path) -> MzMLMetadata:
        """Read file-level metadata without retaining the complete file."""

    @abstractmethod
    def iter_spectra(self, path: Path) -> Iterator[SpectrumRecord]:
        """Stream spectra in acquisition order."""
