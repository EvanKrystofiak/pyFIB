"""The canvas view: zoom / pan, a live micrometre cursor readout, and the
mouse interaction for placing new shapes.

Tools:
  * SELECT   - default; select/move/rotate existing items (Qt rubber-band select)
  * RECT     - click-drag to rubber-band a new rectangle
  * POLYGON  - click to add vertices, double-click / Enter to finish
  * ARC      - click centre, drag radius (simple 120deg default sweep)
  * TEXT     - click to place a label (text/height come from current defaults)

New shapes inherit the editor's "current defaults" (depth, probe, recipe) which
the main window supplies via :meth:`set_defaults`.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from qtpy.QtCore import QPointF, QRectF, Qt, Signal
from qtpy.QtGui import QBrush, QColor, QFont, QPainter, QPen
from qtpy.QtWidgets import QGraphicsView

from ..model import Exposure
from .coords import scene_to_layout
from .items import ArcItem, PolygonItem, RectItem, TextItem
from .style import CANVAS_AXIS, CANVAS_BG, CANVAS_GRID

SELECT, HAND, RECT, RBOX, POLYGON, ARC, TEXT = (
    "select", "hand", "rect", "rbox", "polygon", "arc", "text")


def _nice_step(raw: float) -> float:
    """Round a raw um spacing up to a 1-2-5 x 10^n grid step."""
    import math
    if raw <= 0:
        return 1.0
    exp = math.floor(math.log10(raw))
    base = 10 ** exp
    for mult in (1, 2, 5, 10):
        if raw <= mult * base:
            return mult * base
    return 10 * base


class ShapeDefaults:
    """Parameters applied to newly-created shapes."""

    def __init__(self) -> None:
        self.depth_um: float = 15.0
        self.exposure_factory: Callable[[], Optional[Exposure]] = lambda: None
        self.text: str = "X"
        self.text_height_um: float = 6.0
        self.edge_sel_marked_edge: Optional[int] = None
        self.imaging: bool = False
        # rounded-box options
        self.rounded_side: str = "top"
        self.rounded_radius_um: float = 0.0     # 0 = auto (10% of shorter side)
        self.rounded_segments: int = 12

    def apply(self, item) -> None:
        item.set_depth_um(self.depth_um)
        item.exposure = self.exposure_factory()

    def effective_radius(self, width: float, height: float) -> float:
        from ..geometry import default_radius
        if self.rounded_radius_um and self.rounded_radius_um > 0:
            return self.rounded_radius_um
        return default_radius(width, height)


class PatternView(QGraphicsView):
    cursorMoved = Signal(float, float)      # layout um (x, y-up)
    shapeCreated = Signal(object)           # newly created item
    shapeMoved = Signal()                   # a drag-move finished (one undo step)
    drawSizeChanged = Signal(float, float)  # live box size while drawing (um)

    def __init__(self, scene, parent=None) -> None:
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setMouseTracking(True)
        self.setBackgroundBrush(QColor(CANVAS_BG))
        self._tool = SELECT
        self.defaults = ShapeDefaults()
        self._start: Optional[QPointF] = None
        self._temp_item = None
        self._poly_pts: List[QPointF] = []
        self._panning = False
        self._pan_last = None
        self._move_check = None
        self._show_order = False

    def drawBackground(self, painter, rect) -> None:
        painter.fillRect(rect, QColor(CANVAS_BG))
        # adaptive grid: aim for ~1-5-10 um steps scaled to a readable pitch
        scale = abs(self.transform().m11()) or 1.0
        target_px = 60.0
        raw = target_px / scale                      # um per ~60px
        step = _nice_step(raw)
        left = int(rect.left() // step) * step
        top = int(rect.top() // step) * step
        grid_pen = QPen(QColor(CANVAS_GRID))
        grid_pen.setCosmetic(True)
        grid_pen.setWidthF(1.0)
        painter.setPen(grid_pen)
        x = left
        while x < rect.right():
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            x += step
        y = top
        while y < rect.bottom():
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            y += step
        # origin axes
        axis_pen = QPen(QColor(CANVAS_AXIS))
        axis_pen.setCosmetic(True)
        axis_pen.setWidthF(1.4)
        painter.setPen(axis_pen)
        if rect.left() <= 0 <= rect.right():
            painter.drawLine(QPointF(0, rect.top()), QPointF(0, rect.bottom()))
        if rect.top() <= 0 <= rect.bottom():
            painter.drawLine(QPointF(rect.left(), 0), QPointF(rect.right(), 0))

    # -- tools ------------------------------------------------------------ #
    def set_tool(self, tool: str) -> None:
        self._finish_polygon(commit=False)
        self._tool = tool
        if tool == SELECT:
            self.setDragMode(QGraphicsView.RubberBandDrag)
        elif tool == HAND:
            self.setDragMode(QGraphicsView.ScrollHandDrag)   # left-drag pans
        else:
            self.setDragMode(QGraphicsView.NoDrag)

    # -- overlays: mill order + colour legend ----------------------------- #
    def set_show_order(self, on: bool) -> None:
        self._show_order = bool(on)
        self.viewport().update()

    def refresh_overlays(self) -> None:
        self.viewport().update()

    def drawForeground(self, painter, rect) -> None:
        super().drawForeground(painter, rect)
        painter.save()
        painter.resetTransform()          # draw the HUD in viewport pixels
        if self._show_order:
            self._draw_order_overlay(painter)
        self._draw_legend(painter)
        painter.restore()

    def _draw_order_overlay(self, painter) -> None:
        items = self.scene().shape_items()
        if not items:
            return
        centers = [QPointF(self.mapFromScene(it.center_scene())) for it in items]
        pen = QPen(QColor(230, 235, 245, 120))
        pen.setWidthF(1.5)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        for a, b in zip(centers, centers[1:]):
            painter.drawLine(a, b)
        f = QFont(); f.setPointSizeF(8); f.setBold(True)
        painter.setFont(f)
        R = 9.0
        for i, c in enumerate(centers):
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(18, 22, 30, 225)))
            painter.drawEllipse(c, R, R)
            ring = QPen(QColor(255, 209, 102)); ring.setWidthF(1.3)
            painter.setPen(ring); painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(c, R, R)
            painter.setPen(QPen(QColor(236, 240, 248)))
            painter.drawText(QRectF(c.x() - R, c.y() - R, 2 * R, 2 * R),
                             Qt.AlignCenter, str(i + 1))

    def _draw_legend(self, painter) -> None:
        from .items import (color_mode, COLOR_BY_CURRENT, current_color,
                            depth_color)
        if color_mode() == COLOR_BY_CURRENT:
            title = "Probe current"
            entries = [("30 nA", 3e-8), ("7 nA", 7e-9), ("1.5 nA", 1.5e-9),
                       ("700 pA", 7e-10), ("300 pA", 3e-10), ("100 pA", 1e-10),
                       ("20 pA", 2e-11), ("none", None)]
            swatch = lambda v: current_color(v, 255)
        else:
            title = "Depth (um)"
            entries = [("60", 60.0), ("30", 30.0), ("15", 15.0),
                       ("5", 5.0), ("1", 1.0)]
            swatch = lambda v: depth_color(v, 255)
        pad, sw, row, w = 8, 12, 16, 104
        h = row * (len(entries) + 1) + pad
        vh = self.viewport().height()
        x, y = 10.0, vh - h - 10.0
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(16, 19, 25, 205))
        painter.drawRoundedRect(QRectF(x, y, w, h), 6, 6)
        painter.setPen(QColor(200, 206, 216))
        f = QFont(); f.setPointSizeF(8); f.setBold(True); painter.setFont(f)
        painter.drawText(QRectF(x + pad, y + 4, w - 2 * pad, row),
                         Qt.AlignLeft | Qt.AlignVCenter, title)
        f.setBold(False); painter.setFont(f)
        for i, (lbl, v) in enumerate(entries):
            ry = y + row + i * row + 2
            painter.setPen(Qt.NoPen); painter.setBrush(swatch(v))
            painter.drawRect(QRectF(x + pad, ry + 2, sw, sw))
            painter.setPen(QColor(210, 216, 226))
            painter.drawText(QRectF(x + pad + sw + 6, ry, w - pad - sw - 10, row),
                             Qt.AlignLeft | Qt.AlignVCenter, lbl)

    @property
    def tool(self) -> str:
        return self._tool

    def set_defaults(self, defaults: ShapeDefaults) -> None:
        self.defaults = defaults

    # -- zoom / pan ------------------------------------------------------- #
    def wheelEvent(self, event) -> None:
        factor = 1.25 if event.angleDelta().y() > 0 else 1 / 1.25
        self.scale(factor, factor)

    # -- cursor readout --------------------------------------------------- #
    def mouseMoveEvent(self, event) -> None:
        if self._panning and self._pan_last is not None:
            delta = event.pos() - self._pan_last
            self._pan_last = event.pos()
            h = self.horizontalScrollBar()
            v = self.verticalScrollBar()
            h.setValue(h.value() - delta.x())
            v.setValue(v.value() - delta.y())
            return
        sp = self.mapToScene(event.pos())
        lx, ly = scene_to_layout(sp.x(), sp.y())
        self.cursorMoved.emit(lx, ly)
        if self._tool in (RECT, RBOX) and self._start is not None and self._temp_item is not None:
            self._update_temp_rect(sp)
        super().mouseMoveEvent(event)

    # -- shape creation --------------------------------------------------- #
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MiddleButton:
            self._panning = True
            self._pan_last = event.pos()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            return
        # in SELECT mode, remember positions so a drag becomes one undo step
        if event.button() == Qt.LeftButton and self._tool == SELECT:
            self._move_check = {
                id(it): (it.pos().x(), it.pos().y())
                for it in self.scene().shape_items()
            }
        if event.button() != Qt.LeftButton or self._tool in (SELECT, HAND):
            super().mousePressEvent(event)
            return
        sp = self.mapToScene(event.pos())
        if self._tool in (RECT, RBOX):
            self._start = sp
            it = RectItem(0.1, -0.1)
            it.setPos(sp.x(), sp.y())
            self.defaults.apply(it)
            it.imaging = self.defaults.imaging
            it.edge_sel_marked_edge = self.defaults.edge_sel_marked_edge
            self.scene().addItem(it)
            self._temp_item = it
        elif self._tool == POLYGON:
            self._poly_pts.append(sp)
        elif self._tool == ARC:
            self._start = sp
        elif self._tool == TEXT:
            self._place_text(sp)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MiddleButton and self._panning:
            self._panning = False
            self._pan_last = None
            self.viewport().unsetCursor()
            return
        if self._tool == RECT and self._temp_item is not None:
            self._commit_rect()
        elif self._tool == RBOX and self._temp_item is not None:
            self._commit_rounded_box()
        elif self._tool == ARC and self._start is not None:
            sp = self.mapToScene(event.pos())
            self._commit_arc(self._start, sp)
            self._start = None
        else:
            super().mouseReleaseEvent(event)
        # detect a completed drag-move (SELECT mode) -> one undo step
        if self._move_check is not None:
            moved = False
            for it in self.scene().shape_items():
                prev = self._move_check.get(id(it))
                if prev is not None and (abs(it.pos().x() - prev[0]) > 1e-9
                                         or abs(it.pos().y() - prev[1]) > 1e-9):
                    moved = True
                    break
            self._move_check = None
            if moved:
                self.shapeMoved.emit()

    def mouseDoubleClickEvent(self, event) -> None:
        if self._tool == POLYGON:
            self._finish_polygon(commit=True)
        else:
            super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event) -> None:
        # arrow-key nudge of the selection (Shift = x5, Ctrl = x0.1)
        arrows = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0),
                  Qt.Key_Up: (0, 1), Qt.Key_Down: (0, -1)}   # layout: +y up
        if event.key() in arrows and self._tool == SELECT:
            sel = [it for it in self.scene().shape_items() if it.isSelected()]
            if sel:
                step = 1.0
                mods = event.modifiers()
                if mods & Qt.ShiftModifier:
                    step = 5.0
                elif mods & Qt.ControlModifier:
                    step = 0.1
                dxl, dyl = arrows[event.key()]
                # layout delta -> scene delta (y flips)
                dsx, dsy = dxl * step, -dyl * step
                for it in sel:
                    it.moveBy(dsx, dsy)
                self.shapeMoved.emit()
                event.accept()
                return
        if self._tool == POLYGON and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._finish_polygon(commit=True)
        elif event.key() == Qt.Key_Escape:
            self._finish_polygon(commit=False)
        else:
            super().keyPressEvent(event)

    # -- rect helpers ----------------------------------------------------- #
    def _update_temp_rect(self, sp: QPointF) -> None:
        it = self._temp_item
        x0, y0 = self._start.x(), self._start.y()
        x1, y1 = sp.x(), sp.y()
        min_sx, max_sx = sorted((x0, x1))
        min_sy, max_sy = sorted((y0, y1))
        # anchor at the top-left in scene = top-left corner in layout (y-up).
        # width > 0 (rightward), height < 0 (downward) -> SmartFIB file convention.
        it.setPos(min_sx, min_sy)
        it.width = max(max_sx - min_sx, 0.1)
        it.height = -max(max_sy - min_sy, 0.1)
        it.prepareGeometryChange()
        it.update()
        self.drawSizeChanged.emit(abs(it.width), abs(it.height))

    def _commit_rect(self) -> None:
        it = self._temp_item
        self._temp_item = None
        self._start = None
        self.drawSizeChanged.emit(0.0, 0.0)
        if abs(it.width) < 0.2 and abs(it.height) < 0.2:
            self.scene().removeItem(it)
            return
        self.scene().register_shape(it)
        self.shapeCreated.emit(it)

    def _commit_rounded_box(self) -> None:
        from ..geometry import rounded_rect_polygon
        from .coords import layout_to_scene
        from .items import PolygonItem
        temp = self._temp_item
        self._temp_item = None
        self._start = None
        self.drawSizeChanged.emit(0.0, 0.0)
        # read the dragged box geometry (in layout um), then discard the preview
        m = temp.to_model()
        self.scene().removeItem(temp)
        if abs(m.width) < 0.2 and abs(m.height) < 0.2:
            return
        r = self.defaults.effective_radius(m.width, m.height)
        pts_layout = rounded_rect_polygon(
            m.x, m.y, m.width, m.height, r,
            self.defaults.rounded_side, self.defaults.rounded_segments)
        pts_scene = [layout_to_scene(px, py) for px, py in pts_layout]
        poly = PolygonItem(pts_scene)
        self.defaults.apply(poly)
        self.scene().add_shape_item(poly)
        self.shapeCreated.emit(poly)

    # -- polygon helpers -------------------------------------------------- #
    def _finish_polygon(self, commit: bool) -> None:
        pts = self._poly_pts
        self._poly_pts = []
        if commit and len(pts) >= 3:
            it = PolygonItem([(p.x(), p.y()) for p in pts])
            self.defaults.apply(it)
            it.edge_sel_marked_edge = self.defaults.edge_sel_marked_edge
            self.scene().add_shape_item(it)
            self.shapeCreated.emit(it)

    # -- arc / text helpers ---------------------------------------------- #
    def _commit_arc(self, center: QPointF, edge: QPointF) -> None:
        import math
        # centre in layout coords
        cx, cy = scene_to_layout(center.x(), center.y())
        r = math.hypot(edge.x() - center.x(), edge.y() - center.y())
        r = max(r, 0.5)
        it = ArcItem(cx, cy, r, r * 0.85, 120.0, 240.0)
        self.defaults.apply(it)
        self.scene().add_shape_item(it)
        self.shapeCreated.emit(it)

    def _place_text(self, sp: QPointF) -> None:
        cx, cy = scene_to_layout(sp.x(), sp.y())
        it = TextItem(self.defaults.text, self.defaults.text_height_um)
        it.setPos(sp.x(), sp.y())
        self.defaults.apply(it)
        self.scene().add_shape_item(it)
        self.shapeCreated.emit(it)

    # -- convenience ------------------------------------------------------ #
    def fit_all(self) -> None:
        br = self.scene().itemsBoundingRect()
        if not br.isNull():
            self.fitInView(br.adjusted(-5, -5, 5, 5), Qt.KeepAspectRatio)

    def zoom_to_selection(self) -> None:
        items = self.scene().selectedItems()
        if not items:
            self.fit_all()
            return
        rect = items[0].sceneBoundingRect()
        for it in items[1:]:
            rect = rect.united(it.sceneBoundingRect())
        pad = max(rect.width(), rect.height()) * 0.3 + 3
        self.fitInView(rect.adjusted(-pad, -pad, pad, pad), Qt.KeepAspectRatio)
