# AGENTS.md

## Purpose

Build a small, reliable desktop program for quantitative analysis of peptides
measured by direct-infusion MS from mzML files.

## Scientific constraints

- This is direct infusion, not LC-MS.
- Acquisition time is elapsed spray time, not retention time.
- Do not implement chromatographic peak detection, integration, alignment or
  retention-time matching.
- Apply one common analysis-time interval to all files in an analysis.
- Default file-level response is the median of scan-level extracted responses.
- Keep mean and trimmed mean available as explicit alternatives.
- Do not silently change the m/z window, time interval, concentration unit,
  blank correction, regression model or dilution factor.
- Default calibration is linear with an intercept and no weighting.
- Optional weighting may include 1/x and 1/x^2.
- Do not force the regression through zero unless the user explicitly selects it.
- Do not automatically combine different peptide charge states. Let the user
  select one quantifier window or explicitly choose a sum.
- Use blank response only according to the user-selected blank correction method.
- Report residuals and back-calculated standard errors. Do not judge calibration
  validity from R-squared alone.
- Require the same processing settings for blanks, standards, QC samples and
  unknowns.
- Preserve scan-level output and analysis settings for traceability.

## Engineering constraints

- Use Python 3.11 and PySide6 Qt Widgets.
- Use pymzML as the initial mzML backend.
- Put mzML access behind a reader interface so pyOpenMS can be added later.
- Keep scientific processing independent of the GUI.
- Use typed dataclasses or Pydantic models.
- Keep the GUI responsive by processing files in a worker thread.
- Include progress, cancel support and useful error messages.
- Do not keep complete large mzML files in memory unnecessarily.
- Write automated tests for processing and regression.
- Use Ruff and pytest.
- Every task must end with:

  1. a summary of files changed;
  2. commands run;
  3. test results;
  4. known limitations;
  5. the smallest suggested next step.

- Ask before making scientifically consequential changes.
