"""Tests for v0.6.0: numeric nudge, mirror across X/Y, measurement readout."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture()
def win(qapp, qt_cleanup):
    from elymaker.gui.main_window import MainWindow
    w = MainWindow(); w.show(); w.view.resize(300, 300)
    qt_cleanup.append(w)
    w.new_layout()
    return w


def _add_rect(win, w=10.0, h=-4.0, x=0.0, y=0.0):
    from elymaker.gui.items import RectItem
    from elymaker.gui.coords import layout_to_scene
    it = RectItem(w, h)
    sx, sy = layout_to_scene(x, y)
    it.setPos(sx, sy)
    win.view.defaults.apply(it)
    win.scene.add_shape_item(it)
    win._on_shape_created(it)
    return it


# --------------------------------------------------------------------------- #
# mirror model helper (pure geometry)
# --------------------------------------------------------------------------- #
def test_mirror_rect_across_x_axis():
    from elymaker.gui.items import mirror_shape_model
    from elymaker.model import Rect
    # box spanning x[0,10] y[3,7] (anchor top-left (0,7), h=-4)
    r = Rect(dose_factor=2, z_extent_m=2e-6, x=0, y=7, width=10, height=-4)
    m = mirror_shape_model(r, "x")   # reflect y -> -y : now y[-7,-3]
    assert m.x == pytest.approx(0.0)
    assert m.y == pytest.approx(-3.0)          # new top edge = -3
    assert m.width == pytest.approx(10.0)
    assert m.height == pytest.approx(-4.0)


def test_mirror_polygon_reflects_points():
    from elymaker.gui.items import mirror_shape_model
    from elymaker.model import Polygon
    p = Polygon(dose_factor=2, z_extent_m=2e-6,
                points=[(1, 2), (3, 2), (3, 5)])
    m = mirror_shape_model(p, "y")             # x -> -x
    xs = sorted(pt[0] for pt in m.points)
    assert xs == pytest.approx([-3, -3, -1])


def test_mirror_across_x_creates_symmetric_pair(win):
    a = _add_rect(win, w=20, h=-6, x=-10, y=25)   # box above origin
    a.setSelected(True)
    win.mirror_selected("x")
    shapes = [s for _, _, s in win.scene.to_layout().iter_shapes()]
    assert len(shapes) == 2
    # centres are mirror images across y=0
    from elymaker.gui.items import layout_bbox
    c0 = layout_bbox(shapes[0]); c1 = layout_bbox(shapes[1])
    cy0 = (c0[1] + c0[3]) / 2; cy1 = (c1[1] + c1[3]) / 2
    assert cy0 == pytest.approx(-cy1)


def test_mirror_is_undoable(win):
    a = _add_rect(win); a.setSelected(True)
    win.mirror_selected("y")
    assert win.scene.to_layout().shape_count() == 2
    win.do_undo()
    assert win.scene.to_layout().shape_count() == 1


# --------------------------------------------------------------------------- #
# numeric nudge
# --------------------------------------------------------------------------- #
def test_arrow_nudge_moves_and_commits(win):
    from qtpy.QtCore import Qt, QEvent
    from qtpy.QtGui import QKeyEvent
    it = _add_rect(win, x=0, y=0)
    it.setSelected(True)
    before = it.to_model().x
    ev = QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.NoModifier)
    win.view.keyPressEvent(ev)
    assert it.to_model().x == pytest.approx(before + 1.0)   # 1 um step
    # undoable (one step); undo rebuilds items, so query the scene afresh
    win.do_undo()
    assert win.scene.shape_items()[0].to_model().x == pytest.approx(before)


def test_arrow_nudge_shift_is_bigger(win):
    from qtpy.QtCore import Qt, QEvent
    from qtpy.QtGui import QKeyEvent
    it = _add_rect(win, x=0, y=0)
    it.setSelected(True)
    ev = QKeyEvent(QEvent.KeyPress, Qt.Key_Up, Qt.ShiftModifier)
    win.view.keyPressEvent(ev)
    assert it.to_model().y == pytest.approx(5.0)            # +y up, x5 step


# --------------------------------------------------------------------------- #
# measurement readout
# --------------------------------------------------------------------------- #
def test_measure_readout_shows_selection_size(win):
    it = _add_rect(win, w=18.0, h=-6.0, x=0, y=0)
    win.scene.clearSelection(); it.setSelected(True)
    win._update_measure()
    txt = win.measure_label.text()
    assert "18.00" in txt and "6.00" in txt


def test_measure_readout_clears_with_no_selection(win):
    _add_rect(win)
    win.scene.clearSelection()
    win._update_measure()
    assert win.measure_label.text() == ""


def test_draw_size_label_during_drag(win):
    win._on_draw_size(12.5, 4.0)
    assert "12.50" in win.measure_label.text() and "4.00" in win.measure_label.text()
