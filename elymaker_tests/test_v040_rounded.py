"""Tests for v0.4.0: rounded-corner boxes (FIB-legal polygons)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# --------------------------------------------------------------------------- #
# core geometry
# --------------------------------------------------------------------------- #
def test_rounded_polygon_point_count_and_bounds():
    from elymaker.geometry import rounded_rect_polygon
    pts = rounded_rect_polygon(0, 20, 40, -20, radius=5, side="top", segments=10)
    assert len(pts) == 10 * 2 + 2          # two arcs + two sharp corners
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    assert min(xs) == pytest.approx(0.0) and max(xs) == pytest.approx(40.0)
    assert min(ys) == pytest.approx(0.0) and max(ys) == pytest.approx(20.0)


def test_rounded_keeps_opposite_corners_sharp():
    from elymaker.geometry import rounded_rect_polygon
    pts = [(round(a, 6), round(b, 6))
           for a, b in rounded_rect_polygon(0, 20, 40, -20, 5, "top", 8)]
    assert (0.0, 0.0) in pts and (40.0, 0.0) in pts     # bottom corners sharp
    assert (0.0, 20.0) not in pts and (40.0, 20.0) not in pts   # top rounded


def test_radius_clamped_and_zero_is_plain_rect():
    from elymaker.geometry import rounded_rect_polygon, max_radius
    assert max_radius(40, -20, "top") == pytest.approx(20.0)   # min(w/2,h)
    # huge radius clamps, still valid
    pts = rounded_rect_polygon(0, 20, 40, -20, 1e6, "top", 6)
    assert all(0 <= p[0] <= 40 and 0 <= p[1] <= 20 for p in pts)
    # zero radius -> 4-point rectangle
    assert len(rounded_rect_polygon(0, 20, 40, -20, 0, "top", 6)) == 4


def test_each_side_rounds_correct_corners():
    from elymaker.geometry import rounded_rect_polygon
    corners = {"BL": (0.0, 0.0), "BR": (40.0, 0.0),
               "TR": (40.0, 20.0), "TL": (0.0, 20.0)}
    rounded_by_side = {"top": {"TL", "TR"}, "bottom": {"BL", "BR"},
                       "left": {"BL", "TL"}, "right": {"BR", "TR"}}
    for side, rounded in rounded_by_side.items():
        pts = [(round(a, 6), round(b, 6))
               for a, b in rounded_rect_polygon(0, 20, 40, -20, 5, side, 8)]
        for name, c in corners.items():
            if name in rounded:
                assert c not in pts, (side, name)
            else:
                assert c in pts, (side, name)


# --------------------------------------------------------------------------- #
# builder + round-trip through the writer/reader
# --------------------------------------------------------------------------- #
def test_builder_rounded_rect_roundtrips():
    from elymaker.builder import LayoutBuilder
    from elymaker.writer import dumps
    from elymaker.reader import loads
    from elymaker.model import Polygon
    b = LayoutBuilder("round", fov_um=100)
    b.rounded_rect(0, 20, 40, -20, radius=5, side="top", segments=12,
                   depth_um=2.0, probe="30kV:100pA")
    lay = loads(dumps(b.build()))
    shapes = [s for _, _, s in lay.iter_shapes()]
    assert len(shapes) == 1 and isinstance(shapes[0], Polygon)
    assert len(shapes[0].points) == 12 * 2 + 2      # survives round-trip


# --------------------------------------------------------------------------- #
# GUI: rounded-box tool + convert-in-place
# --------------------------------------------------------------------------- #
@pytest.fixture()
def win(qapp, qt_cleanup):
    from elymaker.gui.main_window import MainWindow
    w = MainWindow()
    w.show()
    qt_cleanup.append(w)
    w.new_layout()
    return w


def test_rounded_box_tool_creates_polygon(win):
    from qtpy.QtCore import QPointF
    from elymaker.gui.canvas import RBOX
    from elymaker.gui.items import RectItem, PolygonItem
    win.view.set_tool(RBOX)
    win.defaults.rounded_side = "top"
    win.defaults.rounded_radius_um = 4.0
    win.defaults.rounded_segments = 10
    # simulate a drag: press, update temp, release
    win.view._start = QPointF(0, -20)
    it = RectItem(0.1, -0.1); it.setPos(0, -20); win.view.defaults.apply(it)
    win.view.scene().addItem(it); win.view._temp_item = it
    win.view._update_temp_rect(QPointF(40, 0))
    win.view._commit_rounded_box()
    shapes = win.scene.shape_items()
    assert len(shapes) == 1 and isinstance(shapes[0], PolygonItem)
    assert len(shapes[0]._pts) == 10 * 2 + 2


def test_round_selected_converts_rect_in_place(win):
    from elymaker.gui.items import RectItem, PolygonItem
    from elymaker.gui.coords import layout_to_scene
    # add two rects, mill order A then B
    for x in (0.0, 60.0):
        it = RectItem(40, -20)
        sx, sy = layout_to_scene(x, 20)
        it.setPos(sx, sy); win.view.defaults.apply(it)
        win.scene.add_shape_item(it); win._on_shape_created(it)
    a, b = win.scene.shape_items()
    win.scene.clearSelection()
    a.set_marked_edge_dir("bottom")
    a.setSelected(True)
    win.defaults.rounded_side = "top"; win.defaults.rounded_radius_um = 0.0
    win.round_selected()
    shapes = win.scene.shape_items()
    # A converted to polygon in place; B still a rect; order preserved
    assert isinstance(shapes[0], PolygonItem)
    assert isinstance(shapes[1], RectItem)
    # depth carried over, marked side preserved
    assert shapes[0].marked_edge_dir() == "bottom"
    # undoable
    win.do_undo()
    assert isinstance(win.scene.shape_items()[0], RectItem)


def test_rounded_box_auto_radius_default(win):
    # radius 0 -> auto 10% of shorter side (=2 for a 40x20 box)
    from elymaker.gui.canvas import ShapeDefaults
    d = ShapeDefaults()
    d.rounded_radius_um = 0.0
    assert d.effective_radius(40, -20) == pytest.approx(2.0)
    d.rounded_radius_um = 7.5
    assert d.effective_radius(40, -20) == pytest.approx(7.5)
