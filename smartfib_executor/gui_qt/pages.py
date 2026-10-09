"""Capture and Positions pages for the pyFIB Qt GUI (Phase 2).

Design notes:
* Capture ACTS ON THE INSTRUMENT, so it shells out to the existing CLI
  (`capture-position`) on the subprocess worker -- unchanged model, unchanged engine.
  The ARM toggle decides whether `--arm` is passed (armed = real; unarmed = the CLI's
  dry-run that only prints the plan).
* Positions only READS a JSON file, which is not an instrument action, so the table
  loads the records in-process via PositionDatabase.load(). That gives the structured
  v2 geometry fields directly and avoids parsing CLI text. BMP preview uses QPixmap,
  which loads BMP natively (no PIL/BMP dance the tkinter GUI needed).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from qtpy.QtCore import Qt
from qtpy.QtGui import QPixmap
from qtpy.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)


def _title(text: str) -> QLabel:
    lab = QLabel(text)
    f = lab.font(); f.setPointSize(20); f.setBold(True); lab.setFont(f)
    return lab


def _section(text: str) -> QLabel:
    lab = QLabel(text)
    f = lab.font(); f.setPointSize(14); f.setBold(True); lab.setFont(f)
    return lab


def _card() -> QFrame:
    fr = QFrame()
    fr.setObjectName("card")
    fr.setFrameShape(QFrame.StyledPanel)
    fr.setStyleSheet(
        "#card { background:#1c212b; border:1px solid #3a4150; border-radius:8px; }")
    return fr


class ArmBar(QWidget):
    """Top bar shared across pages: the ARM safety toggle + the positions file path.

    ARM is deliberately prominent and red when engaged. Unarmed, instrument-acting
    commands run as the CLI's dry-run; armed, they pass --arm and execute for real.
    """

    def __init__(self, on_change=None, on_new_session=None):
        super().__init__()
        self.setObjectName("armBar")
        self._on_new_session = on_new_session
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(10)

        self.arm_btn = QPushButton("ARM  (dry-run)")
        self.arm_btn.setCheckable(True)
        self.arm_btn.setObjectName("armButton")
        self.arm_btn.setMinimumWidth(160)
        self.arm_btn.toggled.connect(self._on_toggle)
        self._apply_arm_style(False)
        lay.addWidget(self.arm_btn)

        lay.addSpacing(16)
        lay.addWidget(QLabel("Positions file:"))
        self.pos_edit = QLineEdit()
        self.pos_edit.setPlaceholderText("path to positions.json")
        lay.addWidget(self.pos_edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        lay.addWidget(browse)

        lay.addSpacing(16)
        lay.addWidget(QLabel("Session:"))
        self.new_btn = QPushButton("New Session")
        self.new_btn.clicked.connect(self._new_session)
        lay.addWidget(self.new_btn)
        self.load_btn = QPushButton("Load Previous…")
        self.load_btn.clicked.connect(self._browse)
        lay.addWidget(self.load_btn)
        self.session_lbl = QLabel("")
        self.session_lbl.setStyleSheet("color:#9aa3b2;")
        lay.addWidget(self.session_lbl, 1)

        self._on_change = on_change
        self.pos_edit.textChanged.connect(self._update_session_label)

    def _new_session(self) -> None:
        from .session import new_session_positions_path
        self.pos_edit.setText(str(new_session_positions_path()))
        if self._on_new_session:
            self._on_new_session()

    def _update_session_label(self, _text: str = "") -> None:
        from pathlib import Path
        p = self.pos_edit.text().strip()
        self.session_lbl.setText(Path(p).resolve().parent.name if p else "")

    def _apply_arm_style(self, armed: bool) -> None:
        if armed:
            self.arm_btn.setText("ARMED  (instrument live)")
            self.arm_btn.setStyleSheet(
                "QPushButton { background:#5a1a14; color:#ffe9e6; border:1px solid "
                "#e0574a; border-radius:6px; padding:6px 12px; font-weight:700; }")
        else:
            self.arm_btn.setText("ARM  (dry-run)")
            self.arm_btn.setStyleSheet(
                "QPushButton { border:1px solid #3a4150; border-radius:6px; "
                "padding:6px 12px; }")

    def _on_toggle(self, checked: bool) -> None:
        self._apply_arm_style(checked)
        if self._on_change:
            self._on_change(checked)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select positions file", self.pos_edit.text() or "",
            "JSON (*.json);;All files (*)")
        if path:
            self.pos_edit.setText(path)

    def is_armed(self) -> bool:
        return self.arm_btn.isChecked()

    def positions_file(self) -> str:
        return self.pos_edit.text().strip()


def next_position_name(current: str, existing) -> str:
    """position_1 -> position_2 (keeps zero-padding: pos09 -> pos10); a name without a
    trailing number gets _2. Skips names already in `existing`."""
    import re
    cur = (current or "").strip() or "position_1"
    m = re.match(r"^(.*?)(\d+)$", cur)
    if m:
        prefix, n, width = m.group(1), int(m.group(2)), len(m.group(2))
    else:
        prefix, n, width = cur + "_", 1, 1
    existing = set(existing or ())
    while True:
        n += 1
        cand = f"{prefix}{n:0{width}d}"
        if cand not in existing:
            return cand


class CapturePage(QWidget):
    """Capture a named position via the `capture-position` CLI (subprocess)."""

    def __init__(self, window):
        super().__init__()
        self._win = window
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.addWidget(_title("Capture position"))

        info = QLabel(
            "Sets the FIB magnification, grabs a BMP, and records stage/objective "
            "plus the v2 geometry. Unarmed runs are a dry-run (plan only); ARM to "
            "capture for real. Output streams to the Log page.")
        info.setWordWrap(True)
        info.setStyleSheet("color: palette(mid);")
        lay.addWidget(info)

        form = QFormLayout()
        form.setContentsMargins(0, 10, 0, 10)
        self.name_edit = QLineEdit("position_1")
        form.addRow("Name", self.name_edit)
        self.mag_edit = QLineEdit("2000")
        form.addRow("FIB magnification", self.mag_edit)
        self.width_spin = QSpinBox(); self.width_spin.setRange(64, 8192)
        self.width_spin.setValue(1024)
        self.height_spin = QSpinBox(); self.height_spin.setRange(64, 8192)
        self.height_spin.setValue(768)
        wh = QWidget(); wh_l = QHBoxLayout(wh); wh_l.setContentsMargins(0, 0, 0, 0)
        wh_l.addWidget(self.width_spin); wh_l.addWidget(QLabel("×")); wh_l.addWidget(self.height_spin)
        wh_l.addStretch(1)
        form.addRow("Grab W × H", wh)
        self.imgdir_edit = QLineEdit()
        self.imgdir_edit.setPlaceholderText("(optional) image output directory")
        form.addRow("Image dir", self.imgdir_edit)
        self.tilt_chk = QCheckBox("Record tilt (AP_STAGE_AT_T) -- recall restores it")
        self.tilt_chk.setChecked(True)            # default ON
        form.addRow("", self.tilt_chk)
        # v0.39.0: an 800x reference beside the 2000x one. Mill batches use it only
        # when the 2000x refine cannot find the site (then: 800x coarse pass, stage if
        # allowed, then 2000x). One extra FIB frame at 800x, scan speed 5.
        self.coarse_chk = QCheckBox("Also capture 800x image (refine fallback)")
        self.coarse_chk.setChecked(True)          # default ON (Evan, 2026-10-01)
        self.coarse_chk.setToolTip(
            "Grabs a second reference at 800x right after the 2000x one. If refine at "
            "2000x cannot find the site in a mill batch, refine retries with this wider "
            "800x view (stage move if 'Refine may move stage' is ticked), then 2000x.")
        form.addRow("", self.coarse_chk)

        # Fiducial BEFORE capture: refine registers the live image against the
        # reference captured here, so a fiducial must exist before the reference is
        # taken. Mill it at the CURRENT stage position, at this capture's mag, then
        # capture -- the capture records which side (and which number) got one.
        fidw = QWidget(); fl = QHBoxLayout(fidw); fl.setContentsMargins(0, 0, 0, 0)
        self.fid_kind = QComboBox(); self.fid_kind.addItems(["number", "square"])
        self.fid_kind.setToolTip("number: loop-free numbered fiducial 1-30 (elynumbers), "
                                 "written through elymaker; square: the prototype .ely")
        fl.addWidget(self.fid_kind)
        self.fid_side = QComboBox(); self.fid_side.addItems(["left", "right"])
        fl.addWidget(self.fid_side)
        fl.addWidget(QLabel("#"))
        self.fid_number = QSpinBox(); self.fid_number.setRange(1, 30); self.fid_number.setValue(1)
        self.fid_number.setToolTip("Counts up automatically after each capture that "
                                   "records a numbered fiducial.")
        fl.addWidget(self.fid_number)
        self.fid_here_btn = QPushButton("Mill fiducial here")
        self.fid_here_btn.clicked.connect(self._on_mill_fiducial_here)
        fl.addWidget(self.fid_here_btn)
        fl.addStretch(1)
        form.addRow("Fiducial", fidw)
        recw = QWidget(); rl = QHBoxLayout(recw); rl.setContentsMargins(0, 0, 0, 0)
        self.fid_record = QComboBox(); self.fid_record.addItems(["none", "left", "right"])
        rl.addWidget(self.fid_record)
        self.fid_record_lbl = QLabel("")
        self.fid_record_lbl.setStyleSheet("color:#9aa3b2;")
        rl.addWidget(self.fid_record_lbl)
        rl.addStretch(1)
        form.addRow("Record on capture", recw)
        for sig in (self.fid_record.currentTextChanged, self.fid_kind.currentTextChanged):
            sig.connect(self._update_record_label)
        self.fid_number.valueChanged.connect(self._update_record_label)
        self.fid_kind.currentTextChanged.connect(
            lambda k: self.fid_number.setEnabled(k == "number"))
        hint = QLabel("Mill the fiducial BEFORE capturing so the reference image "
                      "contains it (refine needs that). 'Record on capture' is set "
                      "automatically after you mill one and cleared after the capture. "
                      "After each successful ARMED capture the name and the fiducial "
                      "number count up.")
        hint.setWordWrap(True); hint.setStyleSheet("color:#9aa3b2;")
        form.addRow("", hint)
        self._update_record_label()
        lay.addLayout(form)

        row = QHBoxLayout()
        self.capture_btn = QPushButton("Capture")
        self.capture_btn.setObjectName("toolButton")
        self.capture_btn.clicked.connect(self._on_capture)
        row.addWidget(self.capture_btn)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)

    # ---------------------------------------------------------------- helpers
    def _load_db(self):
        pf = self._win.positions_file()
        if not pf or not Path(pf).exists():
            return None
        try:
            from ..positions import PositionDatabase
            return PositionDatabase.load(Path(pf))
        except Exception:
            return None

    def _existing_names(self) -> set:
        db = self._load_db()
        return set(db.positions) if db is not None else set()

    def _update_record_label(self, *_):
        side = self.fid_record.currentText()
        if side == "none":
            self.fid_record_lbl.setText("(no fiducial will be recorded)")
        elif self.fid_kind.currentText() == "number":
            self.fid_record_lbl.setText(f"records #{self.fid_number.value()} on the {side}")
        else:
            self.fid_record_lbl.setText(f"records the {side} square")

    def refresh_suggestions(self) -> None:
        """Avoid collisions with the session: bump the name if it is already used, and
        the fiducial number if that number is already recorded."""
        db = self._load_db()
        if db is None:
            return
        names = set(db.positions)
        cur = self.name_edit.text().strip() or "position_1"
        if cur in names:
            self.name_edit.setText(next_position_name(cur, names))
        used = {r.fiducial_number for r in db.positions.values()
                if getattr(r, "fiducial_number", None)}
        if used and self.fid_number.value() in used:
            self.fid_number.setValue(min(max(used) + 1, self.fid_number.maximum()))

    def showEvent(self, ev):  # noqa: N802 (Qt)
        self.refresh_suggestions()
        super().showEvent(ev)

    def _fiducial_file(self, side: str):
        """(path, label) for the selected fiducial, or (None, error message)."""
        from ..fiducial import fiducial_ely_path, fiducial_available, number_fiducial_path
        if self.fid_kind.currentText() == "number":
            pf = self._win.positions_file()
            if not pf:
                return None, ("Set a positions file first (top bar): numbered fiducials "
                              "are written into the session folder.")
            n = self.fid_number.value()
            try:
                return number_fiducial_path(Path(pf).parent, n, side), f"#{n} ({side})"
            except Exception as e:  # noqa: BLE001
                return None, f"Could not write numbered fiducial #{n}: {e}"
        if not fiducial_available(side):
            return None, (f"No {side} fiducial pattern found. Drop it in as "
                          f"{fiducial_ely_path(side)}")
        return fiducial_ely_path(side), f"{side} square"

    # --------------------------------------------------------------- actions
    def _on_mill_fiducial_here(self) -> None:
        """Mill the selected fiducial (numbered or square) at the CURRENT stage position
        (no recall), at this capture's magnification, via `run --fib-mag`. Sets 'Record
        on capture' so the following Capture records it."""
        side = self.fid_side.currentText()
        try:
            mag = float(self.mag_edit.text().strip() or "2000")
        except ValueError:
            self._win.status("FIB magnification must be a number.")
            return
        path, label = self._fiducial_file(side)
        if path is None:
            self._win.status(label)
            return
        from ..fiducial import fiducial_fit
        fits, _margin, detail = fiducial_fit(side, mag, path=path)
        if not fits and not self._win.confirm(
                "WARNING: fiducial {} will NOT fit in the field of view at {:.0f}x ({}). "
                "It would be clipped or not milled. Mill anyway?".format(label, mag, detail)):
            return
        armed = self._win.is_armed()
        if armed and not self._win.confirm(
                "Mill fiducial {} at the CURRENT stage position at {:.0f}x? (Capture the "
                "position afterwards so the reference includes it.)".format(label, mag)):
            return
        args = ["run", str(path), "--fib-mag", f"{mag:g}"]
        if armed:
            args.append("--arm")
        self.fid_record.setCurrentText(side)   # the next Capture records it
        self._win.run_cli(args)

    def _on_capture(self) -> None:
        name = self.name_edit.text().strip() or "position_1"
        pos_file = self._win.positions_file()
        if not pos_file:
            self._win.status("Set a positions file first (top bar).")
            return
        existing = self._existing_names()
        if name in existing and not self._win.confirm(
                f"'{name}' already exists in this session. Re-capture it (overwrite)?"):
            return
        args = ["capture-position", name, "--positions-file", pos_file,
                "--fib-mag", self.mag_edit.text().strip() or "2000",
                "--width", str(self.width_spin.value()),
                "--height", str(self.height_spin.value())]
        if self.imgdir_edit.text().strip():
            args += ["--image-dir", self.imgdir_edit.text().strip()]
        else:
            # Group BMPs beside the session's positions.json (<session>/images).
            from .session import images_dir_for
            args += ["--image-dir", str(images_dir_for(pos_file))]
        args += ["--include-tilt"] if self.tilt_chk.isChecked() else ["--no-include-tilt"]
        if self.coarse_chk.isChecked():
            args += ["--coarse-mag", "800"]
        rec_side = self.fid_record.currentText()
        fid_n = None
        if rec_side in ("left", "right"):
            args += ["--fiducial-side", rec_side]
            if self.fid_kind.currentText() == "number":
                fid_n = self.fid_number.value()
                args += ["--fiducial-number", str(fid_n)]
        armed = self._win.is_armed()
        if armed:
            args += ["--arm"]

        def _done(rc, _armed=armed, _name=name, _n=fid_n, _existing=existing):
            # Only a SUCCESSFUL, ARMED capture advances the counters (a dry run or a
            # failure captured nothing).
            if rc == 0 and _armed:
                self._advance_after_capture(_name, _n, set(_existing) | {_name})
        self._win.run_cli(args, on_done=_done)

    def _advance_after_capture(self, name: str, fid_n, existing: set) -> None:
        nxt = next_position_name(name, existing)
        self.name_edit.setText(nxt)
        msg = f"Captured {name}; next: {nxt}"
        if fid_n is not None:
            if fid_n < self.fid_number.maximum():
                self.fid_number.setValue(fid_n + 1)
                msg += f", fiducial #{fid_n + 1}"
            else:
                msg += f" (fiducial numbers 1-{self.fid_number.maximum()} used up)"
        self.fid_record.setCurrentText("none")   # never carry a fiducial to the next one
        self._win.status(msg)


