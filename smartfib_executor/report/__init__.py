"""Session report (v0.40.0).

    python -m smartfib_executor.report <positions.json | session folder> [--pattern STD.ely]

One self-contained HTML file per session: every position's reference image with its
pattern (and fiducial) drawn on it, coordinates, imaging conditions, a stage-layout
plot, and what the run logs say was milled.

Modules
  overlay.py      .ely -> image pixels, using elymaker's reader and calibration
  runlog.py       the per-run record written by armed mill-positions batches
  html_report.py  builds the HTML (reads files only)

Safety: the report never connects to SmartSEM, and the only file it writes is the
report. The run log is written by the batch, and every call into it is guarded so a
logging failure can never change what the microscope does.
"""
