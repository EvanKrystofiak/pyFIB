"""Dialog for the lamella pattern generator (Pattern -> New lamella...)."""

from __future__ import annotations

from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QVBoxLayout,
)

from ..lamella import LamellaSpec, LamellaStage, default_stages
from ..probes import probe_names_by_current


def _dsb(value, lo, hi, dec=3, step=0.5):
    w = QDoubleSpinBox()
    w.setRange(lo, hi); w.setDecimals(dec); w.setSingleStep(step)
    w.setValue(value); w.setKeyboardTracking(False)
    return w


def _probe_combo(names, current):
    c = QComboBox(); c.addItems(names)
    if current and current in names:
        c.setCurrentText(current)
    else:
        c.setCurrentIndex(0)
    return c


class LamellaDialog(QDialog):
    def __init__(self, parent=None, depth_um=1.0, recipe=None, spec=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New lamella")
        self._recipe = recipe
        spec = spec or LamellaSpec(depth_um=depth_um, recipe=recipe)
        self._names = ["(recipe default)"] + probe_names_by_current()

        root = QVBoxLayout(self)
        form = QFormLayout()
        root.addLayout(form)

        self.width = _dsb(spec.final_width_um, 0.1, 1e4)
        self.thickness = _dsb(spec.final_thickness_um, 0.01, 1e4)
        self.hstep = _dsb(spec.h_step_um, 0.0, 1e4)
        self.overlap = _dsb(spec.overlap_um, 0.0, 1e4)
        self.depth = _dsb(spec.depth_um, 0.001, 1e4)
        self.cx = _dsb(spec.center_x_um, -1e5, 1e5)
        self.cy = _dsb(spec.center_y_um, -1e5, 1e5)
        form.addRow("Final lamella width (X, um)", self.width)
        form.addRow("Final target thickness (Y, um)", self.thickness)
        form.addRow("Horizontal step per stage (um)", self.hstep)
        form.addRow("Vertical overlap (um)", self.overlap)
        form.addRow("Mill depth (um, all stages)", self.depth)
        form.addRow("Centre X (um)", self.cx)
        form.addRow("Centre Y (um)", self.cy)

        # per-stage table: current + box height (outermost first)
        box = QGroupBox("Stages (outer \u2192 inner): current and box height")
        grid = QGridLayout(box)
        grid.addWidget(QLabel("Stage"), 0, 0)
        grid.addWidget(QLabel("Current"), 0, 1)
        grid.addWidget(QLabel("Height (um)"), 0, 2)
        self._rows = []
        for i, st in enumerate(spec.stages):
            grid.addWidget(QLabel(st.name), i + 1, 0)
            combo = _probe_combo(self._names, st.probe)
            hspin = _dsb(st.height_um, 0.001, 1e4)
            grid.addWidget(combo, i + 1, 1)
            grid.addWidget(hspin, i + 1, 2)
            self._rows.append((st.name, combo, hspin))
        root.addWidget(box)

        # side trenches
        tbox = QGroupBox("Side trenches")
        tl = QFormLayout(tbox)
        self.tr_enable = QCheckBox("Add side trenches")
        self.tr_enable.setChecked(spec.trenches)
        tl.addRow(self.tr_enable)
        self.tr_width = _dsb(spec.trench_width_um, 0.01, 1e4)
        self.tr_offset = _dsb(spec.trench_offset_um, 0.0, 1e4)
        self.tr_probe = _probe_combo(self._names, spec.trench_probe)
        tl.addRow("Trench width (um)", self.tr_width)
        tl.addRow("Offset from pattern (um)", self.tr_offset)
        tl.addRow("Trench current", self.tr_probe)
        root.addWidget(tbox)

        self.replace = QCheckBox("Replace previously generated lamella")
        self.replace.setChecked(True)
        root.addWidget(self.replace)

        note = QLabel("Stage heights shrink toward the lamella so the low-current "
                      "fine/polish stages stay fast; trenches span the full height "
                      "and mill inward.")
        note.setWordWrap(True); note.setStyleSheet("color:#8a93a3; font-size:10px;")
        root.addWidget(note)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Generate")
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        root.addWidget(bb)

    def _probe_value(self, combo):
        t = combo.currentText()
        return None if t.startswith("(") else t

    def spec(self) -> LamellaSpec:
        stages = [LamellaStage(name, self._probe_value(combo), hspin.value())
                  for (name, combo, hspin) in self._rows]
        return LamellaSpec(
            final_width_um=self.width.value(),
            final_thickness_um=self.thickness.value(),
            h_step_um=self.hstep.value(),
            overlap_um=self.overlap.value(),
            depth_um=self.depth.value(),
            center_x_um=self.cx.value(),
            center_y_um=self.cy.value(),
            recipe=self._recipe,
            stages=stages,
            trenches=self.tr_enable.isChecked(),
            trench_width_um=self.tr_width.value(),
            trench_offset_um=self.tr_offset.value(),
            trench_probe=self._probe_value(self.tr_probe),
        )

    @property
    def replace_previous(self) -> bool:
        return self.replace.isChecked()
