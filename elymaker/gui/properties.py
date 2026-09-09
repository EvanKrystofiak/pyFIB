"""Properties panel: edit the currently-selected shape.

Precise numeric entry is deliberately first-class here -- for a milling pattern,
typing an exact width/depth matters more than drag-resizing. Editing the depth
field updates both ``dose_factor`` and ``z_extent`` (they are coupled).

Implementation note: the form is rebuilt from scratch on every selection. This
is intentional -- ``QFormLayout.removeRow`` *deletes* the widgets it removes, so
reusing persistent field widgets across selections leads to use-after-free. We
recreate the small set of fields each time (selection is infrequent) and store
them as attributes for the current item.
"""

from __future__ import annotations

from typing import Optional

from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)
from qtpy.QtCore import Signal

from ..probes import PROBES, get_probe, probe_names_by_current
from ..model import Exposure
from .coords import layout_to_scene, scene_to_layout, scene_angle_to_layout
from .items import ArcItem, PolygonItem, RectItem, TextItem
from .widgets import EdgeSelector


def _dsb(value, minimum, maximum, decimals=4, step=1.0) -> QDoubleSpinBox:
    w = QDoubleSpinBox()
    w.setRange(minimum, maximum)
    w.setDecimals(decimals)
    w.setSingleStep(step)
    w.setKeyboardTracking(False)
    w.setValue(value)
    return w


