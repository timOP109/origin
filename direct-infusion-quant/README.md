# DirectInfusionQuant

DirectInfusionQuant is a Windows desktop application for quantitative analysis of
peptides measured by direct-infusion mass spectrometry from centroided mzML files.

The application includes streaming mzML extraction, calibration, project persistence,
reproducibility exports, and a Qt Widgets workflow.

## Requirements

- Windows 10 or later
- Python 3.11

## Setup

From this directory in PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

### Optional pyOpenMS backend

`pymzML` remains the installed and runtime default. To add the optional pyOpenMS
reader on a supported Python 3.11/Windows platform:

```powershell
python -m pip install -e ".[dev,pyopenms]"
```

Backend selection is explicit in code and never falls back silently:

```python
from direct_infusion_quant.io import MzMLBackend, create_mzml_reader

reader = create_mzml_reader(MzMLBackend.PYOPENMS)
```

Selecting `PYOPENMS` uses an isolated worker process that imports OpenMS before Qt,
avoiding Windows native-DLL load-order conflicts. Missing or incompatible native
libraries raise a clear `PyOpenMSUnavailableError` in the parent application. The
worker uses `OnDiscMSExperiment`, so source files must be indexed mzML; spectra are
sent back one at a time and complete peak arrays are not retained. Closing iteration
early terminates only the owned worker process. The GUI records whichever backend the
user explicitly selects; pymzML remains the default.

pyOpenMS carries compiled OpenMS libraries and makes installers substantially larger.
Windows packaging therefore needs a separate optional build (or explicit collection
of pyOpenMS binaries and data files); it should not be bundled into the normal pymzML
installer. Packaging must also be tested against the exact Python, pyOpenMS, and
PyInstaller versions used for release.

Reader parity tests compare the same mzML spectra using absolute time tolerance
`1e-6 s`, m/z tolerance `atol=1e-8 Da, rtol=1e-10`, and intensity/extracted-response
tolerance `rtol=1e-6, atol=1e-8`. They skip when pyOpenMS or the ignored real-data
fixture is unavailable.

## Launch

```powershell
direct-infusion-quant
```

Alternatively:

```powershell
python -m direct_infusion_quant
```

The left side of the main window follows the analysis in order: files, target,
fixed acquisition-time duration with optional per-sample start positions,
processing results, calibration, and export. Stable-period assessment reports
recommendations for review and never applies them silently.
Long-running mzML processing runs in a worker thread; progress and scan counts appear
at the bottom of the window and can be cancelled without silently retaining partial
results.

The **Files and Samples** page includes an explicit reader-backend selector. Its
choice is saved in the project and written to reproducibility exports. Existing
projects without a recorded choice reopen with the historical `pymzml` default;
selecting `pyopenms` never silently falls back when the optional backend is missing.

### Stable-period assessment

The common interval duration is defined by **Default start** and **Default end**.
Each sample can supply an individual start; its end is calculated from the common
duration. **Assess stable periods** streams every complete source file in a worker
thread and reports ranked, non-overlapping candidates without changing those starts.

The stability trace is explicit and persisted:

- reference/internal-standard SIC is preferred when a suitable window exists;
- TIC is available as an analyte-independent alternative;
- analyte SIC remains available, with a visible response-selection-bias warning.

Candidate ranking reports trace robust CV, relative drift, zero fraction, response,
score, and separate analyte-SIC diagnostics. Minimum scans, candidate count,
startup/shutdown exclusions, and optional method limits are user-configurable. Limits
describe method suitability and do not silently discard candidates. An optional
score-difference setting visibly flags ambiguous rankings. The user must
enter or retain each sample start and explicitly confirm all intervals before the
calibration control is enabled.

## Project files

Analysis projects use versioned UTF-8 JSON files. A saved project contains the full
sample classification, analyte and extraction-window definitions, common processing
settings, calibration settings, schema version, UTC save timestamp, and an integrity
fingerprint. Reopening validates the complete project before it is returned to the
application; unsupported future schemas and modified or malformed payloads are
reported instead of being silently accepted.

When a reopened project references missing mzML files, a non-modal relink dialog lists
the affected samples. Replacement files can be chosen individually or matched by
exact filename in a selected folder. Relinking changes only source paths, clears stale
in-memory results, and does not save the project automatically.

## Reproducibility exports

Excel and CSV exports include samples, source paths and provenance, targets and
windows, processing and calibration settings, scan and file responses, calibration
diagnostics, quantified QC/unknown samples, warnings, software versions, and the
processing timestamp. Source SHA-256 values are streamed in a worker thread so files
are not loaded into memory. PNG exports preserve the spray-response, calibration, and
residual figures that are available in the project session. This information supports
reproducibility and review; the application does not claim regulatory compliance.
Stability-assessment candidates, trace mode, method limits, exclusions, analyte
diagnostics, effective per-sample intervals, and explicit confirmation state are also
included.

Use **File → Verify SHA-256 Now…** to explicitly stream and rehash every project
source, including excluded samples. Results distinguish verified files, byte-level
mismatches, missing files, and sources without a saved hash. Verification runs in a
worker thread, supports cancellation, and never replaces the saved reference hash.

## Development checks

```powershell
python -m ruff check .
python -m ruff format --check .
python -m pytest
```
