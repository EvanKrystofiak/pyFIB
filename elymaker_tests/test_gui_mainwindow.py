"""End-to-end MainWindow smoke tests, offscreen.

Exercises the full wired application: construct the window, open a real file,
draw rectangles through the view's interaction path, edit via the properties
panel, save, and reload -- the sequence a user actually performs. Skipped if no
Qt binding is present.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("qtpy")

from qtpy.QtCore import QPointF  # noqa: E402

from elymaker.reader import read_ely  # noqa: E402

HERE = os.path.dirname(__file__)


@pytest.fixture()
def win(qapp, qt_cleanup):
    from elymaker.gui.main_window import MainWindow
    w = MainWindow()
    w.show()
    qt_cleanup.append(w)
    return w


def _drag_rect(view, p0, p1):
    """Simulate a rubber-band rectangle draw in scene coordinates."""
    from elymaker.gui.items import RectItem
    view._start = QPointF(*p0)
    it = RectItem(0.1, -0.1)
    it.setPos(*p0)
    view.defaults.apply(it)
    view.scene().addItem(it)
    view._temp_item = it
    view._update_temp_rect(QPointF(*p1))
    view._commit_rect()


def test_window_constructs(win):
    assert "elymaker" in win.windowTitle()
    assert win.scene.shape_count() if hasattr(win.scene, "shape_count") else True


def test_open_real_file(win):
    win.scene.load_layout(read_ely(os.path.join(HERE, "fixtures", "Waffle_bulk.ely")))
    win._revalidate()
    assert win.count_label.text() == "2 shapes"
    assert "OK" in win.validation.toPlainText()


def test_draw_and_save_reload(win, tmp_path):
    from elymaker.gui.canvas import RECT
    win.new_layout()
    win.view.set_tool(RECT)
    # two opposed boxes; scene y is down, so layout +y is negative scene y
    _drag_rect(win.view, (0, -25), (18, -19))   # layout y ~ +22
    _drag_rect(win.view, (0, 25), (18, 31))     # layout y ~ -28
    win._revalidate()
    out = win.scene.to_layout()
    assert out.shape_count() == 2

    path = str(tmp_path / "drawn.ely")
    win._path = path
    win._write(path)
    reloaded = read_ely(path)
    assert reloaded.shape_count() == 2
    # mill order preserved: first drawn (top box, layout +y) comes first
    ys = [s.y for _, _, s in reloaded.iter_shapes()]
    assert ys[0] > 0 and ys[1] < 0


def test_properties_edit_via_window(win):
    from elymaker.gui.canvas import RECT
    win.new_layout()
    win.view.set_tool(RECT)
    _drag_rect(win.view, (0, 0), (10, 6))
    item = win.scene.shape_items()[0]
    item.setSelected(True)
    win.props.set_item(item)
    win.props.depth.setValue(30.0)
    assert item.depth_um == pytest.approx(30.0)
    assert item.z_extent_m == pytest.approx(30e-6)


def test_selection_repeated_no_crash(win):
    """Re-selecting different items must not use-after-free the form widgets."""
    from elymaker.gui.canvas import RECT
    win.new_layout()
    win.view.set_tool(RECT)
    _drag_rect(win.view, (0, 0), (10, 6))
    _drag_rect(win.view, (20, 0), (30, 6))
    items = win.scene.shape_items()
    # flip selection several times
    for _ in range(3):
        win.props.set_item(items[0])
        win.props.set_item(items[1])
        win.props.set_item(None)
    # after all that, editing still works
    win.props.set_item(items[0])
    win.props.depth.setValue(12.0)
    assert items[0].depth_um == pytest.approx(12.0)
