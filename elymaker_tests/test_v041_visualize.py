"""Tests for v0.4.1: colour-by-current and the mill-order overlay."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _rect_with_probe(pname, depth=10.0):
    from elymaker.gui.items import RectItem
    from elymaker.probes import get_probe
    from elymaker.model import Exposure
    it = RectItem(15, -8)
    it.set_depth_um(depth)
    it.exposure = Exposure(file="r.epm", probe=get_probe(pname))
    return it


def test_color_mode_default_is_depth(qapp):
    from elymaker.gui import items
    items.set_color_mode(items.COLOR_BY_DEPTH)   # reset (module global)
    assert items.color_mode() == "depth"


def test_current_ramp_is_monotonic_and_distinct(qapp):
    from elymaker.gui.items import current_color
    lo = current_color(1e-11)     # 10 pA
    mid = current_color(1e-9)     # 1 nA
    hi = current_color(1.5e-8)    # 15 nA
    # blue-ish low, red-ish high
    assert lo.blue() > lo.red()
    assert hi.red() > hi.blue()
    assert len({lo.rgb(), mid.rgb(), hi.rgb()}) == 3


def test_no_probe_gets_neutral_color(qapp):
    from elymaker.gui.items import current_color
    c = current_color(None)
    # neutral grey: channels close together, not saturated
    assert abs(c.red() - c.green()) < 40 and abs(c.green() - c.blue()) < 40


def test_item_current_reads_probe(qapp):
    from elymaker.gui.items import item_current
    it = _rect_with_probe("30kV:100pA")
    assert item_current(it) == pytest.approx(1e-10)
    from elymaker.gui.items import RectItem
    assert item_current(RectItem(5, -5)) is None   # no exposure


def test_set_color_mode_changes_base_color(qapp):
    from elymaker.gui import items
    it = _rect_with_probe("30kV:15nA", depth=2.0)
    items.set_color_mode(items.COLOR_BY_DEPTH)
    depth_c = items._base_color(it, 255).rgb()
    items.set_color_mode(items.COLOR_BY_CURRENT)
    current_c = items._base_color(it, 255).rgb()
    items.set_color_mode(items.COLOR_BY_DEPTH)       # restore
    assert depth_c != current_c


# --------------------------------------------------------------------------- #
# mill-order overlay
# --------------------------------------------------------------------------- #
@pytest.fixture()
def win(qapp, qt_cleanup):
    from elymaker.gui.main_window import MainWindow
    w = MainWindow(); w.show(); w.view.resize(300, 300)
    qt_cleanup.append(w)
    w.new_layout()
    return w


def test_center_scene_defined_for_all_item_types(win):
    from elymaker.gui.items import RectItem, PolygonItem, ArcItem, TextItem
    from elymaker.gui.coords import layout_to_scene
    items_ = [
        RectItem(10, -4),
        PolygonItem([layout_to_scene(0, 0), layout_to_scene(5, 0), layout_to_scene(5, 5)]),
        ArcItem(3, -2, 5, 4, 300, 420),
        TextItem("X", 6),
    ]
    for it in items_:
        win.scene.add_shape_item(it)
        c = it.center_scene()
        assert c is not None and hasattr(c, "x")


def test_show_order_toggle_and_render(win):
    from qtpy.QtGui import QImage, QPainter
    from elymaker.gui.items import RectItem
    from elymaker.gui.coords import layout_to_scene
    for x in (0.0, 30.0, 60.0):
        it = RectItem(12, -6); sx, sy = layout_to_scene(x, 0); it.setPos(sx, sy)
        win.scene.add_shape_item(it); win._on_shape_created(it)
    win.view.fit_all()
    win.view.set_show_order(True)
    assert win.view._show_order is True
    img = QImage(300, 300, QImage.Format_ARGB32); img.fill(0)
    p = QPainter(img); win.view.render(p); p.end()
    # amber badge rings present
    amber = sum(1 for y in range(300) for x in range(300)
                if abs(img.pixelColor(x, y).red() - 255) < 30
                and abs(img.pixelColor(x, y).green() - 209) < 40
                and abs(img.pixelColor(x, y).blue() - 102) < 50)
    assert amber > 0
