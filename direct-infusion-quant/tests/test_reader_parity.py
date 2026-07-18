"""Isolated numerical parity test for the optional pyOpenMS backend."""

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def shared_mzml_fixture() -> Path:
    paths = sorted((Path(__file__).parents[1] / "test_data").glob("*.mzML"))
    if not paths:
        pytest.skip("ignored real mzML parity fixture is unavailable")
    return paths[0]


def test_backends_match_metadata_spectra_and_extracted_response(
    shared_mzml_fixture: Path,
) -> None:
    runner = Path(__file__).with_name("reader_parity_runner.py")
    completed = subprocess.run(
        [sys.executable, str(runner), str(shared_mzml_fixture)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.stdout.startswith("PYOPENMS_UNAVAILABLE:"):
        pytest.skip(completed.stdout.strip())
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "PARITY_OK" in completed.stdout
