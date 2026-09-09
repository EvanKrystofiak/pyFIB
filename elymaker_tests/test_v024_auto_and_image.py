"""Tests for v0.2.4: auto-scan marked-edge estimate + FIB image placement.

Auto boxes (edge_sel_auto_scan_angle=true, no explicit marked edge) get an
estimated marked edge = the edge facing the frame centre. This reproduces every
explicit mark in the sample files and completes symmetric lamella patterns. The
estimate is rendered dashed and is clearly labelled inferred.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(__file__)


def test_explicit_marked_edge_is_not_auto(qapp):
    from elymaker.gui.items import RectItem
    it = RectItem(20, -2.7)
    it.edge_sel_marked_edge = 3
    idx, is_auto = it.effective_marked_edge()
    assert idx == 3 and is_auto is False


def test_auto_scan_estimates_edge_toward_origin(qapp, qt_cleanup):
    """Lower lamella box (below origin, h<0) should auto-mark its TOP edge."""
    from elymaker.gui.scene import PatternScene
    from elymaker.gui.items import RectItem
    from elymaker.gui.coords import layout_to_scene

    sc = PatternScene()
    qt_cleanup.append(sc)
    it = RectItem(20, -2.7)               # extends down
    sx, sy = layout_to_scene(-10.0, -1.77)   # centre below the origin
    it.setPos(sx, sy)
    it.edge_sel_marked_edge = None
    it.edge_sel_auto_scan_angle = True
    sc.add_shape_item(it)

    idx, is_auto = it.effective_marked_edge()
    assert is_auto is True
    assert it.direction_for_index(idx) == "top"   # faces up toward lamella at y=0


def test_auto_scan_reproduces_all_explicit_marks(qapp, qt_cleanup):
    """On APILayout, the toward-centre estimate must equal every explicit mark."""
    from elymaker.gui.scene import PatternScene
    from elymaker.gui.items import RectItem, PolygonItem
    from elymaker.reader import read_ely

    sc = PatternScene()
    qt_cleanup.append(sc)
    sc.load_layout(read_ely(os.path.join(HERE, "fixtures", "APILayout.ely")))
    checked = 0
    for it in sc.shape_items():
        if not isinstance(it, (RectItem, PolygonItem)):
            continue
        if it.edge_sel_marked_edge is not None:
            est = it._auto_marked_index()
            assert est == it.edge_sel_marked_edge
            checked += 1
    assert checked == 6


def test_no_auto_when_auto_scan_false(qapp):
    from elymaker.gui.items import RectItem
    it = RectItem(20, -2.7)
    it.edge_sel_marked_edge = None
    it.edge_sel_auto_scan_angle = False
    idx, is_auto = it.effective_marked_edge()
    assert idx is None and is_auto is False


# --------------------------------------------------------------------------- #
# FIB image placement: default bottom-left at origin
# --------------------------------------------------------------------------- #
def test_calibration_default_centre_at_origin(qapp, qt_cleanup):
    from elymaker.gui.calibration_dialog import CalibrationDialog

    dlg = CalibrationDialog(1000, 800)
    qt_cleanup.append(dlg)
    # default: HFW 50 um, anchor = centre at (0,0) (SmartFIB convention)
    cal = dlg.calibration()
    assert cal.center_x_um == pytest.approx(0.0)
    assert cal.center_y_um == pytest.approx(0.0)


def test_calibration_bottom_left_option(qapp, qt_cleanup):
    from elymaker.gui.calibration_dialog import CalibrationDialog
    dlg = CalibrationDialog(1000, 800)
    qt_cleanup.append(dlg)
    dlg.anchor.setCurrentIndex(1)   # bottom-left at origin
    cal = dlg.calibration()
    bl_x = cal.center_x_um - cal.width_um / 2.0
    bl_y = cal.center_y_um - cal.height_um / 2.0
    assert bl_x == pytest.approx(0.0)
    assert bl_y == pytest.approx(0.0)


def test_scene_rect_places_bottom_left(qapp):
    """A bottom-left-at-origin calibration puts the pixmap in the +x/-scene-y box."""
    from elymaker.gui.coords import ImageCalibration
    cal = ImageCalibration(pixel_size_um=0.05, image_w_px=1000, image_h_px=800)
    cal.center_x_um = cal.width_um / 2.0
    cal.center_y_um = cal.height_um / 2.0
    x, y, w, h = cal.scene_rect_um()
    assert x == pytest.approx(0.0)          # left at layout x=0
    assert (y + h) == pytest.approx(0.0)    # bottom (scene) at layout y=0


# --------------------------------------------------------------------------- #
# text is centred on its anchor and upright (v0.2.6)
# --------------------------------------------------------------------------- #
def test_text_centered_on_anchor(qapp, qt_cleanup):
    from qtpy.QtGui import QImage, QPainter
    from qtpy.QtCore import QRectF
    from elymaker.gui.scene import PatternScene
    from elymaker.gui.items import TextItem

    sc = PatternScene(); qt_cleanup.append(sc)
    it = TextItem("X", 6.0); it.setPos(0, 0)
    sc.add_shape_item(it)
    view = QRectF(-8, -8, 16, 16); W = H = 200
    img = QImage(W, H, QImage.Format_ARGB32); img.fill(0)
    p = QPainter(img); sc.render(p, QRectF(0, 0, W, H), view); p.end()
    xs = [x for y in range(H) for x in range(W) if img.pixelColor(x, y).alpha() > 0]
    ys = [y for y in range(H) for x in range(W) if img.pixelColor(x, y).alpha() > 0]
    # glyph straddles the anchor in both axes (anchor is image centre)
    assert min(xs) < W / 2 < max(xs)
    assert min(ys) < H / 2 < max(ys)


def test_text_model_roundtrip_unchanged(qapp):
    from elymaker.gui.items import TextItem
    from elymaker.model import Text
    it = TextItem.from_model(Text(dose_factor=2, z_extent_m=2e-6,
                                  x=-20.35, y=-2.28, height=6, text="X"))
    out = it.to_model()
    assert (out.x, out.y, out.height, out.text) == (
        pytest.approx(-20.35), pytest.approx(-2.28), pytest.approx(6), "X")
