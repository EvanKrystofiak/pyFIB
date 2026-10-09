"""Main editor window: menus, tool toolbar, properties + defaults + validation
docks, and file / image actions.

Everything routes through the v0.1.0 core for I/O and validation; the GUI only
adds an editing surface. The window never touches the instrument.
"""

from __future__ import annotations

import os
from typing import Optional

from qtpy.QtCore import Qt
from qtpy.QtGui import QKeySequence, QPixmap
from qtpy.QtWidgets import (
    QAction,
    QActionGroup,
    QButtonGroup,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..reader import read_ely
from ..writer import write_ely
from ..validate import validate
from ..probes import PROBES, get_probe, probe_names_by_current
from ..model import Exposure
from .scene import PatternScene
from .canvas import PatternView, ShapeDefaults, SELECT, HAND, RECT, RBOX, POLYGON, ARC, TEXT
from .properties import PropertiesPanel
from .calibration_dialog import CalibrationDialog
from .undo import UndoManager
from .items import offset_shape_model, mirror_shape_model, layout_bbox, item_from_shape

_DEFAULT_RECIPE = r"D:\FIB Recipes\Vitriuos_Ice.epm"


class DefaultsPanel(QWidget):
    """Controls the parameters applied to newly-drawn shapes."""

    def __init__(self, defaults: ShapeDefaults, parent=None) -> None:
        super().__init__(parent)
        self._defaults = defaults
        form = QFormLayout(self)

        self.depth = QDoubleSpinBox()
        self.depth.setRange(0.001, 500); self.depth.setDecimals(4)
        self.depth.setValue(defaults.depth_um)
        form.addRow("Depth (um)", self.depth)

        self.probe = QComboBox()
        self.probe.addItem("(recipe default)")
        for name in probe_names_by_current():
            self.probe.addItem(name)
        self.probe.setCurrentText("30kV:15nA")
        form.addRow("Probe", self.probe)

        self.recipe = QLineEdit(_DEFAULT_RECIPE)
        form.addRow("Recipe", self.recipe)

        self.text = QLineEdit(defaults.text)
        form.addRow("Text", self.text)

        # rounded-box options
        self.round_side = QComboBox()
        self.round_side.addItems(["top", "bottom", "left", "right"])
        self.round_side.setCurrentText(defaults.rounded_side)
        form.addRow("Round side", self.round_side)

        self.round_radius = QDoubleSpinBox()
        self.round_radius.setRange(0.0, 100000); self.round_radius.setDecimals(4)
        self.round_radius.setValue(defaults.rounded_radius_um)
        self.round_radius.setToolTip("Corner radius in um. 0 = auto (10% of the "
                                     "shorter side).")
        form.addRow("Round radius (0=auto)", self.round_radius)

        self.round_segments = QSpinBox()
        self.round_segments.setRange(2, 200)
        self.round_segments.setValue(defaults.rounded_segments)
        self.round_segments.setToolTip("Points per rounded corner (smoothness).")
        form.addRow("Corner points", self.round_segments)

        self.depth.valueChanged.connect(self._sync)
        self.probe.currentIndexChanged.connect(self._sync)
        self.recipe.editingFinished.connect(self._sync)
        self.text.editingFinished.connect(self._sync)
        self.round_side.currentIndexChanged.connect(self._sync)
        self.round_radius.valueChanged.connect(self._sync)
        self.round_segments.valueChanged.connect(self._sync)
        self._sync()

    def _sync(self, *args) -> None:
        d = self._defaults
        d.depth_um = self.depth.value()
        d.text = self.text.text() or "X"
        d.rounded_side = self.round_side.currentText()
        d.rounded_radius_um = self.round_radius.value()
        d.rounded_segments = self.round_segments.value()
        recipe = self.recipe.text().strip()
        probe_name = self.probe.currentText()

        def factory() -> Optional[Exposure]:
            probe = get_probe(probe_name) if probe_name in PROBES else None
            return Exposure(file=recipe, probe=probe)

        d.exposure_factory = factory


class MainWindow(QMainWindow):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("elymaker %s" % __version__)
        self.resize(1200, 800)
        self._path: Optional[str] = None

        self.scene = PatternScene(self)
        self.view = PatternView(self.scene, self)
        self.setCentralWidget(self.view)

        self.defaults = ShapeDefaults()
        self.view.set_defaults(self.defaults)
        self._last_lamella = []      # items from the last lamella generation
        self._target_site = None     # position/site id from a handoff job (marker)
        self._drop_override = None    # handoff out path (overrides global drop)

        self._make_docks()
        self._make_actions()
        self._make_statusbar()

        # undo/redo: snapshot per committed action, depth 5, images excluded
        self.undo_mgr = UndoManager(self.scene, depth=5)
        self.undo_mgr.on_change = self._update_undo_buttons

        self.view.cursorMoved.connect(self._on_cursor)
        self.view.shapeCreated.connect(self._on_shape_created)
        self.view.shapeMoved.connect(self._on_shape_moved)
        self.view.drawSizeChanged.connect(self._on_draw_size)
        self.scene.selectionShapeChanged.connect(self.props.set_item)
        self.scene.selectionChanged.connect(self._update_measure)
        self.props.changed.connect(self._on_property_changed)

        self._update_undo_buttons()
        self._revalidate()

    # -- docks ------------------------------------------------------------ #
    def _make_docks(self) -> None:
        # Tools: vertical button column on the right, above the panels.
        self._make_tools_dock()

        self.props = PropertiesPanel(self)
        dock = QDockWidget("Properties", self)
        dock.setObjectName("dock_properties")
        dock.setWidget(self.props)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)

        self.defaults_panel = DefaultsPanel(self.defaults, self)
        ddock = QDockWidget("New-shape defaults", self)
        ddock.setObjectName("dock_defaults")
        ddock.setWidget(self.defaults_panel)
        self.addDockWidget(Qt.RightDockWidgetArea, ddock)

        self.validation = QPlainTextEdit()
        self.validation.setReadOnly(True)
        self.validation.setMaximumHeight(120)
        vdock = QDockWidget("Validation", self)
        vdock.setObjectName("dock_validation")
        vdock.setWidget(self.validation)
        self.addDockWidget(Qt.BottomDockWidgetArea, vdock)

    def _make_tools_dock(self) -> None:
        panel = QWidget(self)
        panel.setObjectName("toolsPanel")
        col = QVBoxLayout(panel)
        col.setContentsMargins(8, 8, 8, 8)
        col.setSpacing(6)

        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)
        tools = [
            (SELECT, "Select", "\u25B8", "1"),   # ▸
            (HAND, "Pan", "\u270B", "H"),        # ✋
            (RECT, "Rectangle", "\u25AD", "2"),  # ▭
            (RBOX, "Rounded box", "\u25A2", "6"),# ▢
            (POLYGON, "Polygon", "\u2B20", "3"), # ⬠
            (ARC, "Arc", "\u25DC", "4"),         # ◜
            (TEXT, "Text", "T", "5"),
        ]
        for tool, label, glyph, key in tools:
            b = QToolButton(panel)
            b.setObjectName("toolButton")
            b.setCheckable(True)
            b.setText("  %s   %s" % (glyph, label))
            b.setToolButtonStyle(Qt.ToolButtonTextOnly)
            b.setShortcut(key)
            b.setToolTip("%s tool (%s)" % (label, key))
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.setMinimumHeight(38)
            b.clicked.connect(lambda _=False, t=tool: self.view.set_tool(t))
            self._tool_group.addButton(b)
            col.addWidget(b)
            if tool == SELECT:
                b.setChecked(True)

        # -- edit actions (momentary buttons, not part of the tool group) -- #
        line = QFrame(panel)
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color:#3a4150;")
        col.addSpacing(4)
        col.addWidget(line)

        def edit_btn(label, glyph, slot, key):
            b = QToolButton(panel)
            b.setText("  %s   %s" % (glyph, label))
            b.setToolButtonStyle(Qt.ToolButtonTextOnly)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.setMinimumHeight(34)
            b.setToolTip("%s (%s)" % (label, key))
            b.clicked.connect(slot)
            col.addWidget(b)
            return b

        self._btn_undo = edit_btn("Undo", "\u21B6", self.do_undo, "Ctrl+Z")      # ↶
        self._btn_redo = edit_btn("Redo", "\u21B7", self.do_redo, "Ctrl+Shift+Z")  # ↷
        self._btn_dup = edit_btn("Duplicate", "\u29C9", self.duplicate_selected, "Ctrl+D")  # ⧉
        self._btn_round = edit_btn("Round corners", "\u25A2", self.round_selected, "Ctrl+R")  # ▢
        self._btn_mirx = edit_btn("Mirror X", "\u2195", lambda: self.mirror_selected("x"), "Ctrl+Shift+X")  # ↕
        self._btn_miry = edit_btn("Mirror Y", "\u2194", lambda: self.mirror_selected("y"), "Ctrl+Shift+Y")  # ↔

        col.addStretch(1)
        dock = QDockWidget("Tools", self)
        dock.setObjectName("dock_tools")
        dock.setWidget(panel)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        self._tools_dock = dock

    # -- actions / menus -------------------------------------------------- #
    def _make_actions(self) -> None:
        m = self.menuBar()
        filem = m.addMenu("&File")

        def act(text, slot, shortcut=None):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(shortcut)
            a.triggered.connect(slot)
            return a

        filem.addAction(act("&New", self.new_layout, QKeySequence.New))
        filem.addAction(act("&Open .ely...", self.open_file, QKeySequence.Open))
        filem.addAction(act("&Save", self.save_file, QKeySequence.Save))
        filem.addAction(act("Save &As...", self.save_file_as, QKeySequence.SaveAs))
        filem.addSeparator()
        filem.addAction(act("Load FIB &image...", self.load_image))
        filem.addAction(act("Export &SVG preview...", self.export_svg))
        filem.addSeparator()
        filem.addAction(act("E&xit", self.close, QKeySequence.Quit))

        editm = m.addMenu("&Edit")
        editm.addAction(act("&Undo", self.do_undo, QKeySequence.Undo))
        editm.addAction(act("&Redo", self.do_redo, QKeySequence("Ctrl+Shift+Z")))
        editm.addSeparator()
        editm.addAction(act("D&uplicate", self.duplicate_selected, QKeySequence("Ctrl+D")))
        editm.addAction(act("Round &corners of selected", self.round_selected,
                            QKeySequence("Ctrl+R")))
        editm.addAction(act("Mirror across &X axis (copy)",
                            lambda: self.mirror_selected("x"), QKeySequence("Ctrl+Shift+X")))
        editm.addAction(act("Mirror across &Y axis (copy)",
                            lambda: self.mirror_selected("y"), QKeySequence("Ctrl+Shift+Y")))
        editm.addAction(act("&Delete selected", self.delete_selected,
                            QKeySequence.Delete))
        editm.addSeparator()
        editm.addAction(act("Fit view", self.view.fit_all, QKeySequence("Ctrl+0")))
        editm.addAction(act("Zoom to selection", self.view.zoom_to_selection,
                            QKeySequence("Ctrl+Shift+0")))

        viewm = m.addMenu("&View")
        self._act_color_depth = QAction("Colour by depth", self, checkable=True)
        self._act_color_current = QAction("Colour by probe current", self, checkable=True)
        grp = QActionGroup(self); grp.setExclusive(True)
        grp.addAction(self._act_color_depth); grp.addAction(self._act_color_current)
        self._act_color_current.setChecked(True)
        self._act_color_depth.triggered.connect(lambda: self._set_color_mode("depth"))
        self._act_color_current.triggered.connect(lambda: self._set_color_mode("current"))
        viewm.addAction(self._act_color_depth)
        viewm.addAction(self._act_color_current)
        viewm.addSeparator()
        self._act_show_order = QAction("Show mill order", self, checkable=True)
        self._act_show_order.setShortcut(QKeySequence("Ctrl+M"))
        self._act_show_order.triggered.connect(
            lambda on: self.view.set_show_order(on))
        viewm.addAction(self._act_show_order)

        patm = m.addMenu("&Pattern")
        patm.addAction(act("New &lamella...", self.new_lamella, QKeySequence("Ctrl+L")))

        sfm = m.addMenu("&SmartFIB")
        sfm.addAction(act("&Send pattern to SmartFIB", self.send_to_smartfib,
                          QKeySequence("Ctrl+E")))
        sfm.addAction(act("Set SmartFIB &drop location...", self.set_smartfib_drop))

    def _make_statusbar(self) -> None:
        self.pos_label = QLabel("x=--  y=--  um")
        self.statusBar().addPermanentWidget(self.pos_label)
        self.measure_label = QLabel("")
        self.statusBar().addPermanentWidget(self.measure_label)
        self.count_label = QLabel("0 shapes")
        self.statusBar().addWidget(self.count_label)

    # -- slots ------------------------------------------------------------ #
    # -- SmartFIB drop / send --------------------------------------------- #
    def set_smartfib_drop(self) -> str:
        from . import config
        current = config.get("smartfib_drop_path", "")
        path, _ = QFileDialog.getSaveFileName(
            self, "SmartFIB drop location (the .ely SmartFIB loads)",
            current, "ELAYOUT files (*.ely);;All files (*)")
        if not path:
            return current
        if not path.lower().endswith(".ely"):
            path += ".ely"
        config.set("smartfib_drop_path", path)
        self.statusBar().showMessage("SmartFIB drop location set: %s" % path, 5000)
        return path

    def export_to_smartfib(self, path: str) -> bool:
        """Write the current pattern to *path* for SmartFIB to load. Returns ok.
        Dialog-free (the interactive send_to_smartfib handles messaging). Does
        NOT start milling -- that stays with pyFIB (mag/scan-rotation guards)."""
        layout = self.scene.to_layout()
        if layout.shape_count() == 0:
            return False
        try:
            write_ely(layout, path)
        except Exception:
            return False
        return True

    def send_to_smartfib(self) -> None:
        from . import config
        n = self.scene.to_layout().shape_count()
        if n == 0:
            QMessageBox.warning(self, "Send to SmartFIB", "The pattern is empty.")
            return
        path = self._drop_override or config.get("smartfib_drop_path", "")
        if not path:
            path = self.set_smartfib_drop()
            if not path:
                return
        resp = QMessageBox.question(
            self, "Send to SmartFIB",
            "Write %d shapes to the SmartFIB drop file?\n\n%s\n\n"
            "This makes the pattern available for SmartFIB to load. It does NOT "
            "start milling." % (n, path),
            QMessageBox.Ok | QMessageBox.Cancel)
        if resp != QMessageBox.Ok:
            return
        if self.export_to_smartfib(path):
            marker = ""
            try:
                from ..handoff import write_ready_marker
                mp = write_ready_marker(path, target_site=self._target_site)
                marker = "  (+ready marker)"
            except Exception:
                pass
            self.statusBar().showMessage("Sent %d shapes to SmartFIB drop: %s%s"
                                         % (n, os.path.basename(path), marker), 6000)
        else:
            QMessageBox.critical(self, "Send to SmartFIB",
                                 "Could not write to:\n%s" % path)

    def _on_cursor(self, x: float, y: float) -> None:
        self.pos_label.setText("x=%.3f  y=%.3f  um" % (x, y))

    def _on_draw_size(self, w: float, h: float) -> None:
        if w <= 0 and h <= 0:
            self._update_measure()
        else:
            self.measure_label.setText("drawing  %.2f \u00d7 %.2f um" % (w, h))

    def _update_measure(self) -> None:
        sel = [it for it in self.scene.shape_items() if it.isSelected()]
        if not sel:
            self.measure_label.setText("")
            return
        boxes = [layout_bbox(it.to_model()) for it in sel]
        x0 = min(b[0] for b in boxes); y0 = min(b[1] for b in boxes)
        x1 = max(b[2] for b in boxes); y1 = max(b[3] for b in boxes)
        w, h = x1 - x0, y1 - y0
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        tag = "%d sel" % len(sel) if len(sel) > 1 else ""
        self.measure_label.setText("%s  %.2f \u00d7 %.2f um  @ (%.2f, %.2f)"
                                   % (tag, w, h, cx, cy))

    # -- lamella generator ------------------------------------------------ #
    def new_lamella(self, _checked=False, spec=None) -> None:
        from .lamella_dialog import LamellaDialog
        from .items import item_from_shape
        from ..lamella import build_lamella
        # seed the dialog with the current default depth/recipe
        recipe = None
        exp = self.defaults.exposure_factory()
        if exp is not None:
            recipe = exp.file or None
        dlg = LamellaDialog(self, depth_um=self.defaults.depth_um,
                            recipe=recipe, spec=spec)
        if dlg.exec_() != dlg.Accepted:
            return
        spec = dlg.spec()
        # replace a previously generated lamella if asked
        if dlg.replace_previous and self._last_lamella:
            for it in self._last_lamella:
                if it.scene() is self.scene:
                    self.scene.removeItem(it)
            self._last_lamella = []
        new_items = []
        for rect_model in build_lamella(spec):
            it = item_from_shape(rect_model)
            self.scene.add_shape_item(it)
            new_items.append(it)
        self._last_lamella = new_items
        self.scene.clearSelection()
        for it in new_items:
            it.setSelected(True)
        self.props.set_item(None)
        self.undo_mgr.commit()
        self.view.fit_all()
        self._revalidate()

    # -- mirror ----------------------------------------------------------- #
    def mirror_selected(self, axis: str) -> None:
        """Duplicate the selection reflected across the layout x- or y-axis
        (through the origin) -- for building symmetric lamella pairs."""
        sel = [it for it in self.scene.shape_items() if it.isSelected()]
        if not sel:
            return
        copies = []
        for it in list(self.scene.shape_items()):
            if it not in sel:
                continue
            mirrored = mirror_shape_model(it.to_model(), axis)
            new_item = item_from_shape(mirrored)
            self.scene.insert_shape_item_after(it, new_item)
            copies.append(new_item)
        for it in sel:
            it.setSelected(False)
        for c in copies:
            c.setSelected(True)
        self.undo_mgr.commit()
        self._revalidate()

    def _on_shape_created(self, item) -> None:
        self.scene.clearSelection()
        item.setSelected(True)
        self.props.set_item(item)
        self.undo_mgr.commit()
        self._revalidate()

    def _on_shape_moved(self) -> None:
        # a drag-move finished: one undo step, and refresh the panel's x/y
        self.undo_mgr.commit()
        sel = [it for it in self.scene.selectedItems()
               if it in self.scene.shape_items()]
        if len(sel) == 1:
            self.props.set_item(sel[0])
        self._revalidate()

    def _on_property_changed(self) -> None:
        self.undo_mgr.commit()
        self._revalidate()

    # -- undo / redo ------------------------------------------------------ #
    def _set_color_mode(self, mode: str) -> None:
        from .items import set_color_mode
        set_color_mode(mode)
        for it in self.scene.shape_items():
            it.refresh_style()
            it.update()
        self.view.refresh_overlays()

    def _update_undo_buttons(self) -> None:
        self._btn_undo.setEnabled(self.undo_mgr.can_undo())
        self._btn_redo.setEnabled(self.undo_mgr.can_redo())

    def do_undo(self) -> None:
        self.undo_mgr.undo()
        self._after_history_change()

    def do_redo(self) -> None:
        self.undo_mgr.redo()
        self._after_history_change()

    def _after_history_change(self) -> None:
        self.props.set_item(None)
        self._revalidate()

    # -- round corners of existing rectangles ----------------------------- #
    def round_selected(self) -> None:
        from ..geometry import rounded_rect_polygon
        from .coords import layout_to_scene
        from .items import PolygonItem, RectItem
        rects = [it for it in self.scene.shape_items()
                 if isinstance(it, RectItem) and it.isSelected()]
        if not rects:
            return
        new_items = []
        for rect in rects:
            m = rect.to_model()
            r = self.defaults.effective_radius(m.width, m.height)
            pts = rounded_rect_polygon(
                m.x, m.y, m.width, m.height, r,
                self.defaults.rounded_side, self.defaults.rounded_segments)
            poly = PolygonItem([layout_to_scene(px, py) for px, py in pts])
            poly.dose_factor = rect.dose_factor      # carry depth / exposure
            poly.z_extent_m = rect.z_extent_m
            poly.exposure = rect.exposure
            marked_dir = rect.marked_edge_dir()
            self.scene.replace_shape_item(rect, poly)
            if marked_dir:                            # preserve the marked side
                poly.set_marked_edge_dir(marked_dir)
            new_items.append(poly)
        for it in new_items:
            it.setSelected(True)
        if len(new_items) == 1:
            self.props.set_item(new_items[0])
        self.undo_mgr.commit()
        self._revalidate()

    # -- duplicate -------------------------------------------------------- #
    def duplicate_selected(self) -> None:
        sel = [it for it in self.scene.shape_items() if it.isSelected()]
        if not sel:
            return
        # place each copy right after its original in mill order, offset a few
        # um down-right (layout: +x right, -y down)
        DX, DY = 5.0, -5.0
        copies = []
        for it in list(self.scene.shape_items()):   # snapshot (ordered)
            if it not in sel:
                continue
            copy_model = offset_shape_model(it.to_model(), DX, DY)
            new_item = item_from_shape(copy_model)
            self.scene.insert_shape_item_after(it, new_item)
            copies.append(new_item)
        for it in sel:
            it.setSelected(False)
        for c in copies:
            c.setSelected(True)
        if len(copies) == 1:
            self.props.set_item(copies[0])
        self.undo_mgr.commit()
        self._revalidate()

    def _revalidate(self) -> None:
        try:
            layout = self.scene.to_layout()
        except Exception as exc:  # pragma: no cover - defensive
            self.validation.setPlainText("cannot build layout: %s" % exc)
            return
        n = layout.shape_count()
        self.count_label.setText("%d shapes" % n)
        issues = validate(layout)
        if not issues:
            self.validation.setPlainText("OK: no issues (%d shapes)" % n)
        else:
            self.validation.setPlainText("\n".join(issues))
        self._update_measure()

    # -- file ops --------------------------------------------------------- #
    def new_layout(self) -> None:
        self.scene.clear_shapes()
        self.scene.clear_image()
        self.scene.load_layout(PatternScene._blank_template())
        self._path = None
        self.setWindowTitle("elymaker %s - (untitled)" % __version__)
        self.undo_mgr.reset()
        self._revalidate()

    def open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open .ely", "", "ELAYOUT files (*.ely);;All files (*)")
        if not path:
            return
        try:
            layout = read_ely(path)
        except Exception as exc:
            QMessageBox.critical(self, "Open failed", str(exc))
            return
        self.scene.load_layout(layout)
        self._path = path
        self.setWindowTitle("elymaker %s - %s" % (__version__, os.path.basename(path)))
        self.view.fit_all()
        self.undo_mgr.reset()
        self._revalidate()

    def save_file(self) -> None:
        if not self._path:
            self.save_file_as()
            return
        self._write(self._path)

    def save_file_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save .ely", "", "ELAYOUT files (*.ely)")
        if not path:
            return
        if not path.lower().endswith(".ely"):
            path += ".ely"
        self._path = path
        self._write(path)

    def _write(self, path: str) -> None:
        layout = self.scene.to_layout()
        layout.name = os.path.splitext(os.path.basename(path))[0]
        try:
            write_ely(layout, path)
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self.setWindowTitle("elymaker %s - %s" % (__version__, os.path.basename(path)))
        self.statusBar().showMessage("Saved %s" % path, 4000)

    # -- pyFIB handoff ---------------------------------------------------- #
    def open_handoff(self, image_path, pixel_size_um, out_path,
                     load_path=None, fov_width_um=None, site=None) -> None:
        """Open preloaded from a pyFIB job: calibrated image centred on the FOV
        centre (layout origin), optional existing pattern, Save -> *out_path*."""
        from qtpy.QtGui import QPixmap
        from .coords import ImageCalibration
        self._target_site = site
        self._drop_override = out_path      # in handoff mode, send targets `out`
        pix = QPixmap(image_path)
        if pix.isNull():
            QMessageBox.critical(self, "Handoff", "Could not load image:\n%s" % image_path)
            return
        if load_path and os.path.exists(load_path):
            self.scene.load_layout(read_ely(load_path))
        else:
            self.scene.load_layout(PatternScene._blank_template())
        cal = ImageCalibration(
            pixel_size_um=pixel_size_um,
            image_w_px=pix.width(), image_h_px=pix.height(),
            center_x_um=0.0, center_y_um=0.0,      # FOV centre at layout origin
        )
        self.scene.set_image(pix, cal)
        if fov_width_um:
            self.scene.frame_size_um = fov_width_um
        self._path = out_path
        self.setWindowTitle("elymaker %s - handoff \u2192 %s"
                            % (__version__, os.path.basename(out_path)))
        self.view.fit_all()
        self.undo_mgr.reset()
        self._revalidate()

    def load_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load FIB image", "",
            "Images (*.tif *.tiff *.png *.jpg *.bmp);;All files (*)")
        if not path:
            return
        pix = QPixmap(path)
        if pix.isNull():
            QMessageBox.warning(self, "Image", "Could not load image (unsupported format?)")
            return
        dlg = CalibrationDialog(pix.width(), pix.height(), self)
        if dlg.exec_() != dlg.Accepted:
            return
        self.scene.set_image(pix, dlg.calibration())
        self.view.fit_all()

    def export_svg(self) -> None:
        from ..preview import write_svg
        path, _ = QFileDialog.getSaveFileName(self, "Export SVG", "", "SVG (*.svg)")
        if not path:
            return
        if not path.lower().endswith(".svg"):
            path += ".svg"
        write_svg(self.scene.to_layout(), path)
        self.statusBar().showMessage("Wrote %s" % path, 4000)

    def delete_selected(self) -> None:
        removed = False
        for it in list(self.scene.selectedItems()):
            self.scene.removeItem(it)
            removed = True
        self.props.set_item(None)
        if removed:
            self.undo_mgr.commit()
        self._revalidate()
