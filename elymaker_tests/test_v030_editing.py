"""Tests for v0.3.0: undo/redo (per-action, depth 5), duplicate, pan/hand tool."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(__file__)


@pytest.fixture()
def win(qapp, qt_cleanup):
    from elymaker.gui.main_window import MainWindow
    w = MainWindow()
    w.show()
    qt_cleanup.append(w)
    w.new_layout()
    return w


def _add_rect(win, w=10.0, h=-4.0, x=0.0, y=0.0):
    from elymaker.gui.items import RectItem
    from elymaker.gui.coords import layout_to_scene
    it = RectItem(w, h)
    sx, sy = layout_to_scene(x, y)
    it.setPos(sx, sy)
    win.view.defaults.apply(it)    # depth 15 etc., as the canvas does on create
    win.scene.add_shape_item(it)
    win._on_shape_created(it)       # same path the canvas uses on commit
    return it


# --------------------------------------------------------------------------- #
# undo / redo
# --------------------------------------------------------------------------- #
def test_undo_add_shape(win):
    assert win.scene.to_layout().shape_count() == 0
    _add_rect(win)
    assert win.scene.to_layout().shape_count() == 1
    win.do_undo()
    assert win.scene.to_layout().shape_count() == 0
    win.do_redo()
    assert win.scene.to_layout().shape_count() == 1


def test_undo_parameter_change(win):
    it = _add_rect(win)
    it.setSelected(True)
    win.props.set_item(it)
    win.props.depth.setValue(30.0)           # user edit -> changed -> commit
    assert win.scene.shape_items()[0].depth_um == pytest.approx(30.0)
    win.do_undo()                            # undo the parameter change only
    assert win.scene.shape_items()[0].depth_um == pytest.approx(15.0)
    # the shape still exists (undo was per-action, not the whole add)
    assert win.scene.to_layout().shape_count() == 1


def test_undo_is_per_action(win):
    # add, then change a parameter: two separate undo steps
    it = _add_rect(win)
    it.setSelected(True); win.props.set_item(it)
    win.props.depth.setValue(25.0)
    win.do_undo()   # revert depth
    assert win.scene.shape_items()[0].depth_um == pytest.approx(15.0)
    win.do_undo()   # revert add
    assert win.scene.to_layout().shape_count() == 0


def test_undo_delete(win):
    it = _add_rect(win)
    it.setSelected(True)
    win.delete_selected()
    assert win.scene.to_layout().shape_count() == 0
    win.do_undo()
    assert win.scene.to_layout().shape_count() == 1


def test_undo_depth_capped_at_5(win):
    for i in range(8):
        _add_rect(win, x=float(i) * 12)
    assert win.scene.to_layout().shape_count() == 8
    # only 5 undo steps are retained
    for _ in range(5):
        win.do_undo()
    assert not win.undo_mgr.can_undo()
    # 8 adds - 5 undos = 3 shapes remain
    assert win.scene.to_layout().shape_count() == 3


def test_undo_preserves_mill_order_and_marked_edge(win):
    a = _add_rect(win, x=0)
    b = _add_rect(win, x=20)
    b.set_marked_edge_dir("bottom")
    b.setSelected(True); win.props.set_item(b)
    win.props.marked_edge.choose("bottom")   # commit a marked-edge change
    order_before = [s.x for _, _, s in win.scene.to_layout().iter_shapes()]
    win.do_undo()                            # undo the marked-edge edit
    lay = win.scene.to_layout()
    order_after = [s.x for _, _, s in lay.iter_shapes()]
    assert order_before == order_after       # order preserved


def test_no_image_undo(win, qt_cleanup):
    from qtpy.QtGui import QPixmap
    from elymaker.gui.coords import ImageCalibration
    _add_rect(win)
    pix = QPixmap(50, 40); pix.fill()
    win.scene.set_image(pix, ImageCalibration.from_hfw(50.0, 50, 40))
    assert win.scene.calibration is not None
    win.do_undo()                # undo the shape add; image must remain
    assert win.scene.calibration is not None
    assert win.scene.to_layout().shape_count() == 0


# --------------------------------------------------------------------------- #
# duplicate
# --------------------------------------------------------------------------- #
def test_duplicate_offsets_and_orders(win):
    a = _add_rect(win, x=0.0, y=0.0)
    a.setSelected(True)
    win.duplicate_selected()
    shapes = [s for _, _, s in win.scene.to_layout().iter_shapes()]
    assert len(shapes) == 2
    # copy sits immediately after original in mill order, offset +5/-5 um
    assert shapes[1].x == pytest.approx(shapes[0].x + 5.0)
    assert shapes[1].y == pytest.approx(shapes[0].y - 5.0)


def test_duplicate_is_undoable(win):
    a = _add_rect(win)
    a.setSelected(True)
    win.duplicate_selected()
    assert win.scene.to_layout().shape_count() == 2
    win.do_undo()
    assert win.scene.to_layout().shape_count() == 1


def test_duplicate_inserts_after_each_original(win):
    a = _add_rect(win, x=0)
    b = _add_rect(win, x=100)
    for it in (a, b):
        it.setSelected(True)
    win.duplicate_selected()
    xs = [round(s.x, 1) for _, _, s in win.scene.to_layout().iter_shapes()]
    # each copy immediately follows its original: A, A', B, B'
    assert xs == [0.0, 5.0, 100.0, 105.0]


# --------------------------------------------------------------------------- #
# pan / hand tool
# --------------------------------------------------------------------------- #
def test_hand_tool_sets_scroll_drag(win):
    from elymaker.gui.canvas import HAND, SELECT
    from qtpy.QtWidgets import QGraphicsView
    win.view.set_tool(HAND)
    assert win.view.dragMode() == QGraphicsView.ScrollHandDrag
    win.view.set_tool(SELECT)
    assert win.view.dragMode() == QGraphicsView.RubberBandDrag


def test_middle_mouse_pan_scrolls(win):
    from qtpy.QtCore import QPoint, Qt, QEvent
    from qtpy.QtGui import QMouseEvent
    win.view.resize(300, 300)
    win.scene.setSceneRect(-500, -500, 1000, 1000)
    h0 = win.view.horizontalScrollBar().value()
    press = QMouseEvent(QEvent.MouseButtonPress, QPoint(150, 150),
                        Qt.MiddleButton, Qt.MiddleButton, Qt.NoModifier)
    win.view.mousePressEvent(press)
    assert win.view._panning is True
    move = QMouseEvent(QEvent.MouseMove, QPoint(110, 150),
                       Qt.NoButton, Qt.MiddleButton, Qt.NoModifier)
    win.view.mouseMoveEvent(move)
    release = QMouseEvent(QEvent.MouseButtonRelease, QPoint(110, 150),
                          Qt.MiddleButton, Qt.NoButton, Qt.NoModifier)
    win.view.mouseReleaseEvent(release)
    assert win.view._panning is False
    assert win.view.horizontalScrollBar().value() != h0   # view scrolled