class ElymakerTab(QWidget):
    """Embeds elymaker's ACTUAL GUI (MainWindow) as a pyFIB tab.

    elymaker is a pure pattern-authoring tool -- it does NOT touch the instrument, so
    there's no connection to bridge; it just needs to be embedded. Built lazily on
    first open. pyFIB can later hand it a position's FIB image + geometry via
    open_handoff(...) so a pattern is authored over the right image at the right pixel
    size; elymaker writes the .ely + a ready-marker, which pyFIB picks up to link the
    pattern to its position (wiring TBD -- see the handoff questions).
    """

    def __init__(self, window):
        super().__init__()
        self._win = window
        self._ewin = None
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._status = QLabel("Opening ELYMaker…")
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setStyleSheet("color:#9aa3b2;")
        self._lay.addWidget(self._status)

    def ensure_built(self) -> None:
        if self._ewin is not None:
            return
        try:
            from elymaker.gui.main_window import MainWindow as ElyMainWindow
        except Exception as e:  # pragma: no cover
            self._status.setText(f"ELYMaker unavailable: {e}")
            return
        try:
            self._ewin = ElyMainWindow()
        except Exception as e:  # pragma: no cover
            self._status.setText(f"Could not start ELYMaker: {e}")
            return
        self._status.hide()
        self._lay.addWidget(self._ewin)   # QMainWindow embeds as the tab body

    def open_handoff(self, **kwargs) -> None:
        """Preload a position's image + geometry into elymaker (image_path,
        pixel_size_um, out_path, load_path, fov_width_um, site)."""
        self.ensure_built()
        if self._ewin is not None:
            try:
                self._ewin.open_handoff(**kwargs)
            except Exception as e:  # noqa: BLE001
                self._win.status(f"ELYMaker handoff failed: {e}")


