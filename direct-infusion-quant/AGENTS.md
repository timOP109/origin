You are developing a scientific desktop application for quantifying peptides
from direct-infusion mass-spectrometry mzML files.

Before changing code:
1. Read README.md and all documents under docs/.
2. Inspect the implementation and tests.
3. Explain the proposed change and its scientific consequences.
4. Make the smallest reviewable change.
5. Run relevant tests and report the exact commands and results.

Scientific invariants:
- This is direct infusion, not chromatography.
- Acquisition time is elapsed spray time, not retention time.
- Apply one common acquisition-time duration to every file in a batch; allow
  each file's explicitly selected start position to differ.
- Prefer an explicitly selected internal-standard/reference SIC or TIC for
  interval assessment; label analyte-SIC response-selection bias.
- Never apply a recommended interval automatically. Require explicit interval
  confirmation before calibration.
- Apply one common m/z window to every file in a batch.
- Do not silently alter concentration units.
- Do not silently change the response statistic, blank handling, regression
  model, weighting, dilution factor, m/z window or time interval.
- Preserve scan-level extracted responses.
- Keep raw and blank-corrected responses.
- Apply dilution factors only after calculating the concentration of the
  sprayed solution.
- Do not use R-squared alone to determine whether a calibration is valid.
- Display residuals, back-calculated standards, blank response, carryover
  warnings and possible saturation.
- Never automatically remove outliers.
- Do not silently skip unreadable files.

Software requirements:
- Python 3.11.
- PySide6 Widgets, not QML.
- pyqtgraph for interactive plots.
- pymzML as the initial mzML reader.
- pyOpenMS may later be added behind the same reader interface.
- The calculation engine must contain no Qt imports.
- Use type hints, pathlib and typed data models.
- Use pytest and pytest-qt.
- Use Ruff for linting and formatting.
- Keep numerical methods deterministic.
- The application must not require internet access.

Required checks:
- python -m pytest
- python -m ruff check .
- python -m ruff format --check .

Do not make scientifically consequential decisions on behalf of the user.
Expose them in the GUI and save them in the project file.
