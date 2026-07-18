"""Tests for explicit optional mzML backend selection."""

import importlib

import pytest

from direct_infusion_quant.io import (
    MzMLBackend,
    PymzMLReader,
    PyOpenMSProcessReader,
    PyOpenMSReader,
    PyOpenMSUnavailableError,
    create_mzml_reader,
)


def test_pymzml_is_the_default_backend() -> None:
    assert isinstance(create_mzml_reader(), PymzMLReader)
    assert isinstance(create_mzml_reader(MzMLBackend.PYMZML), PymzMLReader)


def test_pyopenms_selection_is_explicit() -> None:
    assert isinstance(create_mzml_reader("pyopenms"), PyOpenMSProcessReader)


def test_missing_pyopenms_has_clear_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    path = tmp_path / "source.mzML"
    path.touch()
    original = importlib.import_module

    def missing(name: str):
        if name == "pyopenms":
            error = ModuleNotFoundError("No module named 'pyopenms'")
            error.name = "pyopenms"
            raise error
        return original(name)

    monkeypatch.setattr(importlib, "import_module", missing)
    with pytest.raises(PyOpenMSUnavailableError, match="selected explicitly"):
        PyOpenMSReader().inspect(path)
