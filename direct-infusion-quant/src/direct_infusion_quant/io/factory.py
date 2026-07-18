"""Explicit mzML backend selection without automatic fallback."""

from __future__ import annotations

from direct_infusion_quant.io.base import MzMLReader
from direct_infusion_quant.io.pymzml_reader import PymzMLReader
from direct_infusion_quant.io.pyopenms_process_reader import PyOpenMSProcessReader
from direct_infusion_quant.models import MzMLBackend


def create_mzml_reader(
    backend: MzMLBackend | str = MzMLBackend.PYMZML,
) -> MzMLReader:
    """Create only the requested reader; never silently select another backend."""

    selected = MzMLBackend(backend)
    if selected is MzMLBackend.PYMZML:
        return PymzMLReader()
    return PyOpenMSProcessReader()
