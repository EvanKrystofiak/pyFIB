"""The editing scene: shapes + optional FIB image underlay.

Holds the live set of shape items and knows how to (a) load a core
:class:`~elymaker.model.Layout` into items and (b) export the current items back
to a Layout for saving. All non-shape layout metadata (name, version, layer,
frame) is preserved on a stashed template Layout so round-tripping a loaded file
does not lose header/exposure-default information.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from qtpy.QtCore import QRectF, Qt, Signal
from qtpy.QtGui import QPixmap
from qtpy.QtWidgets import QGraphicsScene, QGraphicsPixmapItem

from ..model import (
    Exposure,
    Layer,
    LayerReference,
    Layout,
    Structure,
    VersionInfo,
)
from .coords import ImageCalibration
from .items import (
    ArcItem,
    PolygonItem,
    RectItem,
    ShapeItemMixin,
    TextItem,
    item_from_shape,
)

_SHAPE_ITEM_TYPES = (RectItem, PolygonItem, ArcItem, TextItem)


def _now() -> str:
    return datetime.now().strftime("%d.%m.%Y %H:%M:%S")


class PatternScene(QGraphicsScene):
    """Scene in micrometre units (y-down). 1 scene unit == 1 um."""

    selectionShapeChanged = Signal(object)  # emits the single selected item or None

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pixmap_item: Optional[QGraphicsPixmapItem] = None
        self._calibration: Optional[ImageCalibration] = None
        self._order: list = []   # shape items in mill/insertion order
        # template carries header + layer/frame metadata for lossless save
        self._template: Layout = self._blank_template()
        self.setSceneRect(QRectF(-400, -400, 800, 800))
        self.selectionChanged.connect(self._on_selection_changed)

    # -- template / metadata --------------------------------------------- #
    @staticmethod
    def _blank_template(name: str = "Layout", fov_um: float = 600.0) -> Layout:
        stamp = _now()
        lay = Layout(
            name=name,
            version=VersionInfo(created=stamp, modified=stamp),
            layers=[Layer()],
        )
        lref = LayerReference(ref="Layer", frame_size=fov_um)
        st = Structure(
            name="Structure",
            version=VersionInfo(created=stamp, modified=stamp),
            layer_refs=[lref],
        )
        lay.structures = [st]
        return lay

    @property
    def layout_name(self) -> str:
        return self._template.name

    @layout_name.setter
    def layout_name(self, value: str) -> None:
        self._template.name = value

    @property
    def frame_size_um(self) -> float:
        return self._template.structures[0].layer_refs[0].frame_size

    @frame_size_um.setter
    def frame_size_um(self, value: float) -> None:
        self._template.structures[0].layer_refs[0].frame_size = float(value)

    def default_exposure(self) -> Optional[Exposure]:
        return self._template.layers[0].exposure

    # -- shape items ------------------------------------------------------ #
    def shape_items(self) -> List[ShapeItemMixin]:
        """Shape items in mill order (insertion order), still present in scene."""
        ordered = [it for it in self._order
                   if isinstance(it, _SHAPE_ITEM_TYPES) and it.scene() is self]
        # safety net: include any shape items added by a path that bypassed
        # add_shape_item, appended after the tracked ones
        seen = set(id(it) for it in ordered)
        for it in self.items():
            if isinstance(it, _SHAPE_ITEM_TYPES) and id(it) not in seen:
                ordered.append(it)
                seen.add(id(it))
        return ordered

    def add_shape_item(self, item) -> None:
        self.addItem(item)
        self.register_shape(item)

    def register_shape(self, item) -> None:
        """Record an item (already added to the scene) in mill order."""
        if item not in self._order:
            self._order.append(item)

    def insert_shape_item_after(self, after_item, new_item) -> None:
        """Add *new_item* to the scene, placed right after *after_item* in mill
        order (used by Duplicate). Falls back to appending if the anchor is
        untracked."""
        self.addItem(new_item)
        if after_item in self._order:
            idx = self._order.index(after_item)
            self._order.insert(idx + 1, new_item)
        else:
            self._order.append(new_item)

    def replace_shape_item(self, old_item, new_item) -> None:
        """Swap *old_item* for *new_item* at the same mill-order position."""
        idx = self._order.index(old_item) if old_item in self._order else len(self._order)
        self.addItem(new_item)
        if old_item in self._order:
            self._order[idx] = new_item
        else:
            self._order.append(new_item)
        self.removeItem(old_item)

    def clear_shapes(self) -> None:
        for it in self.shape_items():
            self.removeItem(it)
        self._order = []

    def _on_selection_changed(self) -> None:
        sel = [it for it in self.selectedItems() if isinstance(it, _SHAPE_ITEM_TYPES)]
        self.selectionShapeChanged.emit(sel[0] if len(sel) == 1 else None)

    # -- image underlay --------------------------------------------------- #
    def set_image(self, pixmap: QPixmap, calibration: ImageCalibration) -> None:
        self.clear_image()
        self._calibration = calibration
        item = QGraphicsPixmapItem(pixmap)
        x, y, w, h = calibration.scene_rect_um()
        # scale pixmap from pixels to micrometres
        sx = w / pixmap.width()
        sy = h / pixmap.height()
        item.setScale(1.0)  # we set transform via setScale on both axes below
        item.setTransform(item.transform().scale(sx, sy))
        item.setPos(x, y)
        item.setZValue(-1000)  # underlay
        item.setOpacity(0.85)
        self.addItem(item)
        self._pixmap_item = item

    def clear_image(self) -> None:
        if self._pixmap_item is not None:
            self.removeItem(self._pixmap_item)
            self._pixmap_item = None
        self._calibration = None

    @property
    def calibration(self) -> Optional[ImageCalibration]:
        return self._calibration

    # -- Layout <-> scene ------------------------------------------------- #
    def load_layout(self, layout: Layout) -> None:
        """Replace scene contents with the shapes from *layout*."""
        self.clear_shapes()
        # keep a metadata-only copy as the template (strip shapes)
        self._template = self._template_from(layout)
        for _st, _lr, sh in layout.iter_shapes():
            self.add_shape_item(item_from_shape(sh))
        self._fit_scene_rect()

    @staticmethod
    def _template_from(layout: Layout) -> Layout:
        """Copy a layout's header/layer/frame metadata but drop its shapes."""
        import copy

        tmpl = copy.deepcopy(layout)
        for st in tmpl.structures:
            for lr in st.layer_refs:
                lr.shapes = []
        if not tmpl.structures:
            tmpl.structures = [Structure(layer_refs=[LayerReference()])]
        if not tmpl.structures[0].layer_refs:
            tmpl.structures[0].layer_refs = [LayerReference()]
        return tmpl

    def to_layout(self) -> Layout:
        """Export current shapes into a copy of the template Layout."""
        import copy

        lay = copy.deepcopy(self._template)
        lay.version.modified = _now()
        lref = lay.structures[0].layer_refs[0]
        lref.shapes = [it.to_model() for it in self.shape_items()]
        return lay

    # -- helpers ---------------------------------------------------------- #
    def _fit_scene_rect(self) -> None:
        br = self.itemsBoundingRect()
        if br.isNull():
            return
        pad = max(br.width(), br.height()) * 0.2 + 10
        self.setSceneRect(br.adjusted(-pad, -pad, pad, pad))