class GisdepTab(QWidget):
    """Embeds gisdep's ACTUAL GUI (GisdepWindow) as a pyFIB tab.

    This brings gisdep's full interface across unchanged -- the chamber-view graphics,
    the live-state grid, and the full-run / cryo / workflow / deposition cards with
    their own ARM/STOP controls. It's built lazily the first time the tab is opened
    (so pyFIB startup doesn't connect), and driven inline (as gisdep's GUI always was).

    Connection: it tries pyFIB's SmartSEM through the bridge adapter (one instrument
    connection); if that can't connect (e.g. offline development), it falls back to
    gisdep's MockSmartSEM so the full UI still renders and can be explored.
    """

    def __init__(self, window):
        super().__init__()
        self._win = window
        self._gwin = None
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._status = QLabel("Opening the GISDEP interface…")
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setStyleSheet("color:#9aa3b2;")
        self._lay.addWidget(self._status)

    def ensure_built(self) -> None:
        if self._gwin is not None:
            return
        try:
            from gisdep.gui.app import GisdepWindow
        except Exception as e:  # pragma: no cover
            self._status.setText(f"gisdep GUI unavailable: {e}")
            return

        gwin = None
        # Prefer pyFIB's connection via the bridge (one instrument); fall back to the
        # mock if it can't initialise (offline). GisdepWindow.__init__ calls
        # api.initialise(), so a failed connection raises here and we retry with mock.
        try:
            from ..smartsem_com import SmartSEM
            from ..gisdep_adapter import bridged_api
            gwin = GisdepWindow(api=bridged_api(SmartSEM()))
            self._win.status("GISDEP: connected via pyFIB.")
        except Exception:
            try:
                gwin = GisdepWindow(api=None)   # MockSmartSEM (offline)
                self._win.status("GISDEP: no instrument -> running on the mock.")
            except Exception as e:  # pragma: no cover
                self._status.setText(f"Could not start GISDEP: {e}")
                return

        self._gwin = gwin
        self._status.hide()
        # Embed the whole gisdep window (a QMainWindow is also a QWidget) as the tab
        # body. It is never shown as a separate OS window.
        self._lay.addWidget(gwin)
        # Reconcile ARM: pyFIB's global ARM toggle governs the gisdep tab too, so
        # gisdep's own ARM button is hidden and its arm state is driven from pyFIB.
        self.sync_arm()

    def sync_arm(self) -> None:
        """Drive gisdep's arm state from pyFIB's global ARM toggle; hide gisdep's
        redundant ARM button (its badge still shows the current state)."""
        if self._gwin is None:
            return
        try:
            self._gwin._armed = bool(self._win.is_armed())
            self._gwin._refresh_armed_state()
            self._gwin.arm_btn.setVisible(False)
        except Exception:
            pass


