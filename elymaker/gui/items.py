"""QGraphicsItem wrappers around model shapes.

Each item knows how to render itself in scene space (micrometres, y-down) and
how to convert back to a core model shape via ``to_model()``. Item geometry is
kept in scene coordinates; the y-sign flip to layout space happens only in
``to_model`` / ``from_model`` through :mod:`elymaker.gui.coords`.

Rectangles are the workhorse (waffles are all rects), so RectItem supports
interactive move/rotate and precise numeric edits; the other shapes render and
round-trip but are edited numerically via the properties panel.
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

from qtpy.QtCore import QPointF, QRectF, Qt
from qtpy.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainterPath, QPen, QPolygonF
from qtpy.QtWidgets import (
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsPathItem,
)

from ..model import Arc, Exposure, Polygon, Probe, Rect, Shape, Text, DOSE_TO_METRE
from .coords import (
    layout_angle_to_scene,
    layout_to_scene,
    scene_angle_to_layout,
    scene_to_layout,
)

# depth (um) -> colour ramp, shared with the SVG preview idea
_DEPTH_MIN, _DEPTH_MAX = 1.0, 60.0

# --- "marked edge" (the edge the mill progresses toward) ----------------- #
# marked_edge is an index into the shape's VERTEX order: edge N runs from
# vertex[N-1] to vertex[N mod n] (1-based). Which physical edge that is depends
# on the geometry, so it is computed, not hard-coded:
#   * Rectangle vertices (SmartFIB order): P0=(x,y) P1=(x+w,y) P2=(x+w,y+h)
#     P3=(x,y+h).  With w>0,h<0 this gives edge 1=top, 2=right, 3=bottom, 4=left
#     (matches the confirmed case edge 3 = bottom). With h>0 the box extends the
#     other way, so edge 3 becomes the TOP edge -- the index alone is not a fixed
#     compass point.
#   * Polygon edges follow the point list directly.
# The direction picker therefore maps a physical direction to whichever edge
# index currently points that way, given the box's width/height signs and
# rotation, rather than assuming a fixed table.
EDGE_DIRS = ("top", "right", "bottom", "left")
_DIR_VEC = {"top": (0.0, -1.0), "bottom": (0.0, 1.0),
            "left": (-1.0, 0.0), "right": (1.0, 0.0)}   # scene coords, y-down


def depth_color(depth_um: float, alpha: int = 110) -> QColor:
    if _DEPTH_MAX <= _DEPTH_MIN:
        t = 0.5
    else:
        t = max(0.0, min(1.0, (depth_um - _DEPTH_MIN) / (_DEPTH_MAX - _DEPTH_MIN)))
    r = int(60 + t * 180)
    g = int(90 + (1 - abs(t - 0.5) * 2) * 60)
    b = int(220 - t * 170)
    c = QColor(r, g, b)
    c.setAlpha(alpha)
    return c


# --- colour mode: shapes can be tinted by depth (default) or by probe current #
COLOR_BY_DEPTH, COLOR_BY_CURRENT = "depth", "current"
_COLOR_MODE = COLOR_BY_DEPTH
# probe current colour ramp: spread blue(low)..red across 10 pA .. 1.5 nA, then
# push anything above 1.5 nA into magenta/purple so the common probes (which
# cluster at the low end on a log scale) get real dynamic range instead of all
# reading yellow.
# probe current colour ramp: blue(<=20 pA) -> red(700 pA) -> magenta(>=30 nA).
# Red caps at 700 pA and blue floors at 20 pA so the mid-range probes spread
# across the spectrum instead of bunching in yellow/green.
_CUR_MIN, _CUR_RAMP_MAX = 2e-11, 7e-10     # blue(20 pA) -> red(700 pA)
_CUR_HIGH = 3e-8                            # 700 pA .. 30 nA -> red -> magenta
_NO_CURRENT = QColor(150, 156, 168)   # no probe / recipe default


def set_color_mode(mode: str) -> None:
    global _COLOR_MODE
    if mode not in (COLOR_BY_DEPTH, COLOR_BY_CURRENT):
        raise ValueError("bad colour mode %r" % mode)
    _COLOR_MODE = mode


def color_mode() -> str:
    return _COLOR_MODE


def current_color(current_A, alpha: int = 110) -> QColor:
    """Log ramp: blue(low)->red at 1.5 nA, then red->magenta up to 30 nA."""
    if not current_A or current_A <= 0:
        c = QColor(_NO_CURRENT)
    elif current_A <= _CUR_RAMP_MAX:
        lo, hi = math.log10(_CUR_MIN), math.log10(_CUR_RAMP_MAX)
        t = max(0.0, min(1.0, (math.log10(current_A) - lo) / (hi - lo)))
        c = QColor.fromHsv(int(210 - t * 210), 200, 235)   # blue(210) -> red(0)
    else:
        lo, hi = math.log10(_CUR_RAMP_MAX), math.log10(_CUR_HIGH)
        t = max(0.0, min(1.0, (math.log10(current_A) - lo) / (hi - lo)))
        c = QColor.fromHsv(int(360 - t * 60), 200, 235)    # red(360) -> magenta(300)
    c.setAlpha(alpha)
    return c


def item_current(item):
    ex = getattr(item, "exposure", None)
    if ex is not None and getattr(ex, "probe", None) is not None:
        return ex.probe.current_A
    return None


def _base_color(item, alpha: int) -> QColor:
    if _COLOR_MODE == COLOR_BY_CURRENT:
        return current_color(item_current(item), alpha)
    return depth_color(item.depth_um, alpha)


def _pen(item) -> QPen:
    c = _base_color(item, 255)
    pen = QPen(c)
    pen.setCosmetic(True)  # constant on-screen width regardless of zoom
    pen.setWidthF(1.5)
    if getattr(item, "ignored", False):
        pen.setStyle(Qt.DashLine)
    return pen


def _brush(item) -> QBrush:
    ignored = getattr(item, "ignored", False)
    return QBrush(_base_color(item, alpha=40 if ignored else 110))


class ShapeItemMixin:
    """Common exposure/params carried by every item, plus model helpers."""

    def __init__(self) -> None:
        self.dose_factor: float = 1.0
        self.z_extent_m: float = 1e-6
        self.exposure: Optional[Exposure] = None

    # depth is the single knob; keep dose_factor and z_extent coupled
    @property
    def depth_um(self) -> float:
        return self.z_extent_m / DOSE_TO_METRE

    def set_depth_um(self, depth_um: float) -> None:
        self.dose_factor = float(depth_um)
        self.z_extent_m = float(depth_um) * DOSE_TO_METRE
        self.refresh_style()

    def refresh_style(self) -> None:  # overridden by concrete items
        pass

    # -- marked edge (shared by rect & polygon) --------------------------- #
    # Subclasses provide the vertices and centre in LOCAL coords.
    def _edge_corners_local(self):
        raise NotImplementedError

    def _edge_center_local(self) -> "QPointF":
        pts = self._edge_corners_local()
        n = len(pts) or 1
        return QPointF(sum(p.x() for p in pts) / n, sum(p.y() for p in pts) / n)

    def center_scene(self) -> "QPointF":
        """Scene-space centre of the shape (for the mill-order overlay)."""
        try:
            local = self._edge_center_local()
        except NotImplementedError:
            local = QPointF(0.0, 0.0)   # text: anchor is the centre
        return self.mapToScene(local)

    def _edge_segment_local(self, index: int):
        """(p1, p2, mid) for 1-based edge *index* in vertex order."""
        pts = self._edge_corners_local()
        n = len(pts)
        p1 = pts[(index - 1) % n]
        p2 = pts[index % n]
        mid = QPointF((p1.x() + p2.x()) / 2.0, (p1.y() + p2.y()) / 2.0)
        return p1, p2, mid

    def _edge_scene_dir(self, index: int):
        """Screen direction ('top'/'bottom'/'left'/'right') the edge faces."""
        _, _, mid = self._edge_segment_local(index)
        cs = self.mapToScene(self._edge_center_local())
        ms = self.mapToScene(mid)
        dx, dy = ms.x() - cs.x(), ms.y() - cs.y()   # scene y-down
        if abs(dx) >= abs(dy):
            return "right" if dx > 0 else "left"
        return "bottom" if dy > 0 else "top"

    def index_for_direction(self, direction):
        """Edge index whose midpoint best points in *direction* (screen)."""
        tv = _DIR_VEC[direction]
        pts = self._edge_corners_local()
        n = len(pts)
        cs = self.mapToScene(self._edge_center_local())
        best, best_score = None, -2.0
        for idx in range(1, n + 1):
            _, _, mid = self._edge_segment_local(idx)
            ms = self.mapToScene(mid)
            dx, dy = ms.x() - cs.x(), ms.y() - cs.y()
            length = math.hypot(dx, dy) or 1.0
            score = (dx / length) * tv[0] + (dy / length) * tv[1]
            if score > best_score:
                best, best_score = idx, score
        return best

    def direction_for_index(self, index):
        pts = self._edge_corners_local()
        if index is None or not (1 <= index <= len(pts)):
            return None
        return self._edge_scene_dir(index)

    def marked_edge_dir(self):
        return self.direction_for_index(self.edge_sel_marked_edge)

    def set_marked_edge_dir(self, direction) -> None:
        if direction is None:
            self.edge_sel_marked_edge = None
        else:
            self.edge_sel_marked_edge = self.index_for_direction(direction)
        self.update()

    def _auto_marked_index(self, target_scene=(0.0, 0.0)):
        """Estimate SmartFIB's auto-scan marked edge: the edge facing the frame
        centre (layout origin). Reproduces every explicit mark in the sample
        files and completes symmetric lamella patterns. INFERRED -- confirm the
        real auto-scan rule against SmartFIB.
        """
        pts = self._edge_corners_local()
        if len(pts) < 2:
            return None
        cs = self.mapToScene(self._edge_center_local())
        dx, dy = target_scene[0] - cs.x(), target_scene[1] - cs.y()
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            return None
        direction = ("right" if dx > 0 else "left") if abs(dx) >= abs(dy) \
            else ("bottom" if dy > 0 else "top")
        return self.index_for_direction(direction)

    def effective_marked_edge(self):
        """Return (index, is_auto). Explicit value wins; otherwise, if the shape
        is in auto-scan mode, return the estimated auto edge."""
        if self.edge_sel_marked_edge is not None:
            return self.edge_sel_marked_edge, False
        if getattr(self, "edge_sel_auto_scan_angle", False):
            return self._auto_marked_index(), True
        return None, False

    def _paint_marked_edge(self, painter) -> None:
        idx, is_auto = self.effective_marked_edge()
        pts = self._edge_corners_local()
        if idx is None or not (1 <= idx <= len(pts)):
            return
        p1, p2, _mid = self._edge_segment_local(idx)
        red = QColor("#ff3b30")
        if is_auto:
            red.setAlpha(170)             # dimmer for an auto-scan estimate
        ep = QPen(red)
        ep.setCosmetic(True)              # constant on-screen width at any zoom
        ep.setWidthF(2.5 if is_auto else 3.5)
        if is_auto:
            ep.setStyle(Qt.DashLine)      # dashed = elymaker's auto estimate
        painter.setPen(ep)
        painter.setBrush(Qt.NoBrush)
        painter.drawLine(p1, p2)


class RectItem(QGraphicsObject, ShapeItemMixin):
    """Editable rectangle. Position = anchor (layout x,y); rotation via Qt.

    The rect is drawn centred on its origin with the model width/height so that
    Qt's item rotation pivots about the anchor, matching how SmartFIB rotates a
    box about its placement point.
    """

    def __init__(self, width: float, height: float) -> None:
        QGraphicsObject.__init__(self)
        ShapeItemMixin.__init__(self)
        self.width = float(width)
        self.height = float(height)          # signed (SmartFIB convention)
        self.imaging = False
        self.edge_sel_auto_scan_angle = True
        self.edge_sel_marked_edge: Optional[int] = None
        self.area_fast_deg: Optional[float] = None
        self.ignored = False
        self.setFlags(
            QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemSendsGeometryChanges
        )

    # vertices in SmartFIB order, expressed in local (y-down) coords:
    # layout offset (dx,dy) from the anchor maps to local (dx, -dy).
    def _edge_corners_local(self):
        w, h = self.width, self.height
        return [QPointF(0.0, 0.0), QPointF(w, 0.0),
                QPointF(w, -h), QPointF(0.0, -h)]

    def _edge_center_local(self) -> QPointF:
        return QPointF(self.width / 2.0, -self.height / 2.0)

    # -- Qt geometry ------------------------------------------------------ #
    def _local_rect(self) -> QRectF:
        """Rectangle in local coords: anchor at (0,0), extends by (w, -h).

        (x,y) is the anchor CORNER; the box extends by the signed (width,
        height) in layout space. Layout->local flips y, so the far corner is at
        local (width, -height). Normalised so width/height sign is handled.
        """
        return QRectF(0.0, 0.0, self.width, -self.height).normalized()

    def boundingRect(self) -> QRectF:
        return self._local_rect().adjusted(-2, -2, 2, 2)

    def paint(self, painter, option, widget=None) -> None:
        lr = self._local_rect()
        painter.setPen(_pen(self))
        painter.setBrush(_brush(self))
        painter.drawRect(lr)
        if self.isSelected():
            hp = QPen(QColor("#ffd166"))
            hp.setCosmetic(True)
            hp.setWidthF(1.5)
            hp.setStyle(Qt.DashLine)
            painter.setPen(hp)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(lr)
        if self.imaging:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor("#ffd166")))
            painter.drawEllipse(lr.center(), 1.2, 1.2)
        self._paint_marked_edge(painter)

    def center_layout(self):
        """Box centre in layout coords (um, y-up) -- accounts for rotation."""
        c_scene = self.mapToScene(self._local_rect().center())
        return scene_to_layout(c_scene.x(), c_scene.y())

    def refresh_style(self) -> None:
        self.update()

    # -- model round-trip ------------------------------------------------- #
    @classmethod
    def from_model(cls, r: Rect) -> "RectItem":
        it = cls(r.width, r.height)
        sx, sy = layout_to_scene(r.x, r.y)
        it.setPos(sx, sy)
        it.setRotation(layout_angle_to_scene(r.angle_deg))
        it.dose_factor = r.dose_factor
        it.z_extent_m = r.z_extent_m
        it.exposure = r.exposure
        it.imaging = r.imaging
        it.edge_sel_auto_scan_angle = r.edge_sel_auto_scan_angle
        it.edge_sel_marked_edge = r.edge_sel_marked_edge
        it.area_fast_deg = r.area_fast_deg
        it.ignored = r.ignored
        return it

    def to_model(self) -> Rect:
        lx, ly = scene_to_layout(self.pos().x(), self.pos().y())
        return Rect(
            dose_factor=self.dose_factor,
            z_extent_m=self.z_extent_m,
            exposure=self.exposure,
            x=lx,
            y=ly,
            width=self.width,
            height=self.height,
            angle_deg=scene_angle_to_layout(self.rotation()),
            imaging=self.imaging,
            edge_sel_auto_scan_angle=self.edge_sel_auto_scan_angle,
            edge_sel_marked_edge=self.edge_sel_marked_edge,
            area_fast_deg=self.area_fast_deg,
            ignored=self.ignored,
        )


class PolygonItem(QGraphicsObject, ShapeItemMixin):
    def __init__(self, pts_scene: List[Tuple[float, float]]) -> None:
        QGraphicsObject.__init__(self)
        ShapeItemMixin.__init__(self)
        self._pts = [QPointF(x, y) for x, y in pts_scene]
        self.contours = "0 "
        self.edge_sel_auto_scan_angle = True
        self.edge_sel_marked_edge: Optional[int] = None
        self.ignored = False
        self.setFlags(
            QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemSendsGeometryChanges
        )

    def _qpoly(self) -> QPolygonF:
        return QPolygonF(self._pts)

    def _edge_corners_local(self):
        return list(self._pts)

    def boundingRect(self) -> QRectF:
        return self._qpoly().boundingRect().adjusted(-2, -2, 2, 2)

    def paint(self, painter, option, widget=None) -> None:
        painter.setPen(_pen(self))
        painter.setBrush(_brush(self))
        painter.drawPolygon(self._qpoly())
        if self.isSelected():
            hp = QPen(QColor("#ffd166"))
            hp.setCosmetic(True); hp.setWidthF(1.5); hp.setStyle(Qt.DashLine)
            painter.setPen(hp); painter.setBrush(Qt.NoBrush)
            painter.drawPolygon(self._qpoly())
        self._paint_marked_edge(painter)

    def refresh_style(self) -> None:
        self.update()

    @classmethod
    def from_model(cls, p: Polygon) -> "PolygonItem":
        pts = [layout_to_scene(x, y) for x, y in p.points]
        it = cls(pts)
        it.dose_factor = p.dose_factor
        it.z_extent_m = p.z_extent_m
        it.exposure = p.exposure
        it.contours = p.contours
        it.edge_sel_auto_scan_angle = p.edge_sel_auto_scan_angle
        it.edge_sel_marked_edge = p.edge_sel_marked_edge
        it.ignored = p.ignored
        return it

    def to_model(self) -> Polygon:
        off = self.pos()
        pts_layout = [
            scene_to_layout(p.x() + off.x(), p.y() + off.y()) for p in self._pts
        ]
        return Polygon(
            dose_factor=self.dose_factor,
            z_extent_m=self.z_extent_m,
            exposure=self.exposure,
            points=pts_layout,
            contours=self.contours,
            edge_sel_auto_scan_angle=self.edge_sel_auto_scan_angle,
            edge_sel_marked_edge=self.edge_sel_marked_edge,
            ignored=self.ignored,
        )


class ArcItem(QGraphicsPathItem, ShapeItemMixin):
    def __init__(self, cx, cy, r1, r2, a1, a2, arc_dir=None) -> None:
        QGraphicsPathItem.__init__(self)
        ShapeItemMixin.__init__(self)
        self.cx, self.cy = float(cx), float(cy)     # layout um (y-up)
        self.r1, self.r2 = float(r1), float(r2)
        self.a1_deg, self.a2_deg = float(a1), float(a2)
        self.arc_dir = arc_dir
        self.setFlags(
            QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable
        )
        self._rebuild()

    def _rebuild(self) -> None:
        path = QPainterPath()
        n = max(6, int(abs(self.a2_deg - self.a1_deg) / 4))
        outer = []
        for k in range(n + 1):
            ang = math.radians(self.a1_deg + (self.a2_deg - self.a1_deg) * k / n)
            x, y = layout_to_scene(self.cx + self.r1 * math.cos(ang),
                                   self.cy + self.r1 * math.sin(ang))
            outer.append(QPointF(x, y))
        inner = []
        for k in range(n + 1):
            ang = math.radians(self.a2_deg + (self.a1_deg - self.a2_deg) * k / n)
            x, y = layout_to_scene(self.cx + self.r2 * math.cos(ang),
                                   self.cy + self.r2 * math.sin(ang))
            inner.append(QPointF(x, y))
        poly = QPolygonF(outer + inner)
        path.addPolygon(poly)
        path.closeSubpath()
        self.setPath(path)
        self.setPen(_pen(self))
        self.setBrush(_brush(self))

    def refresh_style(self) -> None:
        self._rebuild()

    def center_scene(self):
        sx, sy = layout_to_scene(self.cx, self.cy)
        return self.mapToScene(QPointF(sx, sy))

    @classmethod
    def from_model(cls, a: Arc) -> "ArcItem":
        it = cls(a.cx, a.cy, a.r1, a.r2, a.a1_deg, a.a2_deg, a.arc_dir)
        it.dose_factor = a.dose_factor
        it.z_extent_m = a.z_extent_m
        it.exposure = a.exposure
        it._rebuild()
        return it

    def to_model(self) -> Arc:
        # cx,cy are stored in layout coords; apply the item's scene-space move
        # offset by round-tripping through scene space.
        off = self.pos()
        sx, sy = layout_to_scene(self.cx, self.cy)
        cx2, cy2 = scene_to_layout(sx + off.x(), sy + off.y())
        return Arc(
            dose_factor=self.dose_factor,
            z_extent_m=self.z_extent_m,
            exposure=self.exposure,
            cx=cx2,
            cy=cy2,
            r1=self.r1,
            r2=self.r2,
            a1_deg=self.a1_deg,
            a2_deg=self.a2_deg,
            arc_dir=self.arc_dir,
        )


class TextItem(QGraphicsObject, ShapeItemMixin):
    def __init__(self, text: str, height: float) -> None:
        QGraphicsObject.__init__(self)
        ShapeItemMixin.__init__(self)
        self.text = text
        self.text_height = float(height)     # um
        self.font_name = "sans serif"
        self.angle_deg = 0.0
        self.setFlags(
            QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemSendsGeometryChanges
        )

    def _qfont(self) -> QFont:
        f = QFont(self.font_name)
        f.setPointSizeF(max(1.0, self.text_height))
        return f

    def boundingRect(self) -> QRectF:
        br = QFontMetricsF(self._qfont()).boundingRect(self.text or " ")
        w, h = br.width(), br.height()
        return QRectF(-w / 2 - 1, -h / 2 - 1, w + 2, h + 2)

    def paint(self, painter, option, widget=None) -> None:
        # The scene is y-down and the view is not flipped, so text drawn normally
        # is already upright -- no counter-flip. (x,y) is the text ANCHOR; we
        # centre the glyph box on it so the label sits on its coordinate rather
        # than hanging down-and-right from it.
        painter.save()
        f = self._qfont()
        painter.setFont(f)
        br = QFontMetricsF(f).boundingRect(self.text or " ")
        ox = -(br.x() + br.width() / 2.0)
        oy = -(br.y() + br.height() / 2.0)
        painter.setPen(_pen(self))
        painter.drawText(QPointF(ox, oy), self.text)
        painter.restore()
        if self.isSelected():
            hp = QPen(QColor("#ffd166"))
            hp.setCosmetic(True); hp.setWidthF(1.0); hp.setStyle(Qt.DashLine)
            painter.setPen(hp); painter.setBrush(Qt.NoBrush)
            painter.drawRect(self.boundingRect())

    def refresh_style(self) -> None:
        self.update()

    @classmethod
    def from_model(cls, t: Text) -> "TextItem":
        it = cls(t.text, t.height)
        sx, sy = layout_to_scene(t.x, t.y)
        it.setPos(sx, sy)
        it.font_name = t.font
        it.angle_deg = t.angle_deg
        it.dose_factor = t.dose_factor
        it.z_extent_m = t.z_extent_m
        it.exposure = t.exposure
        return it

    def to_model(self) -> Text:
        lx, ly = scene_to_layout(self.pos().x(), self.pos().y())
        return Text(
            dose_factor=self.dose_factor,
            z_extent_m=self.z_extent_m,
            exposure=self.exposure,
            x=lx,
            y=ly,
            height=self.text_height,
            text=self.text,
            font=self.font_name,
            angle_deg=self.angle_deg,
        )


def item_from_shape(shape: Shape):
    """Factory: build the right item for a model shape."""
    if isinstance(shape, Rect):
        return RectItem.from_model(shape)
    if isinstance(shape, Polygon):
        return PolygonItem.from_model(shape)
    if isinstance(shape, Arc):
        return ArcItem.from_model(shape)
    if isinstance(shape, Text):
        return TextItem.from_model(shape)
    raise TypeError("Unknown shape type: %r" % type(shape))


def offset_shape_model(shape: Shape, dx_um: float, dy_um: float) -> Shape:
    """Return a deep copy of *shape* translated by (dx, dy) in layout um.

    Used by Duplicate. Handles each shape's coordinate carrier (rect/text
    anchor, arc centre, polygon points).
    """
    import copy as _copy
    s = _copy.deepcopy(shape)
    if isinstance(s, Rect):
        s.x += dx_um
        s.y += dy_um
    elif isinstance(s, Text):
        s.x += dx_um
        s.y += dy_um
    elif isinstance(s, Arc):
        s.cx += dx_um
        s.cy += dy_um
    elif isinstance(s, Polygon):
        s.points = [(px + dx_um, py + dy_um) for (px, py) in s.points]
    return s


def mirror_shape_model(shape: Shape, axis: str) -> Shape:
    """Return a deep copy of *shape* reflected across the layout x- or y-axis
    (through the origin). ``axis='x'`` reflects vertically (y -> -y);
    ``axis='y'`` reflects horizontally (x -> -x).

    Exact for polygons and axis-aligned rects/text/arcs; for a rotated rect the
    anchor is reflected and the angle negated/adjusted (correct for the common
    axis-aligned lamella case).
    """
    import copy as _copy
    s = _copy.deepcopy(shape)
    fx = -1.0 if axis == "y" else 1.0
    fy = -1.0 if axis == "x" else 1.0

    if isinstance(s, Rect):
        # reflect the two defining corners, then re-form top-left/w>0/h<0
        cx0, cy0 = s.x * fx, s.y * fy
        cx1, cy1 = (s.x + s.width) * fx, (s.y + s.height) * fy
        x0, x1 = sorted((cx0, cx1))
        y0, y1 = sorted((cy0, cy1))
        s.x, s.y = x0, y1                 # top-left anchor
        s.width = x1 - x0                 # > 0
        s.height = -(y1 - y0)             # < 0
        s.angle_deg = (-s.angle_deg) if axis == "x" else (180.0 - s.angle_deg)
    elif isinstance(s, Text):
        s.x *= fx
        s.y *= fy
    elif isinstance(s, Arc):
        s.cx *= fx
        s.cy *= fy
        # reflect sweep angles about the mirror axis (0 deg = +x)
        if axis == "x":
            s.a1_deg, s.a2_deg = -s.a1_deg, -s.a2_deg
        else:
            s.a1_deg, s.a2_deg = 180.0 - s.a1_deg, 180.0 - s.a2_deg
    elif isinstance(s, Polygon):
        # reflect points; reflection reverses winding, so reverse to keep it CCW
        s.points = [(px * fx, py * fy) for (px, py) in s.points][::-1]
    return s


def layout_bbox(shape: Shape):
    """Axis-aligned bounding box of a model shape in layout um: (x0,y0,x1,y1)."""
    if isinstance(shape, Rect):
        xs = [shape.x, shape.x + shape.width]
        ys = [shape.y, shape.y + shape.height]
    elif isinstance(shape, Polygon):
        xs = [p[0] for p in shape.points] or [0.0]
        ys = [p[1] for p in shape.points] or [0.0]
    elif isinstance(shape, Arc):
        xs = [shape.cx - shape.r1, shape.cx + shape.r1]
        ys = [shape.cy - shape.r1, shape.cy + shape.r1]
    elif isinstance(shape, Text):
        half_w = max(1, len(shape.text)) * shape.height * 0.3
        xs = [shape.x - half_w, shape.x + half_w]
        ys = [shape.y - shape.height / 2.0, shape.y + shape.height / 2.0]
    else:
        xs = ys = [0.0]
    return (min(xs), min(ys), max(xs), max(ys))
