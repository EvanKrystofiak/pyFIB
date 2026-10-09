"""Tools tab (v0.44.0; SEM autofocus text v0.48.0): session report, SEM autofocus, FIB autofocus.

Each button launches an existing pyFIB command through the shared subprocess worker
(output in the Log tab), exactly as typed at a prompt:

  * Make report     python -m smartfib_executor.report <positions.json> [--pattern P] [--mosaic]
                    Reads files only (never the microscope), so it runs armed or not.
                    Opens the finished report in the browser if "Open when done".
  * SEM autofocus   python -m smartfib_executor.sem_autofocus [--arm --yes] --sem-mag M
                    Not armed: read-only checks + plan. Armed: confirmed here first, then
                    --yes (no console prompt in a GUI subprocess).
  * FIB autofocus   python -m smartfib_executor.autofocus --vmin A --vmax B ... [--dry-run]
                    Not armed: --dry-run (logs the intended calls, touches nothing).
                    Armed: confirmed here first. Uses the same settings as the Rough Mill tab's
                    per-position autofocus (milling_workflow.AutofocusConfig).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import List, Optional

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from .pages import _card, _section, _title

FIB_BOUND_MARGIN_V = 400.0       # = AutofocusConfig.bound_margin_v (milling-tab autofocus)


class ToolsPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self._win = win
        self._report_path: Optional[str] = None
        self._open_url = None             # tests replace this to avoid opening a browser

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.addWidget(_title("Tools"))

        # --- Session report ----------------------------------------------------
        rep = _card(); rl = QVBoxLayout(rep)
        rl.addWidget(_section("Session report"))
        rl.addWidget(self._muted(
            "HTML report of the session in the top bar: every position's image with its "
            "pattern, coordinates, stage layout and run records. Reads files only."))
        prow = QHBoxLayout()
        prow.addWidget(QLabel("Standard pattern:"))
        self.pattern_edit = QLineEdit()
        self.pattern_edit.setPlaceholderText("default.ely (pyFIB default)")
        prow.addWidget(self.pattern_edit, 1)
        b = QPushButton("Browse…")
        b.clicked.connect(self._browse_pattern)
        prow.addWidget(b)
        rl.addLayout(prow)
        orow = QHBoxLayout()
        self.mosaic_chk = QCheckBox("Include 800x mosaic")
        self.mosaic_chk.setChecked(False)
        self.open_chk = QCheckBox("Open when done")
        self.open_chk.setChecked(True)
        orow.addWidget(self.mosaic_chk); orow.addWidget(self.open_chk); orow.addStretch(1)
        rl.addLayout(orow)
        self.report_btn = QPushButton("Make report")
        self.report_btn.setObjectName("toolButton")
        self.report_btn.clicked.connect(self._on_report)
        rl.addWidget(self.report_btn, alignment=Qt.AlignLeft)
        outer.addWidget(rep)

        # --- SEM autofocus -------------------------------------------------------
        sem = _card(); sl = QVBoxLayout(sem)
        sl.addWidget(_section("SEM autofocus"))
        sl.addWidget(self._muted(
            "pyFIB's SEM autofocus (v0.48.0, two-stage): a coarse pass at 500x, then a fine "
            "pass at the mag below, stepping the SEM working distance (4–8 mm only) and "
            "scoring sharpness; ~35-55 s; leaves the scan paused. Never touches "
            "contrast/brightness or the stage; puts the WD back if it fails. Not armed: "
            "read-only checks and the plan."))
        srow = QHBoxLayout()
        srow.addWidget(QLabel("SEM mag (x):"))
        self.sem_mag = QDoubleSpinBox()
        self.sem_mag.setRange(50, 100000); self.sem_mag.setDecimals(0)
        self.sem_mag.setValue(2000)
        srow.addWidget(self.sem_mag); srow.addStretch(1)
        sl.addLayout(srow)
        self.sem_btn = QPushButton("SEM autofocus")
        self.sem_btn.setObjectName("toolButton")
        self.sem_btn.clicked.connect(self._on_sem_af)
        sl.addWidget(self.sem_btn, alignment=Qt.AlignLeft)
        outer.addWidget(sem)

        # --- FIB autofocus -------------------------------------------------------
        fib = _card(); fl = QVBoxLayout(fib)
        fl.addWidget(_section("FIB autofocus"))
        fl.addWidget(self._muted(
            "Sweeps the FIB objective voltage around its current value, inside the "
            "bounds below, and leaves the best focus (same settings as the milling tabs' "
            "autofocus). Not armed: a dry run that only logs the steps."))
        frow = QHBoxLayout()
        frow.addWidget(QLabel("Objective bounds (V):"))
        self.vmin = QDoubleSpinBox(); self.vmax = QDoubleSpinBox()
        for sp in (self.vmin, self.vmax):
            sp.setRange(0, 40000); sp.setDecimals(0); sp.setSingleStep(50)
        frow.addWidget(self.vmin); frow.addWidget(QLabel("to")); frow.addWidget(self.vmax)
        self.fill_btn = QPushButton("From last position")
        self.fill_btn.setToolTip("Last captured position's objective voltage ± "
                                 f"{FIB_BOUND_MARGIN_V:.0f} V")
        self.fill_btn.clicked.connect(self.fill_bounds)
        frow.addWidget(self.fill_btn)
        frow.addSpacing(16)
        frow.addWidget(QLabel("Sweep ± (V):"))
        self.half_range = QDoubleSpinBox()
        self.half_range.setRange(10, 1000); self.half_range.setDecimals(0)
        self.half_range.setValue(150)
        frow.addWidget(self.half_range)
        frow.addWidget(QLabel("FIB mag (x):"))
        self.fib_mag = QDoubleSpinBox()
        self.fib_mag.setRange(50, 100000); self.fib_mag.setDecimals(0)
        self.fib_mag.setValue(2000)
        frow.addWidget(self.fib_mag)
        frow.addStretch(1)
        fl.addLayout(frow)
        self.fib_btn = QPushButton("FIB autofocus")
        self.fib_btn.setObjectName("toolButton")
        self.fib_btn.clicked.connect(self._on_fib_af)
        fl.addWidget(self.fib_btn, alignment=Qt.AlignLeft)
        outer.addWidget(fib)

        outer.addStretch(1)

    # ------------------------------------------------------------------ helpers
    def _muted(self, text: str) -> QLabel:
        lab = QLabel(text); lab.setWordWrap(True)
        lab.setStyleSheet("color:#9aa3b2;")
        return lab

    def _browse_pattern(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select pattern",
                                              self.pattern_edit.text() or "",
                                              "SmartFIB layout (*.ely)")
        if path:
            self.pattern_edit.setText(path)

    def _session_dir(self) -> Optional[Path]:
        pf = self._win.positions_file()
        return Path(pf).parent if pf else None

    def on_show(self) -> None:
        if self.vmin.value() == 0 and self.vmax.value() == 0:
            self.fill_bounds(quiet=True)

    def fill_bounds(self, quiet: bool = False) -> bool:
        """Bounds = last captured position's objective voltage +/- 400 V."""
        pf = self._win.positions_file()
        try:
            from ..positions import PositionDatabase
            db = PositionDatabase.load(Path(pf))
            recs = [r for r in db.positions.values() if r.fib_objective_potential_v]
            last = max(recs, key=lambda r: r.captured_unix_s or 0.0)
        except Exception:
            if not quiet:
                self._win.status("No captured position to take the objective voltage from.")
            return False
        v = float(last.fib_objective_potential_v)
        self.vmin.setValue(max(0.0, v - FIB_BOUND_MARGIN_V))
        self.vmax.setValue(v + FIB_BOUND_MARGIN_V)
        if not quiet:
            self._win.status(f"Bounds from {last.name} ({v:.0f} V) ± {FIB_BOUND_MARGIN_V:.0f} V.")
        return True

    # ------------------------------------------------------------------ report
    def report_args(self) -> Optional[List[str]]:
        pf = self._win.positions_file()
        if not pf or not Path(pf).exists():
            return None
        args = [pf]
        pat = self.pattern_edit.text().strip()
        if pat:
            args += ["--pattern", pat]
        if self.mosaic_chk.isChecked():
            args += ["--mosaic"]
        return args

    def _on_report(self) -> None:
        args = self.report_args()
        if args is None:
            self._win.status("No positions file yet: capture a position or load a session.")
            return
        self._report_path = None
        self._win.run_module("smartfib_executor.report", args,
                             on_line=self._on_report_line, on_done=self._on_report_done)

    def _on_report_line(self, line: str) -> None:
        t = line.strip()
        if t.startswith("Report: ") and ".html" in t:
            self._report_path = t[len("Report: "):t.index(".html") + len(".html")]

    def _on_report_done(self, rc: int) -> None:
        if rc != 0 or not self._report_path:
            return
        self._win.status(f"Report written: {self._report_path}")
        if self.open_chk.isChecked():
            opener = self._open_url or _open_in_browser
            try:
                opener(self._report_path)
            except Exception as e:  # noqa: BLE001
                self._win.status(f"Report written but could not be opened: {e}")

    # ------------------------------------------------------------------ SEM AF
    def sem_args(self, armed: bool) -> List[str]:
        args = ["--sem-mag", f"{self.sem_mag.value():g}"]
        sd = self._session_dir()
        if armed:
            args = ["--arm", "--yes"] + args
            if sd is not None:
                args += ["--out", str(sd / time.strftime("sem_autofocus_%Y%m%d_%H%M%S"))]
        return args

    def _on_sem_af(self) -> None:
        armed = self._win.is_armed()
        if armed and not self._win.confirm(
                f"Run SEM autofocus now at {self.sem_mag.value():g}x?\n\nIt steps the SEM "
                "working distance (4–8 mm only) and images with the SEM: first a coarse "
                "pass at 500x, then the fine pass. The mag, scan speed and scan box are put "
                "back and the scan is left paused. Contrast, brightness and the stage are "
                "never touched; the WD is put back if it fails."):
            return
        self._win.run_module("smartfib_executor.sem_autofocus", self.sem_args(armed))

    # ------------------------------------------------------------------ FIB AF
    def fib_args(self, armed: bool) -> Optional[List[str]]:
        vmin, vmax = float(self.vmin.value()), float(self.vmax.value())
        if not (0 < vmin < vmax) or (vmax - vmin) > 2 * 1000.0:
            return None
        from ..milling_workflow import AutofocusConfig
        c = AutofocusConfig()
        args = ["--vmin", f"{vmin:g}", "--vmax", f"{vmax:g}",
                "--half-range", f"{self.half_range.value():g}",
                "--coarse-points", str(c.coarse_points), "--metric", c.metric,
                "--fib-mag", f"{self.fib_mag.value():g}",
                "--beam-shift-x", f"{c.beam_shift_x_m:g}", "--raster-side", c.raster_side,
                "--scan-rate", str(c.imaging_scan_rate if c.imaging_scan_rate is not None else -1),
                "--scan-rate-method", c.scan_rate_method,
                "--settle", f"{c.settle_s:g}", "--frame-wait", f"{c.frame_wait_s:g}"]
        if not armed:
            args.append("--dry-run")
        return args

    def _on_fib_af(self) -> None:
        armed = self._win.is_armed()
        args = self.fib_args(armed)
        if args is None:
            self._win.status("Set the objective bounds first (min < max, span ≤ 2000 V); "
                             "'From last position' fills them.")
            return
        if armed and not self._win.confirm(
                f"Run FIB autofocus now?\n\nIt sweeps the FIB objective voltage ± "
                f"{self.half_range.value():g} V around its current value (never outside "
                f"{self.vmin.value():g}–{self.vmax.value():g} V) at {self.fib_mag.value():g}x, "
                f"imaging with the FIB beam parked to one side, and leaves the best focus."):
            return
        self._win.run_module("smartfib_executor.autofocus", args)


def _open_in_browser(path: str) -> None:
    from qtpy.QtCore import QUrl
    from qtpy.QtGui import QDesktopServices
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))
