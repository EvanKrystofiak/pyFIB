"""gisdep GUI -- elymaker dark theme; runs against the mock, or as a read-only
instrument monitor.

Modes:
  * mock (default): full interactive -- simulate state, run the real deposition against
    the mock, WD check, Z nudges.
  * instrument monitor (--instrument): connects read-only to the microscope and displays
    live chamber state; all actuation controls are disabled.
  * monitor preview (--monitor): the read-only monitor UI on the mock, for offline
    testing of the monitor look.

Requires: pip install PyQt5 qtpy

Qt is imported at module import time, so this module is imported lazily (only when the
GUI launches) -- the rest of gisdep does not depend on Qt.
"""

from __future__ import annotations

import logging
import sys
import threading

from qtpy.QtCore import Qt, QThread, QTimer, QMutex, QObject, QWaitCondition, Signal
from qtpy.QtGui import QFont, QPixmap
from qtpy.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gisdep.api.mock import MockSmartSEM
from gisdep.clock import RealClock
from gisdep.gis import read_all
from gisdep.gis.deposition import deposit, DWELL_MIN_S, DWELL_MAX_S
from gisdep.gis.state import (
    NEEDLE_INSERTED,
    NEEDLE_PARKED,
    VALVE_CLOSED,
    VALVE_OPEN,
)
from gisdep.gui import fib_dark_theme as theme
from gisdep.gui.state_images import active_port_state, asset_path, START, BLOCKED

# theme colours
BG = theme.BG
PANEL = theme.PANEL
INPUT_BG = theme.INPUT_BG
BORDER = theme.BORDER
TEXT = theme.TEXT
MUTED = theme.MUTED
ACCENT = theme.ACCENT
ACCENT_BRIGHT = theme.ACCENT_BRIGHT
ACCENT_TEXT = theme.ACCENT_TEXT

# semantic status colours (white text pills), tuned to read on the dark theme
GREEN = "#16a34a"
BLUE = "#2563eb"
AMBER = "#d97706"
RED = "#dc2626"
NEUTRAL = "#475569"


def _pill_css(color):
    return (f"background:{color}; color:white; border-radius:11px; "
            f"padding:3px 12px; font-weight:700;")


def _accent_btn_css():
    return (
        f"QPushButton{{background:{ACCENT}; color:{ACCENT_TEXT}; "
        f"border:1px solid {ACCENT_BRIGHT}; border-radius:8px; padding:8px 12px; "
        f"font-weight:700;}}"
        f"QPushButton:hover{{background:{ACCENT_BRIGHT};}}"
        f"QPushButton:disabled{{background:{BORDER}; color:{MUTED}; "
        f"border-color:{BORDER};}}"
    )


class _EmitHandler(logging.Handler):
    def __init__(self, emit_fn):
        super().__init__()
        self._emit_fn = emit_fn

    def emit(self, record):
        try:
            self._emit_fn(self.format(record))
        except Exception:
            pass


class TaskWorker(QThread):
    """Runs any callable off the UI thread; forwards gisdep logs; emits ('ok'|'err', x)."""
    logline = Signal(str)
    done = Signal(object)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        handler = _EmitHandler(self.logline.emit)
        handler.setFormatter(logging.Formatter("%(message)s"))
        root = logging.getLogger("gisdep")
        prev = root.level
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        try:
            self.done.emit(("ok", self.fn()))
        except Exception as exc:
            self.done.emit(("err", exc))
        finally:
            root.removeHandler(handler)
            root.setLevel(prev)


class ConfirmBridge(QObject):
    """Lets a worker thread ask the GUI thread for per-step confirmation and block for
    the answer. Used for live (armed instrument) actuation so each step is confirmed."""
    request = Signal(str)

    def __init__(self):
        super().__init__()
        self._mutex = QMutex()
        self._cond = QWaitCondition()
        self._answer = None

    def ask(self, step):  # called from the WORKER thread; blocks until answered
        self._mutex.lock()
        self._answer = None
        self.request.emit(step)
        while self._answer is None:
            self._cond.wait(self._mutex)
        ans = self._answer
        self._mutex.unlock()
        return bool(ans)

    def answer(self, ok):  # called from the MAIN thread (dialog result)
        self._mutex.lock()
        self._answer = bool(ok)
        self._cond.wakeAll()
        self._mutex.unlock()


