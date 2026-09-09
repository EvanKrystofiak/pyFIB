"""Dialog to calibrate a loaded FIB image into micrometres.

The operator supplies either a pixel size (um/px) or a horizontal field width
(HFW, um); the other is derived from the image's real pixel width. This follows
the pyFIB rule that pixel size must come from actual image geometry, not an
assumed/stored value.
"""

from __future__ import annotations

from typing import Optional

from qtpy.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
)

from .coords import ImageCalibration


class CalibrationDialog(QDialog):
    def __init__(self, image_w_px: int, image_h_px: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Calibrate FIB image")
        self._w = int(image_w_px)
        self._h = int(image_h_px)

        form = QFormLayout(self)
        form.addRow(QLabel("Image: %d x %d px" % (self._w, self._h)))

        self.mode = QComboBox()
        self.mode.addItems(["Horizontal field width (um)", "Pixel size (um/px)"])
        form.addRow("Specify", self.mode)

        self.value = QDoubleSpinBox()
        self.value.setDecimals(6)
        self.value.setRange(1e-6, 1e7)
        self.value.setValue(50.0)   # HFW um default
        form.addRow("Value", self.value)

        # where the image sits in layout space. Default: centre at (0,0), which
        # is how SmartFIB treats a loaded image (FOV centre = layout origin).
        self.anchor = QComboBox()
        self.anchor.addItems(["Centre at origin", "Bottom-left at origin"])
        form.addRow("Placement", self.anchor)

        self.ox = QDoubleSpinBox(); self.ox.setRange(-1e6, 1e6); self.ox.setDecimals(4)
        self.oy = QDoubleSpinBox(); self.oy.setRange(-1e6, 1e6); self.oy.setDecimals(4)
        form.addRow("Anchor x (um)", self.ox)
        form.addRow("Anchor y (um)", self.oy)

        self._derived = QLabel("")
        form.addRow("Derived", self._derived)

        self.mode.currentIndexChanged.connect(self._update_derived)
        self.value.valueChanged.connect(self._update_derived)
        self.anchor.currentIndexChanged.connect(self._update_derived)
        self.ox.valueChanged.connect(self._update_derived)
        self.oy.valueChanged.connect(self._update_derived)
        self._update_derived()

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _update_derived(self, *args) -> None:
        try:
            cal = self.calibration()
        except Exception:
            self._derived.setText("(invalid)")
            return
        bl_x = cal.center_x_um - cal.width_um / 2.0
        bl_y = cal.center_y_um - cal.height_um / 2.0
        self._derived.setText(
            "pixel %.5g um/px, field %.4g x %.4g um, bottom-left=(%.3g, %.3g)"
            % (cal.pixel_size_um, cal.width_um, cal.height_um, bl_x, bl_y)
        )

    def calibration(self) -> ImageCalibration:
        v = self.value.value()
        if self.mode.currentIndex() == 0:  # HFW
            cal = ImageCalibration.from_hfw(v, self._w, self._h)
        else:
            cal = ImageCalibration(v, self._w, self._h)
        # position the image: anchor point sits at (ox, oy) in layout um
        ox, oy = self.ox.value(), self.oy.value()
        if self.anchor.currentIndex() == 0:    # centre at (ox, oy)  [default]
            cal.center_x_um = ox
            cal.center_y_um = oy
        else:                                  # bottom-left at (ox, oy)
            cal.center_x_um = ox + cal.width_um / 2.0
            cal.center_y_um = oy + cal.height_um / 2.0
        return cal