class SetupPage(QWidget):
    """Setup: Prep CryoFIBSEM -> Move to position -> GISDEP -> Coincidence.

    Order matches the intended cryo workflow. Prep CryoFIBSEM's action is not defined
    yet (placeholder until its purpose is provided). Move-to-position and coincidence
    are ARM-gated. Coincidence shows the SEM + FIB overview images side by side.
    """

    def __init__(self, window):
        super().__init__()
        self._win = window
        self._coinc_img_dir = None
        self._coinc_sem = None
        self._coinc_fib = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.addWidget(_title("Setup"))

        # 1) Prep CryoFIBSEM ---------------------------------------------------
        prep = _card(); pl = QVBoxLayout(prep)
        pl.addWidget(_section("1 · Prep CryoFIBSEM"))
        pl.addWidget(self._muted("Prepares the cryo-FIB/SEM before positioning. "
                                 "(Action to be defined.)"))
        self.prep_btn = QPushButton("Prep CryoFIBSEM")
        self.prep_btn.setObjectName("toolButton")
        self.prep_btn.clicked.connect(self._on_prep)
        pl.addWidget(self.prep_btn, alignment=Qt.AlignLeft)
        outer.addWidget(prep)

        # 2) Move to position (via CLEM SAFE) --------------------------------
        mv = _card(); ml = QVBoxLayout(mv)
        ml.addWidget(_section("2 · Move to position (via CLEM SAFE)"))
        ml.addWidget(self._muted(
            "Recall a SETUP fixture. Every move goes VIA CLEM SAFE (Z/XYTR/M to CLEM "
            "SAFE, then M/XYTR/Z to the destination), confirming arrival at each. "
            "Skipped only if already within 0.3 mm of CLEM SAFE (exact T/R/M). "
            "ARM-gated and confirmed. (Milling recall does NOT detour via CLEM SAFE.)"))
        row = QHBoxLayout()
        row.addWidget(QLabel("Position:"))
        self.pos_combo = QComboBox()
        self.pos_combo.setMinimumWidth(200)
        row.addWidget(self.pos_combo)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.reload_positions)
        row.addWidget(self.refresh_btn)
        row.addStretch(1)
        ml.addLayout(row)
        self.move_btn = QPushButton("Move to position")
        self.move_btn.setObjectName("toolButton")
        self.move_btn.clicked.connect(self._on_move)
        ml.addWidget(self.move_btn, alignment=Qt.AlignLeft)
        outer.addWidget(mv)

        # 4) Coincidence -------------------------------------------------------
        co = _card(); cl = QVBoxLayout(co)
        cl.addWidget(_section("3 · SEM WD / FIB-SEM coincidence"))
        cl.addWidget(self._muted(
            "Autofocus the SEM, read WD, and recommend/adjust Z toward the 5 mm "
            "coincidence point (moves Z itself only for <= 0.5 mm). Unarmed runs are "
            "SIMULATED. After an armed adjust, the SEM and FIB overview images show "
            "side by side below."))
        crow = QHBoxLayout()
        self.measure_btn = QPushButton("Measure Working Distance")
        self.measure_btn.setObjectName("toolButton")
        self.measure_btn.clicked.connect(self._on_measure)
        self.adjust_btn = QPushButton("Measure & Adjust Z (≤ 0.5 mm)")
        self.adjust_btn.clicked.connect(self._on_adjust)
        crow.addWidget(self.measure_btn); crow.addWidget(self.adjust_btn)
        crow.addStretch(1)
        cl.addLayout(crow)
        imgrow = QHBoxLayout()
        self.sem_lbl = QLabel("SEM: (no image yet)")
        self.fib_lbl = QLabel("FIB: (no image yet)")
        for lbl in (self.sem_lbl, self.fib_lbl):
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setMinimumHeight(180)
            lbl.setFrameShape(QFrame.StyledPanel)
            lbl.setStyleSheet("background:#12151b; color:#9aa3b2;")
            imgrow.addWidget(lbl, 1)
        cl.addLayout(imgrow)
        outer.addWidget(co)
        outer.addStretch(1)

        self.reload_positions()

    # --- helpers ---
    def _muted(self, text: str) -> QLabel:
        lab = QLabel(text); lab.setWordWrap(True)
        lab.setStyleSheet("color:#9aa3b2;")
        return lab

    def reload_positions(self) -> None:
        """Populate the move dropdown from the shipped setup fixtures (not session)."""
        self.pos_combo.clear()
        try:
            from ..setup_move import load_setup_db
            db = load_setup_db()
            self.pos_combo.addItems(list(db.positions.keys()))
        except Exception:
            pass
        # GIS full-run grids come from gisdep's (validated) waypoints, minus CLEM SAFE.
        if hasattr(self, "gis_grid"):
            self.gis_grid.clear()
            try:
                from gisdep.stage.cryo import DEFAULT_WAYPOINTS
                self.gis_grid.addItems([n for n in DEFAULT_WAYPOINTS
                                        if n.upper() != "CLEM_SAFE"])
            except Exception:
                pass

    # --- 1) prep CryoFIBSEM ---
    def _on_prep(self) -> None:
        armed = self._win.is_armed()
        if armed and not self._win.confirm(
                "Prep CryoFIBSEM will turn the SEM EHT OFF, close the column and FIB "
                "valves, set the SEM to 2.00 keV / 20 pA, and MOVE THE STAGE to Cryo "
                "Insertion via CLEM SAFE (confirming arrival at each).\n\nProceed on "
                "the instrument?"):
            return
        # prep_cryo reads CLEM SAFE + Cryo Insertion from the shipped setup fixtures.
        args = ["--arm"] if armed else []
        self._win.run_module("smartfib_executor.prep_cryo", args)

    # --- 2) move to setup position (via CLEM SAFE) ---
    def _on_move(self) -> None:
        name = self.pos_combo.currentText().strip()
        if not name:
            self._win.status("Pick a setup position.")
            return
        armed = self._win.is_armed()
        if armed and not self._win.confirm(
                "Move the stage to '{}' VIA CLEM SAFE? This drives the stage through "
                "the safe middle point (Z/XYTR/M to CLEM SAFE, then M/XYTR/Z to the "
                "destination) and confirms arrival at each.".format(name)):
            return
        args = [name]
        if armed:
            args.append("--arm")
        self._win.run_module("smartfib_executor.setup_move", args)

    # --- 3) coincidence ---
    def _coincidence_args(self, *, dry_run: bool):
        args = []
        if not self._win.is_armed():
            args.append("--simulate")
        if dry_run:
            args.append("--dry-run")
        else:
            import tempfile
            self._coinc_img_dir = tempfile.mkdtemp(prefix="pyfib_coinc_")
            self._coinc_sem = None
            self._coinc_fib = None
            args += ["--image-dir", self._coinc_img_dir]
        return args

    def _on_measure(self) -> None:
        self._win.run_module("smartfib_executor.coincidence",
                             self._coincidence_args(dry_run=True))

    def _on_adjust(self) -> None:
        if self._win.is_armed() and not self._win.confirm(
                "Measure WD and, if the correction is ≤ 0.5 mm, MOVE the stage Z "
                "toward the 5 mm coincidence point? Code guards enforce the limits."):
            return
        self._win.run_module(
            "smartfib_executor.coincidence", self._coincidence_args(dry_run=False),
            input_text="yes\n", on_line=self._on_coinc_line,
            on_done=self._on_coinc_done)

    def _on_coinc_line(self, line: str) -> None:
        t = line.strip()
        if t.startswith("SEM_IMAGE:"):
            self._coinc_sem = t.split("SEM_IMAGE:", 1)[1].strip()
        elif t.startswith("FIB_IMAGE:"):
            self._coinc_fib = t.split("FIB_IMAGE:", 1)[1].strip()

    def _on_coinc_done(self, rc: int) -> None:
        self._show_one(self.sem_lbl, self._coinc_sem, "SEM")
        self._show_one(self.fib_lbl, self._coinc_fib, "FIB")
        if self._coinc_sem or self._coinc_fib:
            self._win.show_page("setup")

    def _show_one(self, lbl, path, tag) -> None:
        if not path or not Path(path).exists():
            return
        pm = QPixmap(str(path))
        if pm.isNull():
            lbl.setText(f"{tag}: could not load\n{path}")
            return
        lbl.setPixmap(pm.scaled(lbl.width(), lbl.height(),
                                Qt.KeepAspectRatio, Qt.SmoothTransformation))
        lbl.setText("")


