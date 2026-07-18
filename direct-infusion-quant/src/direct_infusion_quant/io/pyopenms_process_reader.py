"""GUI-safe pyOpenMS reader using an isolated native-library process."""

from __future__ import annotations

import pickle
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np

from direct_infusion_quant.io.base import MzMLMetadata, MzMLReader, SpectrumRecord
from direct_infusion_quant.io.pyopenms_reader import (
    PyOpenMSReadError,
    PyOpenMSUnavailableError,
)


class PyOpenMSProcessReader(MzMLReader):
    """Read through a child that imports pyOpenMS before Qt native libraries."""

    def inspect(self, path: Path) -> MzMLMetadata:
        """Return metadata received from an isolated pyOpenMS child process."""

        process = _start_worker("inspect", path)
        try:
            kind, payload = _receive(process)
            if kind != "metadata":
                _raise_message(kind, payload)
            return MzMLMetadata(
                path=Path(payload["path"]),
                spectrum_count=payload["spectrum_count"],
                ms_levels=frozenset(payload["ms_levels"]),
                is_centroided=payload["is_centroided"],
            )
        finally:
            _finish(process)

    def iter_spectra(self, path: Path) -> Iterator[SpectrumRecord]:
        """Stream spectra over the local subprocess pipe one record at a time."""

        process = _start_worker("stream", path)
        completed = False
        try:
            while True:
                kind, payload = _receive(process)
                if kind == "done":
                    completed = True
                    return
                if kind != "spectrum":
                    _raise_message(kind, payload)
                yield SpectrumRecord(
                    native_id=payload["native_id"],
                    index=payload["index"],
                    ms_level=payload["ms_level"],
                    elapsed_time_seconds=payload["elapsed_time_seconds"],
                    mz=np.ascontiguousarray(payload["mz"], dtype=np.float64),
                    intensity=np.ascontiguousarray(
                        payload["intensity"], dtype=np.float64
                    ),
                )
        finally:
            if not completed and process.poll() is None:
                process.terminate()
            _finish(process, allow_terminated=not completed)


def _start_worker(operation: str, path: Path) -> subprocess.Popen[bytes]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"mzML file does not exist: {resolved}")
    if getattr(sys, "frozen", False):
        worker = Path(sys.executable).with_name("DirectInfusionQuantPyOpenMSWorker.exe")
        if not worker.is_file():
            raise PyOpenMSUnavailableError(
                "The packaged pyOpenMS worker executable is missing. "
                "Reinstall DirectInfusionQuant or select the pymzML backend."
            )
        command = [str(worker), operation, str(resolved)]
    else:
        worker = Path(__file__).with_name("_pyopenms_worker.py")
        command = [sys.executable, str(worker), operation, str(resolved)]
    return subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )


def _receive(process: subprocess.Popen[bytes]) -> tuple[str, Any]:
    assert process.stdout is not None
    try:
        return pickle.load(process.stdout)
    except EOFError as error:
        process.wait()
        details = _stderr(process)
        raise PyOpenMSReadError(
            "The isolated pyOpenMS reader exited without a complete response."
            + (f" Details: {details}" if details else "")
        ) from error


def _raise_message(kind: str, payload: dict[str, Any]) -> None:
    message = payload.get("message", "Unknown isolated pyOpenMS error")
    if kind == "unavailable":
        raise PyOpenMSUnavailableError(
            "The pyOpenMS backend was selected explicitly, but its isolated "
            f"worker could not load pyOpenMS: {message}"
        )
    if kind == "error":
        raise PyOpenMSReadError(message)
    raise PyOpenMSReadError(f"Unexpected pyOpenMS worker message: {kind!r}")


def _finish(
    process: subprocess.Popen[bytes], *, allow_terminated: bool = False
) -> None:
    try:
        return_code = process.wait(timeout=10)
    except subprocess.TimeoutExpired as error:
        process.kill()
        process.wait()
        raise PyOpenMSReadError("The isolated pyOpenMS reader did not exit.") from error
    finally:
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
    if return_code != 0 and not allow_terminated:
        raise PyOpenMSReadError(
            f"The isolated pyOpenMS reader exited with code {return_code}."
        )


def _stderr(process: subprocess.Popen[bytes]) -> str:
    if process.stderr is None:
        return ""
    return process.stderr.read().decode(errors="replace").strip()