class GisdepWindow(QMainWindow):
    def __init__(self, api=None, monitor=False):
        super().__init__()
        self.api = api or MockSmartSEM()
        self.api.initialise()
        self._is_mock = isinstance(self.api, MockSmartSEM)
        # Armed unless explicitly launched in monitor (read-only) mode. --instrument is
        # ARMED by default; pass monitor=True (--monitor) for a read-only view.
        self._armed = not monitor
        self._pixmaps = {}
        self._last_stable_file = None
        self._worker = None
        self._busy = []
        self._on_done_cb = None
        self._confirm_bridge = ConfirmBridge()
        self._confirm_bridge.request.connect(self._on_confirm_request)
        self._abort_event = threading.Event()
        self._last_run_path = None
        from gisdep.config import load_config
        self._cfg = load_config()
        # image override state: "start" just after load, "blocked" when insertion is
        # refused, else None (readback-driven).
        self._display_state = "start"

        self.setWindowTitle("gisdep")
        self._build_ui()
        self._refresh_armed_state()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(150)
        self._refresh()

    @property
    def _read_only(self):
        return not self._armed

    # --- layout -------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._header())

        body = QWidget()
        outer.addWidget(body, 1)
        h = QHBoxLayout(body)
        h.setContentsMargins(16, 16, 16, 16)
        h.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(14)
        left.addWidget(self._image_card(), 1)
        left.addWidget(self._status_card())
        h.addLayout(left, 3)
        h.addWidget(self._right_panel(), 2)

    def _header(self):
        f = QFrame()
        f.setObjectName("header")
        f.setStyleSheet(
            f"QFrame#header{{background:{PANEL}; border-bottom:1px solid {BORDER};}}"
        )
        lay = QHBoxLayout(f)
        lay.setContentsMargins(18, 12, 18, 12)
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        t = QLabel("gisdep")
        t.setStyleSheet(f"font-size:18px; font-weight:800; color:{TEXT};")
        s = QLabel("cryo GIS deposition — chamber view")
        s.setStyleSheet(f"color:{MUTED}; font-size:12px;")
        title_box.addWidget(t)
        title_box.addWidget(s)
        lay.addLayout(title_box)
        lay.addStretch(1)
        # Emergency STOP: aborts the running operation and runs safe teardown.
        self.stop_btn = QPushButton("■ STOP")
        self.stop_btn.setToolTip("Abort the running operation and safely tear down "
                                 "(close valve, retract needle).")
        self.stop_btn.setStyleSheet(
            f"QPushButton{{background:{RED}; color:white; border:none; border-radius:9px; "
            f"padding:7px 16px; font-weight:800; font-size:13px;}}"
            f"QPushButton:hover{{background:#b31d1d;}}"
            f"QPushButton:disabled{{background:{BORDER}; color:{MUTED};}}"
        )
        self.stop_btn.clicked.connect(self._stop)
        self.stop_btn.setEnabled(False)
        lay.addWidget(self.stop_btn)
        # Prominent ARM toggle (instrument only; the mock is always live).
        self.arm_btn = QPushButton("ARM LIVE ACTUATION")
        self.arm_btn.setCheckable(True)
        self.arm_btn.setToolTip(
            "Instrument only. Off = read-only monitor. On = enable live actuation\n"
            "(every step still asks for confirmation)."
        )
        self.arm_btn.toggled.connect(self._on_arm_toggled)
        lay.addWidget(self.arm_btn)
        self.badge = QLabel("")
        self.badge.setStyleSheet(
            f"background:{INPUT_BG}; color:{ACCENT}; border:1px solid {BORDER}; "
            f"border-radius:11px; padding:3px 12px; font-weight:700; font-size:11px;"
        )
        lay.addWidget(self.badge)
        return f

    def _update_arm_button(self):
        if self._is_mock:
            self.arm_btn.setVisible(False)
            return
        self.arm_btn.setVisible(True)
        self.arm_btn.blockSignals(True)
        self.arm_btn.setChecked(self._armed)
        self.arm_btn.blockSignals(False)
        if self._armed:
            self.arm_btn.setText("●  ARMED — LIVE  (click to disarm)")
            self.arm_btn.setStyleSheet(
                f"QPushButton{{background:{RED}; color:white; border:2px solid #ff7a7a; "
                f"border-radius:9px; padding:7px 16px; font-weight:800; font-size:13px;}}"
                f"QPushButton:hover{{background:#b31d1d;}}"
            )
        else:
            self.arm_btn.setText("🔒  ARM LIVE ACTUATION")
            self.arm_btn.setStyleSheet(
                f"QPushButton{{background:{AMBER}; color:white; border:2px solid #f0a955; "
                f"border-radius:9px; padding:7px 16px; font-weight:800; font-size:13px;}}"
                f"QPushButton:hover{{background:#b9720a;}}"
            )

    def _update_badge(self):
        self._update_arm_button()
        if self._is_mock:
            txt, color = "MOCK · offline", ACCENT
        elif self._armed:
            txt, color = "INSTRUMENT · ARMED (live)", RED
        else:
            txt, color = "INSTRUMENT · MONITOR (read-only)", ACCENT
        self.badge.setText(txt)
        self.badge.setStyleSheet(
            f"background:{INPUT_BG}; color:{color}; border:1px solid {BORDER}; "
            f"border-radius:11px; padding:3px 12px; font-weight:700; font-size:11px;"
        )
        if self._is_mock:
            txt = "MOCK · offline"
        elif self._armed:
            txt = "INSTRUMENT · ARMED (live)"
        else:
            txt = "INSTRUMENT · MONITOR (read-only)"
        color = RED if (self._armed and not self._is_mock) else ACCENT
        self.badge.setText(txt)
        self.badge.setStyleSheet(
            f"background:{INPUT_BG}; color:{color}; border:1px solid {BORDER}; "
            f"border-radius:11px; padding:3px 12px; font-weight:700; font-size:11px;"
        )

    def _image_card(self):
        card = QFrame()
        card.setObjectName("imageCard")
        card.setStyleSheet(
            f"QFrame#imageCard{{background:{PANEL}; border:1px solid {BORDER}; "
            f"border-radius:14px;}}"
        )
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 14, 14, 14)
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumSize(360, 320)
        self.image_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # keep the line-art on a white canvas even in the dark theme
        self.image_label.setStyleSheet("background:#ffffff; border-radius:8px;")
        lay.addWidget(self.image_label, 1)
        self.caption = QLabel("…")
        self.caption.setAlignment(Qt.AlignCenter)
        self.caption.setStyleSheet(_pill_css(NEUTRAL))
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.caption)
        row.addStretch(1)
        lay.addLayout(row)
        return card

    def _right_panel(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        scroll.setWidget(inner)
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)
        v.addWidget(self._full_run_card())
        v.addWidget(self._cryo_card())
        v.addWidget(self._workflow_card())
        v.addWidget(self._deposition_card())
        if self._is_mock:
            v.addWidget(self._simulate_card())
        else:
            self._sim_box = None  # simulate is mock-only; not shown on the instrument
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(150)
        logfont = QFont("Monospace")
        logfont.setStyleHint(QFont.TypeWriter)
        logfont.setPointSize(10)
        self.log.setFont(logfont)
        v.addWidget(self.log, 1)
        return scroll

    def _status_card(self):
        box = QGroupBox("LIVE STATE")
        g = QGridLayout(box)
        g.setVerticalSpacing(8)
        g.setHorizontalSpacing(10)
        self._val = {}

        def pill(r, c, label, key):
            g.addWidget(QLabel(label), r, c)
            p = QLabel("…")
            p.setStyleSheet(_pill_css(NEUTRAL))
            g.addWidget(p, r, c + 1, alignment=Qt.AlignLeft)
            self._val[key] = p

        def text(r, c, label, key):
            g.addWidget(QLabel(label), r, c)
            v = QLabel("…")
            v.setStyleSheet(f"font-weight:700; color:{TEXT};")
            g.addWidget(v, r, c + 1, alignment=Qt.AlignLeft)
            self._val[key] = v

        pill(0, 0, "GIS1 needle", "n1")
        pill(0, 2, "GIS2 needle", "n2")
        pill(1, 0, "GIS1 valve", "v1")
        pill(1, 2, "GIS2 valve", "v2")
        text(2, 0, "Stage Z", "z")
        text(2, 2, "Working dist.", "wd")
        g.setColumnStretch(1, 1)
        g.setColumnStretch(3, 1)
        return box

    def _full_run_card(self):
        box = QGroupBox("FULL RUN — cryo → focus+WD → deposition")
        self._full_box = box
        g = QGridLayout(box)
        g.addWidget(QLabel("Grid"), 0, 0)
        self.fr_grid = QComboBox()
        self.fr_grid.addItems(["Grid 1", "Grid 2"])
        g.addWidget(self.fr_grid, 0, 1)
        g.addWidget(QLabel("Port"), 1, 0)
        self.fr_port = QComboBox()
        self.fr_port.addItems(["GIS1", "GIS2"])
        g.addWidget(self.fr_port, 1, 1)
        g.addWidget(QLabel("Dwell (s)"), 2, 0)
        self.fr_dwell = QSpinBox()
        self.fr_dwell.setRange(int(DWELL_MIN_S), int(DWELL_MAX_S))
        self.fr_dwell.setValue(int(self._cfg.dwell_default_s))
        g.addWidget(self.fr_dwell, 2, 1)
        self.fr_btn = QPushButton("Run full sequence")
        self.fr_btn.setStyleSheet(_accent_btn_css())
        self.fr_btn.clicked.connect(self._run_full)
        g.addWidget(self.fr_btn, 3, 0, 1, 2)
        note = QLabel(
            "cryo position (via CLEM SAFE) → autofocus → step Z to 5.5 mm → autofocus "
            "fine to confirm → deposition. Each step confirmed when armed."
        )
        note.setStyleSheet(f"color:{MUTED}; font-size:11px;")
        note.setWordWrap(True)
        g.addWidget(note, 4, 0, 1, 2)
        return box

    def _cryo_card(self):
        box = QGroupBox("STEP 1 — CRYO POSITIONING (via CLEM SAFE)")
        self._cryo_box = box
        g = QGridLayout(box)
        self.grid1_btn = QPushButton("Go to Grid 1")
        self.grid1_btn.setStyleSheet(_accent_btn_css())
        self.grid1_btn.clicked.connect(lambda: self._go_to_grid("GRID1"))
        self.grid2_btn = QPushButton("Go to Grid 2")
        self.grid2_btn.setStyleSheet(_accent_btn_css())
        self.grid2_btn.clicked.connect(lambda: self._go_to_grid("GRID2"))
        g.addWidget(self.grid1_btn, 0, 0)
        g.addWidget(self.grid2_btn, 0, 1)
        note = QLabel(
            "To CLEM SAFE (Z first, then X,Y,T,R) → verify → to grid (X,Y,T,R first, "
            "then Z). M never moves (recorded at 12.99 mm — verify before live use)."
        )
        note.setStyleSheet(f"color:{MUTED}; font-size:11px;")
        note.setWordWrap(True)
        g.addWidget(note, 1, 0, 1, 2)
        return box

    def _workflow_card(self):
        box = QGroupBox("STEP 2 — WD CHECK + STAGE")
        g = QGridLayout(box)
        g.setVerticalSpacing(8)

        self.autofocus_check = QCheckBox("with autofocus")
        self.autofocus_check.setToolTip(
            "Off: read the current working distance and judge it (read-only, no actuation).\n"
            "On: run SEM coarse+fine autofocus first (which sets the working distance),\n"
            "then judge the result. On the instrument this actuates and takes ~20–30 s.\n"
            "(In the mock, autofocus runs but does not change WD.)"
        )
        g.addWidget(self.autofocus_check, 0, 0, 1, 2)

        self.wd_btn = QPushButton("Check working distance")
        self.wd_btn.setStyleSheet(_accent_btn_css())
        self.wd_btn.clicked.connect(self._check_wd)
        g.addWidget(self.wd_btn, 1, 0, 1, 2)

        g.addWidget(QLabel("Result"), 2, 0)
        self.wd_verdict = QLabel("not checked")
        self.wd_verdict.setStyleSheet(_pill_css(NEUTRAL))
        g.addWidget(self.wd_verdict, 2, 1, alignment=Qt.AlignLeft)

        sep = QLabel("Possible stage movement (Z)")
        sep.setStyleSheet(f"color:{MUTED}; font-weight:700; margin-top:4px;")
        g.addWidget(sep, 3, 0, 1, 2)

        g.addWidget(QLabel("Step (µm)"), 4, 0)
        self.zstep_spin = QSpinBox()
        self.zstep_spin.setRange(1, 1000)
        self.zstep_spin.setValue(20)
        g.addWidget(self.zstep_spin, 4, 1)

        self.zminus_btn = QPushButton("Move −Z")
        self.zminus_btn.clicked.connect(lambda: self._nudge_z(-1))
        self.zplus_btn = QPushButton("Move +Z")
        self.zplus_btn.clicked.connect(lambda: self._nudge_z(+1))
        g.addWidget(self.zminus_btn, 5, 0)
        g.addWidget(self.zplus_btn, 5, 1)

        self.gotowd_btn = QPushButton("→ 5.5 mm WD (one step)")
        self.gotowd_btn.setToolTip(
            "One bounded Z step toward WD = 5.5 mm, using the confirmed dWD/dZ = −1.\n"
            "Clamped to the max move and the Z limits; run again to continue if capped."
        )
        self.gotowd_btn.clicked.connect(self._go_to_wd)
        g.addWidget(self.gotowd_btn, 6, 0, 1, 2)

        note = QLabel("target 5.5 mm · Z limits [20, 40] mm · needles must be parked")
        note.setStyleSheet(f"color:{MUTED}; font-size:11px;")
        note.setWordWrap(True)
        g.addWidget(note, 7, 0, 1, 2)
        return box

    def _deposition_card(self):
        box = QGroupBox("RUN DEPOSITION")
        self._depo_box = box
        g = QGridLayout(box)
        g.addWidget(QLabel("Port"), 0, 0)
        self.port_combo = QComboBox()
        self.port_combo.addItems(["GIS1", "GIS2"])
        g.addWidget(self.port_combo, 0, 1)
        g.addWidget(QLabel("Dwell (s)"), 1, 0)
        self.dwell_spin = QSpinBox()
        self.dwell_spin.setRange(int(DWELL_MIN_S), int(DWELL_MAX_S))
        self.dwell_spin.setValue(int(self._cfg.dwell_default_s))
        g.addWidget(self.dwell_spin, 1, 1)
        self.run_btn = QPushButton("Run deposition")
        self.run_btn.setStyleSheet(_accent_btn_css())
        self.run_btn.clicked.connect(self._run_deposition)
        g.addWidget(self.run_btn, 2, 0, 1, 2)
        return box

    def _simulate_card(self):
        box = QGroupBox("SIMULATE (MOCK ONLY)")
        self._sim_box = box
        g = QGridLayout(box)
        defs = [
            ("GIS1 insert", 1, "needle", NEEDLE_INSERTED),
            ("GIS1 retract", 1, "needle", NEEDLE_PARKED),
            ("GIS1 open", 1, "valve", VALVE_OPEN),
            ("GIS1 close", 1, "valve", VALVE_CLOSED),
            ("GIS2 insert", 2, "needle", NEEDLE_INSERTED),
            ("GIS2 retract", 2, "needle", NEEDLE_PARKED),
            ("GIS2 open", 2, "valve", VALVE_OPEN),
            ("GIS2 close", 2, "valve", VALVE_CLOSED),
        ]
        for i, (label, port, field, value) in enumerate(defs):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, p=port, f=field, v=value: self._simulate(p, f, v))
            g.addWidget(b, i // 2, i % 2)
        reset = QPushButton("Reset to safe")
        reset.clicked.connect(self._reset_safe)
        g.addWidget(reset, 4, 0, 1, 2)
        return box

    def _refresh_armed_state(self):
        """Enable/disable all actuation controls based on the armed flag."""
        actuate = self._armed
        boxes = [self._full_box, self._cryo_box, self._depo_box]
        if self._sim_box is not None:
            boxes.append(self._sim_box)
        for box in boxes:
            box.setEnabled(actuate)
        for w in (self.zminus_btn, self.zplus_btn, self.zstep_spin, self.gotowd_btn,
                  self.autofocus_check):
            w.setEnabled(actuate)
        if not actuate:
            self.autofocus_check.setChecked(False)
        if self._sim_box is not None:
            self._sim_box.setEnabled(actuate and self._is_mock)  # simulate is mock-only
        self.wd_btn.setText(
            "Check working distance (read-only)" if not actuate
            else "Check working distance"
        )
        self._update_badge()

    def _on_arm_toggled(self, checked):
        if checked and not self._is_mock:
            ok = QMessageBox.warning(
                self, "Arm live actuation",
                "This enables LIVE actuation of the microscope. Each step will still ask "
                "for confirmation.\n\nArm now?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if ok != QMessageBox.Yes:
                self.arm_btn.blockSignals(True)
                self.arm_btn.setChecked(False)
                self.arm_btn.blockSignals(False)
                self._update_arm_button()
                return
        self._armed = checked or self._is_mock
        self._refresh_armed_state()
        self._append_log("[armed] live actuation ENABLED" if self._armed
                         else "[disarmed] read-only monitor")

    def _on_confirm_request(self, step):
        # runs on the MAIN thread; the worker is blocked waiting for the answer
        ok = QMessageBox.question(
            self, "Confirm step", f"Proceed to:\n\n{step}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        self._confirm_bridge.answer(ok == QMessageBox.Yes)

    def _confirm_cb(self):
        """The confirm callback to pass to actuation: dialogs on a live instrument,
        auto-approve on the mock (smooth, testable)."""
        if self._is_mock:
            return None
        return self._confirm_bridge.ask

    # --- task plumbing ------------------------------------------------------
    def _run_task(self, fn, on_done, busy=()):
        if self._worker is not None and self._worker.isRunning():
            return
        self._abort_event.clear()
        self._display_state = None  # leave the start/blocked screen once we act
        self._busy = list(busy)
        for w in self._busy:
            w.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._on_done_cb = on_done
        self._worker = TaskWorker(fn)
        self._worker.logline.connect(self._append_log)
        self._worker.done.connect(self._task_finished)
        self._worker.start()

    def _task_finished(self, result):
        status, payload = result
        for w in self._busy:
            w.setEnabled(True)
        self.stop_btn.setEnabled(False)
        if self._last_run_path:
            self._append_log(f"run logged: {self._last_run_path}")
            self._last_run_path = None
        if status == "err":
            self._append_log(f"HALTED: {type(payload).__name__}: {payload}")
            # a refused/blocked needle insertion -> show the "not possible" image
            if type(payload).__name__ in ("NotSafeToStartError", "MutualExclusionError"):
                self._display_state = "blocked"
        if self._on_done_cb:
            self._on_done_cb(status, payload)

    def _stop(self):
        self._abort_event.set()
        self._append_log("■ STOP requested — aborting and tearing down safely…")

    # --- actions ------------------------------------------------------------
    def _precheck_cb(self):
        """Precheck confirmations (e.g. tilt override): auto-approve on the mock, a
        dialog on a live instrument."""
        if self._is_mock:
            return lambda step: True
        return self._confirm_bridge.ask

    def _stage_cfg(self):
        from gisdep.stage.mover import StageMoveConfig
        return StageMoveConfig(z_min_m=self._cfg.z_min_m, z_max_m=self._cfg.z_max_m)

    def _check_wd(self):
        from gisdep.focus.autofocus import autofocus, AutoFocusConfig
        from gisdep.focus.wd_gate import evaluate_wd
        from gisdep.prereq.checks import check_stage_tilt
        from gisdep.prereq.tilt import CRYO_GIS_TILT
        af = self.autofocus_check.isChecked()
        af_cfg = (AutoFocusConfig(sustained_idle_s=1.0, startup_timeout_s=10.0)
                  if self._is_mock else AutoFocusConfig())
        precheck = self._precheck_cb()
        self._append_log("--- checking working distance"
                         + (" (with autofocus)" if af else " (read-only)") + " ---")

        def fn():
            # tilt gate (soft): if not in the cryo window, confirm to proceed or cancel
            t = check_stage_tilt(self.api, CRYO_GIS_TILT)
            if not t.ok and (precheck is None or not precheck(f"{t.detail} — check WD anyway?")):
                raise RuntimeError(f"tilt not confirmed: {t.detail}")
            if af:
                autofocus(self.api, clock=RealClock(), cfg=af_cfg)
            return evaluate_wd(self.api.get_analogue("AP_WD"))
        busy = [self.wd_btn]
        if self._armed:
            busy += [self.zminus_btn, self.zplus_btn]
        self._run_task(fn, self._wd_done, busy=busy)

    def _wd_done(self, status, payload):
        if status != "ok":
            self.wd_verdict.setText("check failed")
            self.wd_verdict.setStyleSheet(_pill_css(RED))
            return
        wd = payload  # WDResult
        colors = {"ideal": GREEN, "safe_suboptimal": AMBER, "too_close": RED}
        self.wd_verdict.setText(wd.verdict.value.replace("_", " "))
        self.wd_verdict.setStyleSheet(_pill_css(colors.get(wd.verdict.value, NEUTRAL)))
        self._append_log(wd.message())

    def _nudge_z(self, sign):
        from gisdep.stage.mover import StageMover
        delta = sign * self.zstep_spin.value() * 1e-6
        self._append_log(f"--- moving Z by {delta * 1e6:+.0f} µm ---")

        def fn():
            mv = StageMover(self.api, clock=RealClock(), cfg=self._stage_cfg()); mv.abort = self._abort_event
            return mv.move_axis_relative("z", delta, confirm=self._confirm_cb())
        self._run_task(fn, self._nudge_done, busy=[self.wd_btn, self.zminus_btn, self.zplus_btn])

    def _nudge_done(self, status, payload):
        if status == "ok":
            r = payload
            self._append_log(
                f"Z moved {r.measured_delta * 1e6:+.2f} µm -> {r.after.z_m * 1e3:.4f} mm"
            )

    def _go_to_grid(self, grid_name):
        from gisdep.stage.cryo import CryoPositioner
        self._append_log(f"--- STEP 1: cryo positioning -> {grid_name} (via CLEM SAFE) ---")

        def fn():
            return CryoPositioner(self.api, clock=RealClock()).go_to_grid(grid_name, confirm=self._confirm_cb(), abort=self._abort_event)
        self._run_task(
            fn, self._grid_done,
            busy=[self.grid1_btn, self.grid2_btn, self.wd_btn,
                  self.zminus_btn, self.zplus_btn, self.gotowd_btn],
        )

    def _grid_done(self, status, payload):
        if status == "ok":
            rep = payload
            for s in rep.steps:
                self._append_log("  " + s)
            if not rep.m_matches:
                self._append_log(
                    f"  ⚠ M {rep.m_current_m*1e3:.4f} mm ≠ recorded "
                    f"{rep.m_ref_m*1e3:.4f} mm (M not moved)"
                )

    def _go_to_wd(self):
        from gisdep.stage.mover import StageMover
        target = self._cfg.wd_target_m  # deposition target WD (from config)
        self._append_log("--- one Z step toward WD 5.5 mm ---")

        def fn():
            mv = StageMover(self.api, clock=RealClock(), cfg=self._stage_cfg()); mv.abort = self._abort_event
            return mv.move_to_wd(target, confirm=self._confirm_cb())
        self._run_task(
            fn, self._go_to_wd_done,
            busy=[self.wd_btn, self.zminus_btn, self.zplus_btn, self.gotowd_btn],
        )

    def _go_to_wd_done(self, status, payload):
        if status == "ok":
            self._append_log(payload.message())

    def _run_deposition(self):
        port = 1 if self.port_combo.currentIndex() == 0 else 2
        dwell = float(self.dwell_spin.value())
        self._append_log(f"--- GIS{port} deposition, dwell {dwell:g}s ---")

        # No per-step confirmation: the button press is the go, and the dwell must be
        # exactly `dwell` (never waiting on a click).
        def fn():
            from gisdep.runlog import RunLog
            with RunLog("deposition", {"port": port, "dwell": dwell}, api=self.api) as rl:
                res = deposit(self.api, port, dwell, clock=RealClock(), confirm=None,
                              precheck_confirm=self._precheck_cb(), abort=self._abort_event)
                rl.set_result({"message": res.message, "completed": res.completed})
            self._last_run_path = rl.path
            return res
        self._run_task(fn, self._deposition_done, busy=[self.run_btn, self.fr_btn])

    def _run_full(self):
        from gisdep.orchestrate.full_run import run_full_workflow
        from gisdep.focus.autofocus import AutoFocusConfig
        grid = f"GRID{self.fr_grid.currentIndex() + 1}"
        port = self.fr_port.currentIndex() + 1
        dwell = float(self.fr_dwell.value())
        # fast autofocus settle for the mock; real window on the instrument
        af_cfg = (AutoFocusConfig(sustained_idle_s=1.0, startup_timeout_s=10.0)
                  if self._is_mock else AutoFocusConfig())
        self._append_log(f"=== FULL RUN: {grid}, GIS{port}, dwell {dwell:g}s ===")

        def fn():
            from gisdep.runlog import RunLog
            params = {"grid": grid, "port": port, "dwell": dwell}
            with RunLog("full_run", params, api=self.api) as rl:
                res = run_full_workflow(
                    self.api, grid, port, dwell, clock=RealClock(),
                    target_wd_m=self._cfg.wd_target_m,
                    confirm=self._confirm_cb(), af_cfg=af_cfg, abort=self._abort_event,
                )
                rl.set_result({
                    "cryo_reached": res.cryo.reached,
                    "wd_confirmed_mm": round(res.focus_wd.wd_after_confirm_m * 1e3, 4),
                    "deposition": res.deposition.message,
                })
            self._last_run_path = rl.path
            return res
        self._run_task(fn, self._full_done,
                       busy=[self.fr_btn, self.run_btn, self.grid1_btn, self.grid2_btn,
                             self.wd_btn, self.gotowd_btn, self.zminus_btn, self.zplus_btn])

    def _full_done(self, status, payload):
        if status == "ok":
            r = payload
            self._append_log(f"FULL RUN complete: {r.grid} → focus+WD → "
                             f"GIS{r.port} {r.deposition.message}")

    def _deposition_done(self, status, payload):
        if status == "ok":
            self._append_log(f"deposition: {payload.message}")

    def _simulate(self, port, field, value):
        if self._read_only or not self._is_mock:
            return
        self._display_state = None
        (self.api.set_gis_needle if field == "needle" else self.api.set_gis_valve)(port, value)
        self._refresh()

    def _reset_safe(self):
        if self._read_only or not self._is_mock:
            return
        for port in (1, 2):
            self.api.set_gis_valve(port, VALVE_CLOSED)
            self.api.set_gis_needle(port, NEEDLE_PARKED)
        self._append_log("[sim] reset to safe state")
        self._refresh()

    # --- refresh ------------------------------------------------------------
    def _pixmap(self, filename):
        if filename not in self._pixmaps:
            self._pixmaps[filename] = QPixmap(asset_path(filename))
        return self._pixmaps[filename]

    def _refresh(self):
        try:
            readings = read_all(self.api)
        except Exception as exc:
            self.caption.setText(f"read error: {exc}")
            return

        self._set_needle("n1", readings[1])
        self._set_valve("v1", readings[1])
        self._set_needle("n2", readings[2])
        self._set_valve("v2", readings[2])
        try:
            self._val["z"].setText(f"{self.api.get_stage_position().z_m * 1e3:.4f} mm")
        except Exception:
            self._val["z"].setText("—")
        try:
            self._val["wd"].setText(f"{self.api.get_analogue('AP_WD') * 1e3:.4f} mm")
        except Exception:
            self._val["wd"].setText("—")

        # image: honour a display override (just-loaded / insertion-blocked) first
        if self._display_state == "start":
            self._set_image(START, "GIS loaded — ready to position (move X / Z)", NEUTRAL)
            return
        if self._display_state == "blocked":
            self._set_image(BLOCKED, "Needle insertion not possible", RED)
            return

        state = active_port_state(readings)
        if state.transit and self._last_stable_file is not None:
            pix = self._pixmap(self._last_stable_file)
        else:
            pix = self._pixmap(state.filename)
            if not state.transit:
                self._last_stable_file = state.filename
        self.image_label.setPixmap(
            pix.scaled(self.image_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        self.caption.setText(state.caption)
        self.caption.setStyleSheet(_pill_css(RED if state.depositing else NEUTRAL))

    def _set_image(self, filename, caption, pill_color):
        pix = self._pixmap(filename)
        self.image_label.setPixmap(
            pix.scaled(self.image_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        self.caption.setText(caption)
        self.caption.setStyleSheet(_pill_css(pill_color))

    def _set_needle(self, key, r):
        color = {NEEDLE_PARKED: GREEN, NEEDLE_INSERTED: BLUE}.get(r.needle_raw, AMBER)
        self._val[key].setText(f"{r.needle_meaning}")
        self._val[key].setStyleSheet(_pill_css(color))

    def _set_valve(self, key, r):
        color = {VALVE_CLOSED: GREEN, VALVE_OPEN: RED}.get(r.valve_raw, AMBER)
        self._val[key].setText(f"{r.valve_meaning}")
        self._val[key].setStyleSheet(_pill_css(color))

    def _append_log(self, text):
        self.log.append(text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh()


def run(api=None, monitor=False, argv=None):
    app = QApplication.instance() or QApplication(argv or sys.argv)
    theme.apply_theme(app)
    win = GisdepWindow(api=api, monitor=monitor)
    win.resize(1160, 760)
    win.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(run())