class PropertiesPanel(QWidget):
    changed = Signal()   # emitted after any edit is applied to the item

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._item = None
        self._building = False

        root = QVBoxLayout(self)
        self._title = QLabel("No selection")
        self._title.setStyleSheet("font-weight: bold;")
        root.addWidget(self._title)
        self._form = QFormLayout()
        root.addLayout(self._form)
        root.addStretch(1)

        # attribute handles for the current item's fields (set in set_item)
        self.depth = self.probe = self.recipe = None
        self.x = self.y = self.w = self.h = self.angle = None
        self.imaging = self.ignored = self.marked_edge = self.area_fast = None
        self.text = self.text_h = None
        self.r1 = self.r2 = self.a1 = self.a2 = None

        self.set_item(None)

    # -- form management -------------------------------------------------- #
    def _clear_form(self) -> None:
        while self._form.rowCount():
            self._form.removeRow(0)   # deletes the removed widgets

    def _shared_fields(self, item) -> None:
        self.depth = _dsb(item.depth_um, 0.001, 500.0, 4, 1.0)
        self.depth.valueChanged.connect(self._apply)
        self._form.addRow("Depth (um)", self.depth)

        self.probe = QComboBox()
        self.probe.addItem("(recipe default)")
        for name in probe_names_by_current():
            self.probe.addItem(name)
        cur = item.exposure.probe.name if (item.exposure and item.exposure.probe) else None
        idx = self.probe.findText(cur) if cur else 0
        self.probe.setCurrentIndex(idx if idx >= 0 else 0)
        self.probe.currentIndexChanged.connect(self._apply)
        self._form.addRow("Probe", self.probe)

        self.recipe = QLineEdit(item.exposure.file if item.exposure else "")
        self.recipe.editingFinished.connect(self._apply)
        self._form.addRow("Recipe (.epm)", self.recipe)

    # -- selection -------------------------------------------------------- #
    def set_item(self, item) -> None:
        self._building = True
        self._item = item
        self._clear_form()
        if item is None:
            self._title.setText("No selection")
            self._building = False
            return

        self._title.setText(type(item).__name__.replace("Item", ""))
        self._shared_fields(item)

        if isinstance(item, RectItem):
            lx, ly = scene_to_layout(item.pos().x(), item.pos().y())
            self.x = _dsb(lx, -1e6, 1e6); self.x.valueChanged.connect(self._apply)
            self.y = _dsb(ly, -1e6, 1e6); self.y.valueChanged.connect(self._apply)
            self.w = _dsb(item.width, 0.001, 1e6); self.w.valueChanged.connect(self._apply)
            self.h = _dsb(item.height, -1e6, 1e6); self.h.valueChanged.connect(self._apply)
            self.angle = _dsb(scene_angle_to_layout(item.rotation()), -360, 360, 3)
            self.angle.valueChanged.connect(self._apply)
            self.marked_edge = EdgeSelector()
            self.marked_edge.set_direction(item.marked_edge_dir())
            self.marked_edge.edgeChanged.connect(self._apply)
            self.area_fast = QLineEdit("" if item.area_fast_deg is None
                                       else ("%g" % item.area_fast_deg))
            self.area_fast.editingFinished.connect(self._apply)
            self.imaging = QCheckBox("imaging"); self.imaging.setChecked(item.imaging)
            self.imaging.toggled.connect(self._apply)
            self.ignored = QCheckBox("ignored (excluded from mill)")
            self.ignored.setChecked(item.ignored); self.ignored.toggled.connect(self._apply)
            self._form.addRow("x (um)", self.x); self._form.addRow("y (um)", self.y)
            self._form.addRow("width (um)", self.w); self._form.addRow("height (um)", self.h)
            self._form.addRow("angle (deg)", self.angle)
            self._form.addRow("mill toward", self.marked_edge)
            _idx, _auto = item.effective_marked_edge()
            if _auto and _idx is not None:
                _n = QLabel("auto-scan (SmartFIB chooses; est. %s)"
                            % item.direction_for_index(_idx))
                _n.setStyleSheet("color:#8a93a3; font-size:10px;")
                self._form.addRow("", _n)
            self._form.addRow("area_fast (deg)", self.area_fast)
            self._form.addRow("", self.imaging)
            self._form.addRow("", self.ignored)

        elif isinstance(item, TextItem):
            lx, ly = scene_to_layout(item.pos().x(), item.pos().y())
            self.x = _dsb(lx, -1e6, 1e6); self.x.valueChanged.connect(self._apply)
            self.y = _dsb(ly, -1e6, 1e6); self.y.valueChanged.connect(self._apply)
            self.text = QLineEdit(item.text); self.text.editingFinished.connect(self._apply)
            self.text_h = _dsb(item.text_height, 0.001, 1e4)
            self.text_h.valueChanged.connect(self._apply)
            self._form.addRow("x (um)", self.x); self._form.addRow("y (um)", self.y)
            self._form.addRow("text", self.text)
            self._form.addRow("text height (um)", self.text_h)

        elif isinstance(item, ArcItem):
            self.x = _dsb(item.cx, -1e6, 1e6); self.x.valueChanged.connect(self._apply)
            self.y = _dsb(item.cy, -1e6, 1e6); self.y.valueChanged.connect(self._apply)
            self.r1 = _dsb(item.r1, 0.001, 1e6); self.r1.valueChanged.connect(self._apply)
            self.r2 = _dsb(item.r2, 0.0, 1e6); self.r2.valueChanged.connect(self._apply)
            self.a1 = _dsb(item.a1_deg, -720, 720, 3); self.a1.valueChanged.connect(self._apply)
            self.a2 = _dsb(item.a2_deg, -720, 720, 3); self.a2.valueChanged.connect(self._apply)
            self._form.addRow("cx (um)", self.x); self._form.addRow("cy (um)", self.y)
            self._form.addRow("r outer (um)", self.r1); self._form.addRow("r inner (um)", self.r2)
            self._form.addRow("a1 (deg)", self.a1); self._form.addRow("a2 (deg)", self.a2)

        elif isinstance(item, PolygonItem):
            self._form.addRow("vertices", QLabel("%d points" % len(item._pts)))
            self.marked_edge = EdgeSelector()
            self.marked_edge.set_direction(item.marked_edge_dir())
            self.marked_edge.edgeChanged.connect(self._apply)
            self.ignored = QCheckBox("ignored (excluded from mill)")
            self.ignored.setChecked(item.ignored); self.ignored.toggled.connect(self._apply)
            self._form.addRow("mill toward", self.marked_edge)
            _idx, _auto = item.effective_marked_edge()
            if _auto and _idx is not None:
                _n = QLabel("auto-scan (SmartFIB chooses; est. %s)"
                            % item.direction_for_index(_idx))
                _n.setStyleSheet("color:#8a93a3; font-size:10px;")
                self._form.addRow("", _n)
            self._form.addRow("", self.ignored)

        self._building = False

    # -- apply edits back to the item ------------------------------------- #
    def _apply(self, *args) -> None:
        if self._building or self._item is None:
            return
        item = self._item
        item.set_depth_um(self.depth.value())

        recipe = self.recipe.text().strip()
        probe_name = self.probe.currentText()
        probe = get_probe(probe_name) if probe_name in PROBES else None
        if item.exposure is None:
            item.exposure = Exposure(file=recipe, probe=probe)
        else:
            item.exposure.file = recipe
            item.exposure.probe = probe

        if isinstance(item, RectItem):
            sx, sy = layout_to_scene(self.x.value(), self.y.value())
            item.prepareGeometryChange()
            item.setPos(sx, sy)
            item.width = self.w.value()
            item.height = self.h.value()
            item.setRotation(self.angle.value())
            item.imaging = self.imaging.isChecked()
            item.ignored = self.ignored.isChecked()
            item.set_marked_edge_dir(self.marked_edge.direction())
            af = self.area_fast.text().strip()
            item.area_fast_deg = float(af) if af else None

        elif isinstance(item, TextItem):
            sx, sy = layout_to_scene(self.x.value(), self.y.value())
            item.prepareGeometryChange()
            item.setPos(sx, sy)
            item.text = self.text.text()
            item.text_height = self.text_h.value()

        elif isinstance(item, ArcItem):
            item.cx = self.x.value(); item.cy = self.y.value()
            item.r1 = self.r1.value(); item.r2 = self.r2.value()
            item.a1_deg = self.a1.value(); item.a2_deg = self.a2.value()
            item._rebuild()

        elif isinstance(item, PolygonItem):
            item.ignored = self.ignored.isChecked()
            item.set_marked_edge_dir(self.marked_edge.direction())

        item.refresh_style()
        item.update()
        self.changed.emit()
