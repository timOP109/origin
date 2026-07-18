# Windows packaging and troubleshooting

## Supported first target

Build and validate the one-folder distribution before attempting one-file mode.
PyInstaller must run on Windows; it is not a cross-compiler. The standard build uses
Python 3.11 and includes the optional pyOpenMS backend installed in the build
environment. Test data and generated exports are excluded.

```powershell
python -m pip install -e ".[dev,pyopenms]"
.\scripts\build_windows.ps1
```

Output:

```text
dist\DirectInfusionQuant\DirectInfusionQuant.exe
```

The first build deliberately retains a console window so missing modules, DLL errors,
Qt plugin diagnostics and Python tracebacks remain visible. Do not switch to
`console=False` until the clean-machine validation matrix passes.

The spec uses PyInstaller's flat one-folder contents layout because frozen pymzML
looks for its `obo` directory beside `sys.executable`. Changing back to the default
`_internal` layout requires a pymzML-specific runtime hook or equivalent tested path
adaptation.

## Packaged smoke diagnostic

Use an external centroided mzML file; never copy test data into the distribution.
Every analytical value is explicit and project-specific:

```powershell
.\dist\DirectInfusionQuant\DirectInfusionQuant.exe --packaging-smoke `
  C:\data\fixture.mzML C:\temp\diq-smoke 120 150 1009 2
```

Append `pyopenms` to exercise the optional isolated backend in the frozen build.

Success prints `PACKAGING_SMOKE_OK` and creates a project JSON, Excel workbook and PNG
spray-response plot. This checks mzML import, processing, plotting, project save/open
and Excel export through frozen modules.

## Clean account or VM validation

Copy the complete `dist\DirectInfusionQuant` folder to a Windows 10/11 VM or clean
standard-user account that has neither Python nor the source checkout. Verify:

1. `DirectInfusionQuant.exe` opens without administrator rights.
2. The Files page imports an external centroided mzML file.
3. Both the packaged smoke diagnostic and an interactive processing run complete.
4. Spray, calibration and residual plots render.
5. A project saves, closes and reopens with source paths intact or visibly missing.
6. Excel export opens in Excel or LibreOffice with all expected sheets.
7. `pymzml` works; if pyOpenMS is included, its isolated worker also processes the
   same fixture within documented parity tolerances.

Record Windows edition/build, architecture, username type, executable SHA-256, bundle
size, fixture SHA-256, elapsed time and observed warnings. A local developer-machine
run does not replace this clean-environment test.

## Common failures

### `No module named ...`

Add the dynamic import to `hiddenimports` in the spec. Prefer a package-specific
PyInstaller hook when available. Rebuild with `--clean` and retain the warning file in
`build\pyinstaller\DirectInfusionQuant\warn-DirectInfusionQuant.txt`.

### Qt platform plugin could not be initialized

Confirm `_internal\PySide6\Qt\plugins\platforms\qwindows.dll` exists. Run from
PowerShell with `QT_DEBUG_PLUGINS=1` and inspect DLL dependency errors. Do not copy Qt
plugins from a different PySide6 release.

### Missing VC runtime or Windows DLL

Install the supported Microsoft Visual C++ Redistributable on the clean machine, or
include it in a future installer according to Microsoft redistribution terms. Do not
copy arbitrary system DLLs into the bundle.

### pyOpenMS DLL load failure

The packaged app restarts itself with a private worker flag so pyOpenMS loads before
Qt. Ensure the spec collected pyOpenMS binaries from the same Python environment used
for the build. Test the pymzML default independently. Never silently substitute one
backend for another in an existing project.

### Matplotlib plot is blank or fails

Confirm Matplotlib data files and the QtAgg backend were collected. Check the build
warning file for omitted Matplotlib backends and verify `PySide6` is present.

### pymzML tries to download an OBO file or reports HTTP 404

The controlled-vocabulary package data were not collected. Keep
`collect_data_files("pymzml")` in the spec. Frozen pymzML specifically looks beside
the executable, so the spec also maps the package's `obo` directory to the bundle's
top-level `obo` directory. Rebuild with `--clean` and test with networking disabled.

### Excel export fails

Confirm `openpyxl`, pandas and their metadata are present. Run the packaged smoke
diagnostic and inspect the full console traceback.

### Antivirus quarantine or SmartScreen warning

Use reproducible builds, publish SHA-256 checksums, and code-sign release artifacts.
Do not disable endpoint protection as a packaging workaround.

## One-file gate

Do not add a one-file target until the one-folder build passes all clean-account/VM
checks repeatedly. One-file mode adds extraction behavior, slower startup and another
location for DLL/plugin failures, so it requires a separate validation record.