class MillPage(QWidget):
    """Milling queue: per-position select / autofocus / refine, then `mill-positions`.

    v0.50.0 (Evan, 2026-10-07): one class, three tabs -- Rough Mill, Prepolish and Polish
    (``title`` / ``stage``).

    v0.51.0 milling stages (mill_stages.py): every tab runs `mill-positions --stage S`.
      * Rough Mill: the standard pattern (a position's authored pattern overrides it);
        refine aligns to the capture image.
      * Prepolish / Polish: their OWN pattern at every position (authored rough patterns
        are never milled); refine aligns to the latest Rough Mill / Prepolish after image.
        Checked positions without that image are listed and need an OK before the batch
        (they are not milled).
      * After-mill image ON by default, named <name>_<n>-<stage>_<time>.bmp and recorded
        in positions.json; the "Align to" / "After image" columns show them.
      * Manual, for the selected position: Go to · Align · Push pattern · Record after
        image (`site ...`). Push loads + prepares the pattern in SmartFIB WITHOUT starting
        it. These keep this tab on screen (output also goes to the Log page).

    Reuses PositionDatabase for the queue rows. Assembles the mill-positions argv from
    the checked rows + options, gating --arm on the shared ARM toggle, and updates a
    live per-position status column by parsing the streamed CLI output.
    """

    (C_MILL, C_AF, C_REFINE, C_NAME, C_STATUS, C_REF, C_AFTER, C_MAG,
     C_OBJ) = range(9)
    HEADERS = ["Mill", "AF", "Refine", "Position", "Status", "Align to", "After image",
               "Mag (x)", "Obj (V)"]
    MANUAL_ACTIONS = (("goto", "Go to"), ("align", "Align"), ("push", "Push pattern"),
                      ("after-image", "Record after image"))

    def __init__(self, window, title: str = "Rough Mill", stage: str = "rough"):
        super().__init__()
        from ..mill_stages import (FORCED_PATTERN_STAGES, PREVIOUS_STAGE, STAGE_LABEL,
                                   default_stage_pattern, stage_tag)
        self._win = window
        self._db = None
        self._row_by_name = {}
        self.title = title
        self.stage = stage
        self._forced = stage in FORCED_PATTERN_STAGES
        self._prev = PREVIOUS_STAGE[stage]
        self._label = STAGE_LABEL[stage]

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.addWidget(_title(title))

        # pattern (.ely) picker
        prow = QHBoxLayout()
        prow.addWidget(QLabel("Standard pattern (.ely):" if not self._forced
                              else f"{self._label} pattern (.ely):"))
        self.ely_edit = QLineEdit()
        self.ely_edit.setPlaceholderText(
            "path to pattern .ely" if not self._forced else
            f"path to the {self._label.lower()} .ely (no default installed yet)")
        dflt = default_stage_pattern(stage) if self._forced else None
        if dflt is not None:
            self.ely_edit.setText(str(dflt))
        prow.addWidget(self.ely_edit, 1)
        b = QPushButton("Browse…")
        b.clicked.connect(self._browse_ely)
        prow.addWidget(b)
        lay.addLayout(prow)
        if self._forced:
            prev = STAGE_LABEL[self._prev]
            hint = QLabel(f"Every position mills this pattern (authored rough patterns are not "
                          f"used). Refine aligns to the position's latest {prev} after image; "
                          f"positions without one are listed before starting and not milled.")
        else:
            hint = QLabel("Positions with an authored pattern mill that instead of the "
                          "standard one. Refine aligns to the capture image.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#9aa3b2;")
        lay.addWidget(hint)

        # options row
        orow = QHBoxLayout()
        orow.addWidget(QLabel("Wait:"))
        self.wait_combo = QComboBox()
        self.wait_combo.addItems(["auto", "mode_change", "blanked", "sleep", "none"])
        orow.addWidget(self.wait_combo)
        orow.addWidget(QLabel("Move:"))
        self.move_combo = QComboBox()
        # movestage first = default (v0.38.0): ap_goto ignored a 14 um recall on the
        # instrument (2026-10-01); MoveStage moved it.
        self.move_combo.addItems(["movestage", "ap_goto", "none"])
        orow.addWidget(self.move_combo)
        orow.addWidget(QLabel("Imaging scan rate:"))
        self.scan_spin = QSpinBox(); self.scan_spin.setRange(-1, 15)
        self.scan_spin.setValue(8)
        orow.addWidget(self.scan_spin)
        self.continue_chk = QCheckBox("Continue on error")
        orow.addWidget(self.continue_chk)
        orow.addStretch(1)
        lay.addLayout(orow)
        # Refine stage correction: opt-in (default OFF).
        rrow = QHBoxLayout()
        self.refine_stage_chk = QCheckBox("Refine may move stage (X/Y, ≤ 100 µm)")
        self.refine_stage_chk.setToolTip(
            "When a site's drift exceeds the 10 um beam-shift window, refine makes ONE "
            "X/Y stage move (<= 100 um), undone unless the drift halves, then finishes "
            "with beam shift. Only at R = 12 and tilt -1..31 deg; refuses otherwise.")
        # v0.39.0: default ON in the UI (validated 2026-10-01; Evan). The CLI keeps the
        # explicit --refine-enable-stage opt-in.
        self.refine_stage_chk.setChecked(True)
        rrow.addWidget(self.refine_stage_chk)
        self.refine_skip_chk = QCheckBox("Skip site if refine fails")
        self.refine_skip_chk.setToolTip(
            "Do not mill a site whose refine failed (with 'Continue on error' the batch "
            "moves on to the next site; without it the batch stops). Default: mill at "
            "the recalled position.")
        if self._forced:
            # v0.51.0 review: off by the rough refine correction a prepolish/polish pattern
            # could cut the lamella, so a failed refine never mills here (batch enforces it).
            self.refine_skip_chk.setChecked(True)
            self.refine_skip_chk.setEnabled(False)
            self.refine_skip_chk.setToolTip(
                f"Always on in {self._label}: a site whose refine failed is never milled "
                f"(with 'Continue on error' the batch moves on; without it, it stops).")
        rrow.addWidget(self.refine_skip_chk)
        # v0.41.0 after-mill image; v0.51.0: ON by default (the next stage aligns to it).
        self.after_chk = QCheckBox("After-mill image")
        self.after_chk.setChecked(True)
        self.after_chk.setToolTip(
            f"After milling each site, grab ONE fresh FIB frame at its capture "
            f"magnification, before the beam shift is reset, as images/<position>_"
            f"{stage_tag(stage)}_<time>.bmp, and record it in positions.json"
            + ("; Prepolish aligns to it" if stage == "rough" else
               "; Polish aligns to it" if stage == "prepolish" else "")
            + ". Skipped with a warning if SmartSEM still indicates milling. Adds one frame "
              "of ion dose per site.")
        rrow.addWidget(self.after_chk)
        rrow.addStretch(1)
        lay.addLayout(rrow)

        # select helpers: explicit per-column All / None (no state-dependent toggles,
        # which inverted mixed columns -- "Toggle all" unchecked Mill and checked
        # AF/Refine because they started in different states).
        srow = QHBoxLayout()
        self.refresh_btn = QPushButton("Load / Refresh")
        self.refresh_btn.clicked.connect(self.reload)
        srow.addWidget(self.refresh_btn)
        for label, col in (("Mill", self.C_MILL), ("Autofocus", self.C_AF),
                           ("Refine", self.C_REFINE)):
            srow.addSpacing(8)
            srow.addWidget(QLabel(f"{label}:"))
            b_all = QPushButton("All")
            b_all.clicked.connect(lambda _=False, c=col: self._set_col(c, True))
            b_none = QPushButton("None")
            b_none.clicked.connect(lambda _=False, c=col: self._set_col(c, False))
            srow.addWidget(b_all)
            srow.addWidget(b_none)
        srow.addStretch(1)
        lay.addLayout(srow)

        # queue table
        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for c in (self.C_MILL, self.C_AF, self.C_REFINE):
            self.table.horizontalHeader().setSectionResizeMode(
                c, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._on_select)
        lay.addWidget(self.table, 1)

        # manual, one position at a time (v0.51.0)
        mbox = _card()
        ml = QVBoxLayout(mbox)
        ml.setContentsMargins(12, 10, 12, 10)
        mrow = QHBoxLayout()
        self.sel_label = QLabel("Selected position: —")
        self.sel_label.setStyleSheet("font-weight:600;")
        mrow.addWidget(self.sel_label)
        mrow.addSpacing(12)
        self.manual_btns = {}
        for act, text in self.MANUAL_ACTIONS:
            btn = QPushButton(text)
            btn.clicked.connect(lambda _=False, a=act: self._on_manual(a))
            mrow.addWidget(btn)
            self.manual_btns[act] = btn
        mrow.addStretch(1)
        ml.addLayout(mrow)
        self.manual_status = QLabel(
            "Manual: Go to → Align → Push pattern (milling is NOT started; start it in "
            "SmartFIB) → Record after image.")
        self.manual_status.setWordWrap(True)
        self.manual_status.setStyleSheet("color:#9aa3b2;")
        ml.addWidget(self.manual_status)
        self.manual_btns["goto"].setToolTip(
            "Zero the FIB beam shift, move the stage to this position (MoveStage, recorded "
            "tilt), set its objective voltage and FIB magnification.")
        self.manual_btns["align"].setToolTip(
            "Refine to " + ("the capture image" if self._prev is None else
                            f"the latest {STAGE_LABEL[self._prev]} after image")
            + ". The beam-shift correction stays on for milling.")
        self.manual_btns["push"].setToolTip(
            "Load + prepare this stage's pattern in SmartFIB (drop folder, LOAD_ELY, "
            "PREPARE_EXPOSURE). Milling is NOT started.")
        self.manual_btns["after-image"].setToolTip(
            f"Grab one FIB frame at the capture magnification as images/<position>_"
            f"{stage_tag(stage)}_<time>.bmp and record it (positions.json + a run log).")
        lay.addWidget(mbox)

        # start
        brow = QHBoxLayout()
        self.start_btn = QPushButton("Start milling")
        self.start_btn.setObjectName("toolButton")
        self.start_btn.clicked.connect(self._on_start)
        brow.addWidget(self.start_btn)
        brow.addStretch(1)
        lay.addLayout(brow)

    def _browse_ely(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select pattern", self.ely_edit.text() or "",
            "ELY (*.ely);;All files (*)")
        if path:
            self.ely_edit.setText(path)

    def _mk_check(self, checked=False) -> QTableWidgetItem:
        it = QTableWidgetItem()
        it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        it.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        return it

    def _session_dir(self) -> Optional[Path]:
        pf = self._win.positions_file()
        return Path(pf).parent if pf else None

    def reload(self) -> None:
        pos_file = self._win.positions_file()
        if not pos_file:
            self._win.status("Set a positions file first (top bar).")
            return
        try:
            from ..positions import PositionDatabase
            self._db = PositionDatabase.load(Path(pos_file))
        except Exception as exc:  # noqa: BLE001
            self._win.status(f"Could not load positions: {exc}")
            self.table.setRowCount(0)
            return
        # Keep the operator's ticks for rows that were already listed (v0.51.0: switching
        # tabs no longer resets them).
        keep = {}
        for n, r in self._row_by_name.items():
            if r < self.table.rowCount() and self.table.item(r, self.C_NAME) is not None \
                    and self.table.item(r, self.C_NAME).text() == n:
                keep[n] = tuple(self.table.item(r, c).checkState() == Qt.Checked
                                for c in (self.C_MILL, self.C_AF, self.C_REFINE))
        selected = self.selected_name()
        names = sorted(self._db.positions.keys())
        self._row_by_name = {n: i for i, n in enumerate(names)}
        self.table.setRowCount(len(names))
        for r, name in enumerate(names):
            rec = self._db.positions[name]
            mill, af, rf = keep.get(name, (True, False, self._forced))
            self.table.setItem(r, self.C_MILL, self._mk_check(mill))
            self.table.setItem(r, self.C_AF, self._mk_check(af))
            self.table.setItem(r, self.C_REFINE, self._mk_check(rf))
            self.table.setItem(r, self.C_NAME, QTableWidgetItem(name))
            self.table.setItem(r, self.C_STATUS, QTableWidgetItem(""))
            self.table.setItem(r, self.C_REF, QTableWidgetItem(""))
            self.table.setItem(r, self.C_AFTER, QTableWidgetItem(""))
            self.table.setItem(r, self.C_MAG,
                               QTableWidgetItem(f"{rec.fib_magnification_x:.0f}"))
            self.table.setItem(r, self.C_OBJ,
                               QTableWidgetItem(f"{rec.fib_objective_potential_v:.1f}"))
        self._fill_stage_columns()
        if selected in self._row_by_name:
            self.table.selectRow(self._row_by_name[selected])
        self._win.status(f"Queue: {len(names)} position(s).")

    def refresh_stage_columns(self) -> None:
        """Re-read positions.json and update only the Align to / After image columns
        (after a manual step or a batch); the ticks are left alone."""
        pos_file = self._win.positions_file()
        if not pos_file:
            return
        try:
            from ..positions import PositionDatabase
            self._db = PositionDatabase.load(Path(pos_file))
        except Exception:  # noqa: BLE001
            return
        self._fill_stage_columns()

    def _fill_stage_columns(self) -> None:
        from qtpy.QtGui import QColor
        from ..mill_stages import latest_after_image, reference_for_stage, stamp_of
        base = self._session_dir()
        for name, r in self._row_by_name.items():
            rec = self._db.positions.get(name) if self._db else None
            if rec is None or self.table.item(r, self.C_REF) is None:
                continue
            ref, _desc, why = reference_for_stage(rec, self.stage, base)
            if ref is None:
                txt = "missing"
                tip = why or ""
            elif self._prev is None:
                txt, tip = "capture image", ref.name
            else:
                when = stamp_of(ref)
                txt = f"{self._prev} {when[5:16] if when else ref.name}"
                tip = ref.name
            it = self.table.item(r, self.C_REF)
            it.setText(txt)
            it.setToolTip(tip)
            it.setForeground(QColor("#e5534b") if ref is None else QColor("#c9d1d9"))
            aft = latest_after_image(rec, self.stage, base)
            when = stamp_of(aft) if aft is not None else None
            it2 = self.table.item(r, self.C_AFTER)
            it2.setText((when[5:16] if when else aft.name) if aft is not None else "—")
            it2.setToolTip(aft.name if aft is not None else "")

    def _set_col(self, col, checked: bool) -> None:
        """Set every row in a column to a fixed state (no inversion).

        For the Autofocus / Refine columns, "All" only checks rows that are selected
        for milling: autofocus/refine at an un-milled site never runs (the argv
        builder drops them), so a checkbox there would look on while doing nothing.
        "None" clears the column regardless.
        """
        for r in range(self.table.rowCount()):
            it = self.table.item(r, col)
            if it is None:
                continue
            if checked and col in (self.C_AF, self.C_REFINE):
                mill = self.table.item(r, self.C_MILL)
                if mill is None or mill.checkState() != Qt.Checked:
                    it.setCheckState(Qt.Unchecked)
                    continue
            it.setCheckState(Qt.Checked if checked else Qt.Unchecked)

    def _checked_names(self, col) -> list:
        return [self.table.item(r, self.C_NAME).text()
                for r in range(self.table.rowCount())
                if self.table.item(r, col).checkState() == Qt.Checked]

    def build_argv(self):
        """Assemble the mill-positions argv from the current selections (or None)."""
        pos_file = self._win.positions_file()
        ely = self.ely_edit.text().strip()
        mill_names = self._checked_names(self.C_MILL)
        if not pos_file or not ely or not mill_names:
            return None
        args = ["mill-positions", ely, *mill_names, "--positions-file", pos_file,
                "--stage", self.stage,
                "--wait", self.wait_combo.currentText(),
                "--move-method", self.move_combo.currentText(),
                "--imaging-scan-rate", str(self.scan_spin.value())]
        af = [n for n in self._checked_names(self.C_AF) if n in mill_names]
        rf = [n for n in self._checked_names(self.C_REFINE) if n in mill_names]
        if af:
            args += ["--autofocus", ",".join(af)]
        if rf:
            args += ["--refine", ",".join(rf)]
        if rf and self.refine_stage_chk.isChecked():
            args += ["--refine-enable-stage"]
        if rf and self.refine_skip_chk.isChecked():
            args += ["--refine-abort-on-fail"]
        if self.continue_chk.isChecked():
            args += ["--continue-on-error"]
        if not self.after_chk.isChecked():
            args += ["--no-after-image"]           # --stage turns it on by default
        if self._win.is_armed():
            args += ["--arm"]
        return args

    def missing_references(self, names) -> dict:
        """{name: reason} for the positions without this stage's reference (Prepolish:
        Rough Mill after image; Polish: Prepolish after image). Rough: always {}."""
        if self._prev is None:
            return {}
        try:
            from ..mill_stages import plan_stage
            from ..positions import PositionDatabase
            db = PositionDatabase.load(Path(self._win.positions_file()))
            return dict(plan_stage(db, names, self.stage, self._session_dir()).missing)
        except Exception as exc:  # noqa: BLE001
            return {n: f"could not check ({exc})" for n in names}

    def _on_start(self) -> None:
        from ..mill_stages import STAGE_LABEL
        args = self.build_argv()
        if args is None:
            self._win.status("Need a positions file, a pattern, and at least one "
                             "checked position.")
            return
        for r in range(self.table.rowCount()):
            self.table.item(r, self.C_STATUS).setText("")
        # v0.51.0: positions without the previous stage's after image are listed first and
        # need an OK; they are not milled (the run is told with --accept-reference-skips).
        mill_names = self._checked_names(self.C_MILL)
        missing = self.missing_references(mill_names)
        if missing:
            prev = STAGE_LABEL[self._prev]
            for n in missing:
                self._set_status(n, f"Skipped (no {prev.lower()} image)")
            rest = [n for n in mill_names if n not in missing]
            if not rest:
                self._win.status(f"No checked position has a {prev} after image; nothing to "
                                 f"{self._label.lower()}.")
                return
            if "--arm" in args and not self._win.confirm(
                    f"These positions have no {prev} after image to align to and will NOT be "
                    f"{self._label.lower()}ed:\n\n  " + "\n  ".join(missing)
                    + f"\n\n{self._label} the other {len(rest)} position(s)?"):
                for n in missing:
                    self._set_status(n, "")
                self._win.status("Not started; nothing moved.")
                return
            args = args + ["--accept-reference-skips", ",".join(missing)]
        refined = set(self._checked_names(self.C_REFINE))
        unrefined = [n for n in mill_names if n not in missing and n not in refined]
        if self._forced and unrefined and "--arm" in args and not self._win.confirm(
                f"These positions are not ticked for Refine and will be "
                f"{self._label.lower()}ed WITHOUT refine:\n\n  " + "\n  ".join(unrefined)
                + f"\n\nThe pattern then lands at the recalled stage position with zero beam "
                  f"shift, not on the {STAGE_LABEL[self._prev]} milling. Continue?"):
            self._win.status("Not started; nothing moved.")
            return
        if "--arm" not in args or self.move_combo.currentText() == "none":
            self._win.run_cli(args, on_line=self._on_mill_line,
                              on_done=lambda rc: self.refresh_stage_columns())
            return
        # v0.46.0 batch tilt limit: a read-only --tilt-check first (no --arm), then an OK
        # for any position that will NOT be milled, then the armed run with exactly that
        # list in --accept-tilt-skips (the run refuses if anything else is out of range).
        self._tilt = None
        check = [a for a in args if a != "--arm"] + ["--tilt-check"]
        self._win.run_cli(check, on_line=self._on_tilt_line,
                          on_done=lambda rc, a=list(args): self._after_tilt_check(rc, a))

    def _on_tilt_line(self, line: str) -> None:
        from ..tilt_guard import parse_machine_line
        got = parse_machine_line(line)
        if got is not None:
            self._tilt = got

    def _after_tilt_check(self, rc: int, args: list) -> None:
        from ..tilt_guard import BATCH_TILT_LIMIT_DEG
        t = getattr(self, "_tilt", None)
        if rc != 0 or t is None:
            self._win.status("Tilt check failed (see Log); nothing moved.")
            return
        excluded, allowed = t["excluded"], t["allowed"]
        for n in excluded:
            self._set_status(n, "Skipped (tilt)")
        if not allowed:
            self._win.status(f"No checked position is within {BATCH_TILT_LIMIT_DEG:g}° of "
                             f"the current tilt ({t['live']:.2f}°); nothing to mill.")
            return
        if excluded:
            if not self._win.confirm(
                    f"The stage is at {t['live']:.2f}°. These positions are more than "
                    f"{BATCH_TILT_LIMIT_DEG:g}° from it (or from the previous site) and will "
                    f"NOT be milled:\n\n  " + "\n  ".join(excluded)
                    + f"\n\nMill the other {len(allowed)} position(s)?"):
                for n in excluded:
                    self._set_status(n, "")
                self._win.status("Not started; nothing moved.")
                return
            args = args + ["--accept-tilt-skips", ",".join(excluded)]
        w = getattr(self._win, "_worker", None)
        if w is not None and hasattr(w, "wait"):
            w.wait(5000)                     # the check's thread is finishing; let it end
        self._win.run_cli(args, on_line=self._on_mill_line,
                          on_done=lambda rc: self.refresh_stage_columns())

    def _set_status(self, name: str, text: str) -> None:
        r = self._row_by_name.get(name)
        if r is not None:
            self.table.item(r, self.C_STATUS).setText(text)

    def _on_mill_line(self, line: str) -> None:
        """Parse streamed CLI output to update per-position status live."""
        import re
        s = line.strip()
        for pat, label in ((r"Completed milling at\s+(\S+)", "Done"),
                           (r"\[stage\] NOT milling\s+(\S+)", "Skipped (no image)"),
                           (r"Milling at\s+(\S+)", "Milling…"),
                           (r"Autofocus at\s+(\S+)", "Autofocus…"),
                           (r"Refine at\s+(\S+)", "Refine…"),
                           (r"Error at\s+(\S+)", "Error")):
            m = re.search(pat, s)
            if m:
                self._set_status(m.group(1).rstrip(":,"), label)
                return

    # ------------------------------------------------------------ manual (v0.51.0)
    def selected_name(self) -> Optional[str]:
        rows = {i.row() for i in self.table.selectedItems()}
        if len(rows) != 1:
            return None
        it = self.table.item(rows.pop(), self.C_NAME)
        return it.text() if it is not None else None

    def _on_select(self) -> None:
        n = self.selected_name()
        self.sel_label.setText(f"Selected position: {n or '—'}")

    def site_argv(self, action: str):
        """argv for `site <action> <selected>` (or None, with a status message)."""
        name = self.selected_name()
        pos_file = self._win.positions_file()
        if not pos_file or not name:
            self._win.status("Select one position in the table first.")
            return None
        args = ["site", action, name, "--stage", self.stage, "--positions-file", pos_file]
        if action == "goto":
            args += ["--move-method", self.move_combo.currentText()]
        elif action == "align":
            args += ["--imaging-scan-rate", str(self.scan_spin.value())]
            if self.refine_stage_chk.isChecked():
                args += ["--refine-enable-stage"]
        elif action == "push":
            ely = self.ely_edit.text().strip()
            if not ely:
                self._win.status(f"Choose the {'standard' if not self._forced else self._label.lower()} "
                                 f"pattern first.")
                return None
            args += ["--ely", ely]
        if self._win.is_armed():
            args += ["--arm"]
        return args

    def _manual_question(self, action: str, name: str) -> str:
        from ..mill_stages import reference_for_stage, resolve_stage_pattern
        rec = self._db.positions.get(name) if self._db else None
        if action == "goto":
            t = getattr(getattr(rec, "stage_at", None), "t_deg", None)
            return (f"Move the stage to {name}"
                    + (f" (recorded tilt {t:.2f}°)" if t is not None else "")
                    + "?\n\nThe FIB beam shift is zeroed first; then the objective voltage and "
                      "FIB magnification are set.")
        if action == "align":
            ref = reference_for_stage(rec, self.stage, self._session_dir())[0] if rec else None
            return (f"Align {name} to {ref.name if ref else 'its reference'}?\n\nFIB frames are "
                    f"taken; the beam-shift correction stays on for milling.")
        if action == "push":
            try:
                path = resolve_stage_pattern(rec, self.stage, self.ely_edit.text().strip(),
                                             self._session_dir())[0]
                pname = Path(path).name
            except Exception:  # noqa: BLE001
                pname = Path(self.ely_edit.text().strip()).name
            return (f"Load {pname} into SmartFIB for {name}?\n\nMilling will NOT start: "
                    f"check it and start it in SmartFIB.")
        return (f"Grab the {self._label} after image of {name}?\n\nOne FIB frame at the "
                f"capture magnification; recorded in positions.json and the run log.")

    def _on_manual(self, action: str) -> None:
        args = self.site_argv(action)
        if args is None:
            return
        name = args[2]
        if "--arm" in args and not self._win.confirm(self._manual_question(action, name)):
            self._win.status("Cancelled; nothing changed.")
            return
        label = dict(self.MANUAL_ACTIONS)[action]
        self.manual_status.setText(f"{label} {name}…" + ("" if "--arm" in args else
                                                          " (dry run: plan in the Log)"))
        self._win.run_cli(args, on_line=self._on_manual_line,
                          on_done=lambda rc, a=action, n=name: self._after_manual(rc, a, n),
                          show_log=False)

    def _on_manual_line(self, line: str) -> None:
        s = line.strip()
        if s.startswith(("SITE-DONE", "[site]", "***", "[goto]", "[align]", "[push]",
                         "[after-image]", "[PUSH]", "[DRY RUN]")):
            self.manual_status.setText(s)

    def _after_manual(self, rc: int, action: str, name: str) -> None:
        label = dict(self.MANUAL_ACTIONS)[action]
        if rc == 0:
            self._win.status(f"{label} {name}: done.")
        else:
            self._win.status(f"{label} {name}: exit {rc} (see Log).")
        if action == "after-image":
            self.refresh_stage_columns()


