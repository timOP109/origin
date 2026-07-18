"""Private subprocess entry point; load pyOpenMS before the application stack."""

from __future__ import annotations

import pickle
import sys
import traceback
from pathlib import Path
from typing import Any


def _send(kind: str, payload: Any) -> None:
    pickle.dump((kind, payload), sys.stdout.buffer, protocol=pickle.HIGHEST_PROTOCOL)
    sys.stdout.buffer.flush()


def run_operation(operation: str, source_path: str) -> int:
    """Run one worker operation after importing pyOpenMS before Qt."""

    try:
        import pyopenms  # noqa: F401
    except (ImportError, OSError) as error:
        _send(
            "unavailable",
            {
                "message": str(error),
                "traceback": traceback.format_exc(),
            },
        )
        return 2

    from direct_infusion_quant.io.pyopenms_reader import PyOpenMSReader

    try:
        path = Path(source_path)
        reader = PyOpenMSReader()
        if operation == "inspect":
            metadata = reader.inspect(path)
            _send(
                "metadata",
                {
                    "path": str(metadata.path),
                    "spectrum_count": metadata.spectrum_count,
                    "ms_levels": tuple(metadata.ms_levels),
                    "is_centroided": metadata.is_centroided,
                },
            )
        elif operation == "stream":
            for spectrum in reader.iter_spectra(path):
                _send(
                    "spectrum",
                    {
                        "native_id": spectrum.native_id,
                        "index": spectrum.index,
                        "ms_level": spectrum.ms_level,
                        "elapsed_time_seconds": spectrum.elapsed_time_seconds,
                        "mz": spectrum.mz,
                        "intensity": spectrum.intensity,
                    },
                )
            _send("done", None)
        else:
            raise ValueError(f"Unknown pyOpenMS worker operation: {operation!r}")
        return 0
    except Exception as error:
        _send(
            "error",
            {
                "message": str(error),
                "traceback": traceback.format_exc(),
            },
        )
        return 1


def main() -> int:
    return run_operation(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    raise SystemExit(main())
