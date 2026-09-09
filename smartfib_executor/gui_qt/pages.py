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
        self.tilt_chk = QCheckBox("Also record tilt (AP_STAGE_AT_T)")
        form.addRow("", self.tilt_chk)
        lay.addLayout(form)

        row = QHBoxLayout()
        self.capture_btn = QPushButton("Capture")
        self.capture_btn.setObjectName("toolButton")
        self.capture_btn.clicked.connect(self._on_capture)
        row.addWidget(self.capture_btn)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)

    def _on_capture(self) -> None:
        name = self.name_edit.text().strip() or "position_1"
        pos_file = self._win.positions_file()
        if not pos_file:
            self._win.status("Set a positions file first (top bar).")
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
        if self.tilt_chk.isChecked():
            args += ["--include-tilt"]
        if self._win.is_armed():
            args += ["--arm"]
        self._win.run_cli(args)


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

    Reuses PositionDatabase for the queue rows. Assembles the mill-positions argv from
    the checked rows + options, gating --arm on the shared ARM toggle, and updates a
    live per-position status column by parsing the streamed CLI output.
    """

    C_MILL, C_AF, C_REFINE, C_NAME, C_STATUS, C_MAG, C_OBJ = range(7)
    HEADERS = ["Mill", "AF", "Refine", "Position", "Status", "Mag (x)", "Obj (V)"]

    def __init__(self, window):
        super().__init__()
        self._win = window
        self._db = None
        self._row_by_name = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.addWidget(_title("Mill"))

        # pattern (.ely) picker
        prow = QHBoxLayout()
        prow.addWidget(QLabel("Standard pattern (.ely):"))
        self.ely_edit = QLineEdit()
        self.ely_edit.setPlaceholderText("path to pattern .ely")
        prow.addWidget(self.ely_edit, 1)
        b = QPushButton("Browse…")
        b.clicked.connect(self._browse_ely)
        prow.addWidget(b)
        lay.addLayout(prow)
        hint = QLabel("Positions with an authored pattern mill that instead of the "
                      "standard one.")
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
        self.move_combo.addItems(["ap_goto", "movestage", "none"])
        orow.addWidget(self.move_combo)
        orow.addWidget(QLabel("Imaging scan rate:"))
        self.scan_spin = QSpinBox(); self.scan_spin.setRange(-1, 15)
        self.scan_spin.setValue(8)
        orow.addWidget(self.scan_spin)
        self.continue_chk = QCheckBox("Continue on error")
        orow.addWidget(self.continue_chk)
        orow.addStretch(1)
        lay.addLayout(orow)

        # select helpers
        srow = QHBoxLayout()
        self.refresh_btn = QPushButton("Load / Refresh")
        self.refresh_btn.clicked.connect(self.reload)
        srow.addWidget(self.refresh_btn)
        for label, col in (("all", None), ("autofocus", self.C_AF),
                           ("refine", self.C_REFINE)):
            btn = QPushButton(f"Toggle {label}")
            btn.clicked.connect(lambda _=False, c=col: self._toggle_col(c))
            srow.addWidget(btn)
        srow.addStretch(1)
        lay.addLayout(srow)

        # queue table
        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for c in (self.C_MILL, self.C_AF, self.C_REFINE):
            self.table.horizontalHeader().setSectionResizeMode(
                c, QHeaderView.ResizeToContents)
        lay.addWidget(self.table, 1)

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
        names = sorted(self._db.positions.keys())
        self._row_by_name = {n: i for i, n in enumerate(names)}
        self.table.setRowCount(len(names))
        for r, name in enumerate(names):
            rec = self._db.positions[name]
            self.table.setItem(r, self.C_MILL, self._mk_check(True))
            self.table.setItem(r, self.C_AF, self._mk_check(False))
            self.table.setItem(r, self.C_REFINE, self._mk_check(False))
            self.table.setItem(r, self.C_NAME, QTableWidgetItem(name))
            self.table.setItem(r, self.C_STATUS, QTableWidgetItem(""))
            self.table.setItem(r, self.C_MAG,
                               QTableWidgetItem(f"{rec.fib_magnification_x:.0f}"))
            self.table.setItem(r, self.C_OBJ,
                               QTableWidgetItem(f"{rec.fib_objective_potential_v:.1f}"))
        self._win.status(f"Queue: {len(names)} position(s).")

    def _toggle_col(self, col) -> None:
        cols = (self.C_MILL, self.C_AF, self.C_REFINE) if col is None else (col,)
        for r in range(self.table.rowCount()):
            for c in cols:
                it = self.table.item(r, c)
                it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked
                                 else Qt.Checked)

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
                "--wait", self.wait_combo.currentText(),
                "--move-method", self.move_combo.currentText(),
                "--imaging-scan-rate", str(self.scan_spin.value())]
        af = [n for n in self._checked_names(self.C_AF) if n in mill_names]
        rf = [n for n in self._checked_names(self.C_REFINE) if n in mill_names]
        if af:
            args += ["--autofocus", ",".join(af)]
        if rf:
            args += ["--refine", ",".join(rf)]
        if self.continue_chk.isChecked():
            args += ["--continue-on-error"]
        if self._win.is_armed():
            args += ["--arm"]
        return args

    def _on_start(self) -> None:
        args = self.build_argv()
        if args is None:
            self._win.status("Need a positions file, a pattern, and at least one "
                             "checked position.")
            return
        for r in range(self.table.rowCount()):
            self.table.item(r, self.C_STATUS).setText("")
        self._win.run_cli(args, on_line=self._on_mill_line)

    def _set_status(self, name: str, text: str) -> None:
        r = self._row_by_name.get(name)
        if r is not None:
            self.table.item(r, self.C_STATUS).setText(text)

    def _on_mill_line(self, line: str) -> None:
        """Parse streamed CLI output to update per-position status live."""
        import re
        s = line.strip()
        for pat, label in ((r"Completed milling at\s+(\S+)", "Done"),
                           (r"Milling at\s+(\S+)", "Milling…"),
                           (r"Autofocus at\s+(\S+)", "Autofocus…"),
                           (r"Refine at\s+(\S+)", "Refine…"),
                           (r"Error at\s+(\S+)", "Error")):
            m = re.search(pat, s)
            if m:
                self._set_status(m.group(1).rstrip(":,"), label)
                return


class PositionsPage(QWidget):
    """Browse saved positions (in-process JSON read) with a BMP preview."""

    COLS = ["Name", "Mag (x)", "Obj (V)", "X (mm)", "Y (mm)", "Z (mm)",
            "px (nm)", "FOV w (µm)", "ELY"]

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
                    px_nm, fov_um, "yes" if rec.ely_pattern else "—"]
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

    def _on_refresh_patterns(self) -> None:
        self._link_patterns(quiet=False)
        self.reload()  # redraw the ELY column

    def _link_patterns(self, *, quiet: bool) -> None:
        pf = self._win.positions_file()
        if not pf or self._db is None:
            return
        try:
            from ..ely_link import link_patterns_to_positions
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