class PositionsPage(QWidget):
    """Browse saved positions (in-process JSON read) with a BMP preview."""

    COLS = ["Name", "Mag (x)", "Obj (V)", "X (mm)", "Y (mm)", "Z (mm)",
            "px (nm)", "FOV w (µm)", "ELY", "Fid"]

    def __init__(self, window):
        super().__init__()
        self._win = window
        self._db = None
        self._base_dir: Optional[Path] = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.addWidget(_title("Positions"))

        row = QHBoxLayout()
        self.refresh_btn = QPushButton("Load / Refresh")
        self.refresh_btn.clicked.connect(self.reload)
        row.addWidget(self.refresh_btn)
        self.author_btn = QPushButton("Author pattern…")
        self.author_btn.setObjectName("toolButton")
        self.author_btn.clicked.connect(self._on_author_pattern)
        row.addWidget(self.author_btn)
        self.link_btn = QPushButton("Refresh patterns")
        self.link_btn.clicked.connect(self._on_refresh_patterns)
        row.addWidget(self.link_btn)
        # Fiducial: mill a reference mark left/right of the image at the selected
        # position (its own .ely per side; ARM-gated + confirmed).
        row.addSpacing(12)
        row.addWidget(QLabel("Fiducial:"))
        self.fid_side = QComboBox(); self.fid_side.addItems(["left", "right"])
        row.addWidget(self.fid_side)
        self.fid_af_chk = QCheckBox("autofocus")
        self.fid_rf_chk = QCheckBox("refine")
        row.addWidget(self.fid_af_chk); row.addWidget(self.fid_rf_chk)
        self.fid_btn = QPushButton("Mill fiducial")
        self.fid_btn.setObjectName("toolButton")
        self.fid_btn.clicked.connect(self._on_mill_fiducial)
        row.addWidget(self.fid_btn)
        self.count_lbl = QLabel("")
        self.count_lbl.setStyleSheet("color: palette(mid);")
        row.addWidget(self.count_lbl)
        row.addStretch(1)
        lay.addLayout(row)

        body = QHBoxLayout()
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._on_select)
        body.addWidget(self.table, 3)

        preview = QVBoxLayout()
        preview.addWidget(QLabel("Reference image"))
        self.img_lbl = QLabel("(select a position)")
        self.img_lbl.setAlignment(Qt.AlignCenter)
        self.img_lbl.setMinimumWidth(320)
        self.img_lbl.setFrameShape(QFrame.StyledPanel)
        self.img_lbl.setStyleSheet("background:#1c212b; color:#9aa3b2;")
        preview.addWidget(self.img_lbl, 1)
        body.addLayout(preview, 2)
        lay.addLayout(body, 1)

    def reload(self) -> None:
        pos_file = self._win.positions_file()
        if not pos_file:
            self._win.status("Set a positions file first (top bar).")
            return
        try:
            from ..positions import PositionDatabase
            self._db = PositionDatabase.load(Path(pos_file))
            self._base_dir = Path(pos_file).resolve().parent
        except Exception as exc:  # noqa: BLE001
            self._win.status(f"Could not load positions: {exc}")
            self.table.setRowCount(0)
            return
        names = sorted(self._db.positions.keys())
        self.table.setRowCount(len(names))
        for r, name in enumerate(names):
            rec = self._db.positions[name]
            st = rec.stage_at

            def _mm(v):
                return "" if v is None else f"{v * 1e3:.4f}"

            px_nm = "" if rec.fib_pixel_size_m is None else f"{rec.fib_pixel_size_m * 1e9:.2f}"
            fov_um = "" if rec.fov_width_m is None else f"{rec.fov_width_m * 1e6:.2f}"
            vals = [name, f"{rec.fib_magnification_x:.0f}",
                    f"{rec.fib_objective_potential_v:.1f}",
                    _mm(st.x_m), _mm(st.y_m), _mm(st.z_m),
                    px_nm, fov_um, "yes" if rec.ely_pattern else "—",
                    ((f"{rec.fiducial_side} #{rec.fiducial_number}"
                      if getattr(rec, "fiducial_number", None) else rec.fiducial_side)
                     or "—")]
            for c, v in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(v))
        self.count_lbl.setText(f"{len(names)} position(s)")
        self._win.status(f"Loaded {len(names)} position(s).")
        # Explicit pattern pickup (option b): link any ready-markers on load.
        self._link_patterns(quiet=True)

    def _selected_name(self):
        items = self.table.selectedItems()
        if not items or self._db is None:
            return None
        return self.table.item(items[0].row(), 0).text()

    def _on_author_pattern(self) -> None:
        pf = self._win.positions_file()
        if not pf or self._db is None:
            self._win.status("Load a positions file first.")
            return
        name = self._selected_name()
        if not name:
            self._win.status("Select a position to author a pattern for.")
            return
        rec = self._db.positions.get(name)
        try:
            from ..ely_link import build_handoff_kwargs
            kwargs = build_handoff_kwargs(rec, pf)
        except Exception as exc:  # noqa: BLE001
            self._win.status(f"Cannot author for '{name}': {exc}")
            return
        # Hand the image + geometry to the embedded ELYMaker tab and switch to it.
        self._win.elymaker_page.open_handoff(**kwargs)
        self._win.show_page("elymaker")
        self._win.status(f"ELYMaker: authoring for '{name}'. Save/Send there, then "
                         f"'Refresh patterns' here to link it.")

    def _on_mill_fiducial(self) -> None:
        """Mill the left/right fiducial .ely at the selected position via the standard
        mill path (recall -> arrival wait -> [autofocus] -> [refine] -> mill), forcing
        the fiducial pattern (never the position's authored lamella pattern) and
        recording fiducial_side on success."""
        pf = self._win.positions_file()
        if not pf or self._db is None:
            self._win.status("Load a positions file first.")
            return
        name = self._selected_name()
        if not name:
            self._win.status("Select a position to mill a fiducial at.")
            return
        side = self.fid_side.currentText()
        from ..fiducial import fiducial_ely_path, fiducial_available
        if not fiducial_available(side):
            self._win.status(f"No {side} fiducial pattern found. Drop it in as "
                             f"{fiducial_ely_path(side)}")
            return
        # Will the fiducial fit in the FOV at this position's mag? (The prototype
        # fiducials reach ~28 um from centre; the FOV half-width at 2000x is ~28.6 um,
        # so any higher mag clips them.)
        from ..fiducial import fiducial_fit
        rec = self._db.positions.get(name)
        fits, margin, detail = fiducial_fit(side, rec.fib_magnification_x)
        if not fits:
            if not self._win.confirm(
                    "WARNING: the {} fiducial will NOT fit in the field of view at this "
                    "position's magnification ({}). It would be clipped or not milled. "
                    "Mill anyway?".format(side, detail)):
                return
        armed = self._win.is_armed()
        if armed and not self._win.confirm(
                "Mill a {} fiducial at '{}'? This moves the stage to the position and "
                "mills the fiducial pattern (not the lamella pattern).\n\nNOTE: this "
                "position's reference image was captured WITHOUT the fiducial, so "
                "refine will NOT see it. Re-capture the position afterwards (Capture "
                "tab, same name, overwrite) to update the reference. To avoid this, "
                "mill the fiducial BEFORE capturing (Capture tab).".format(side, name)):
            return
        args = ["mill-positions", str(fiducial_ely_path(side)), name,
                "--positions-file", pf, "--force-pattern", "--record-fiducial", side]
        if self.fid_af_chk.isChecked():
            args += ["--autofocus", name]
        if self.fid_rf_chk.isChecked():
            args += ["--refine", name]
        if armed:
            args.append("--arm")
        self._win.run_cli(args)

    def _on_refresh_patterns(self) -> None:
        self._link_patterns(quiet=False)
        self.reload()  # redraw the ELY column

    def _link_patterns(self, *, quiet: bool) -> None:
        pf = self._win.positions_file()
        if not pf or self._db is None:
            return
        try:
            from ..ely_link import link_patterns_to_positions
            from ..positions import PositionDatabase
            # v0.51.0: link on a FRESH copy -- a batch may have recorded after images in
            # positions.json since this page loaded it; saving the old copy would drop them.
            self._db = PositionDatabase.load(Path(pf))
            summary = link_patterns_to_positions(self._db, pf)
        except Exception as exc:  # noqa: BLE001
            if not quiet:
                self._win.status(f"Pattern refresh failed: {exc}")
            return
        if summary["linked"]:
            try:
                self._db.save(Path(pf))   # persist ely_pattern links
            except Exception:
                pass
        if not quiet:
            msg = f"Linked {len(summary['linked'])} pattern(s)."
            if summary["stale"]:
                msg += (f" WARNING: {len(summary['stale'])} edited since marked ready "
                        f"({', '.join(summary['stale'])}).")
            if summary["unmatched"]:
                msg += f" {len(summary['unmatched'])} marker(s) had no matching position."
            self._win.status(msg)

    def _on_select(self) -> None:
        items = self.table.selectedItems()
        if not items or self._db is None:
            return
        name = self.table.item(items[0].row(), 0).text()
        rec = self._db.positions.get(name)
        if rec is None or not rec.image_bmp:
            self.img_lbl.setText("(no reference image)")
            self.img_lbl.setPixmap(QPixmap())
            return
        p = Path(rec.image_bmp)
        if not p.is_absolute() and self._base_dir is not None:
            p = (self._base_dir / p)
        if not p.exists():
            self.img_lbl.setText(f"(image not found)\n{p}")
            self.img_lbl.setPixmap(QPixmap())
            return
        pm = QPixmap(str(p))
        if pm.isNull():
            self.img_lbl.setText(f"(could not load)\n{p}")
            return
        self.img_lbl.setPixmap(
            pm.scaled(self.img_lbl.width(), self.img_lbl.height(),
                      Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.img_lbl.setText("")
