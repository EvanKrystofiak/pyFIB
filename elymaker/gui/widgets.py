"""Reusable editor widgets.

EdgeSelector
------------
A plus-shaped picker for the edge a shape's mill progresses toward. Buttons are
laid out spatially (top/left/right/bottom around a central "None") so the choice
matches what the operator sees on the box.

The picker deals only in *physical directions*. The actual stored value
(``edge_sel_marked_edge``) is an index into the shape's vertex order, and which
index corresponds to a given screen direction depends on the box's width/height
signs and rotation -- so that conversion lives on the shape item
(``index_for_direction`` / ``direction_for_index``), not here.
"""

from __future__ import annotations

from typing import Optional

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class EdgeSelector(QWidget):
    edgeChanged = Signal(object)   # emits direction str or None

    _POS = {"top": (0, 1), "left": (1, 0), "right": (1, 2), "bottom": (2, 1)}

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        grid_host = QWidget(self)
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons = {}

        for direction, (r, c) in self._POS.items():
            b = QToolButton(self)
            b.setCheckable(True)
            b.setText(direction.capitalize())
            b.setToolTip("Mill toward the %s edge" % direction)
            b.setMinimumWidth(54)
            grid.addWidget(b, r, c)
            self._group.addButton(b)
            self._buttons[direction] = b
            b.clicked.connect(lambda _=False, d=direction: self._pick(d))

        none = QToolButton(self)
        none.setCheckable(True)
        none.setText("None")
        none.setToolTip("No marked edge (default scan direction)")
        none.setChecked(True)
        grid.addWidget(none, 1, 1)
        self._group.addButton(none)
        none.clicked.connect(lambda _=False: self._pick(None))
        self._buttons[None] = none

        outer.addWidget(grid_host)
        note = QLabel("mill/scan direction \u2014 confirm in SmartFIB before milling")
        note.setWordWrap(True)
        note.setStyleSheet("color: #8a93a3; font-size: 10px;")
        outer.addWidget(note)

    # -- interaction ------------------------------------------------------ #
    def _pick(self, direction) -> None:
        self.edgeChanged.emit(direction)

    # -- programmatic API ------------------------------------------------- #
    def set_direction(self, direction: Optional[str]) -> None:
        btn = self._buttons.get(direction, self._buttons[None])
        was = self._group.blockSignals(True)
        for b in self._buttons.values():
            b.setChecked(False)
        btn.setChecked(True)
        self._group.blockSignals(was)

    def direction(self) -> Optional[str]:
        for direction, b in self._buttons.items():
            if b.isChecked():
                return direction
        return None

    def choose(self, direction: Optional[str]) -> None:
        """Select *and* emit, as if the user clicked (for tests / scripting)."""
        self.set_direction(direction)
        self.edgeChanged.emit(direction)
