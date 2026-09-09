"""Regression tests for the rectangle anchor convention (v0.2.2).

The .ely RECT anchor (x,y) is the top-left CORNER (y-up), and the box extends by
the signed (width, height). Earlier the editor drew boxes CENTERED on the
anchor, which put every rectangle off by half its size on import. These tests
pin the corner convention at the render level. Model round-trip is unaffected
(reader/writer store x,y,width,height verbatim) and is covered elsewhere.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_rect_anchor_is_corner_not_center(qapp):
    from elymaker.gui.items import RectItem
    from elymaker.model import Rect as MRect

    it = RectItem.from_model(MRect(dose_factor=15, z_extent_m=15e-6,
                                   x=86.0, y=-6.0, width=18.0, height=-6.8))
    # the model anchor is preserved
    m = it.to_model()
    assert (m.x, m.y) == pytest.approx((86.0, -6.0))
    # but the box CENTRE is offset from the anchor by (+w/2, +h/2)
    cx, cy = it.center_layout()
    assert cx == pytest.approx(86.0 + 9.0)
    assert cy == pytest.approx(-6.0 + (-3.4))
    # explicitly NOT centered on the anchor (the old bug)
    assert abs(cx - 86.0) > 1.0
    assert abs(cy - (-6.0)) > 1.0


def test_local_rect_extends_from_origin(qapp):
    from elymaker.gui.items import RectItem

    it = RectItem(18.0, -6.8)          # width>0, height<0
    lr = it._local_rect()
    # anchor corner is at local (0,0); box extends right and down (y-down scene)
    assert lr.left() == pytest.approx(0.0)
    assert lr.top() == pytest.approx(0.0)
    assert lr.width() == pytest.approx(18.0)
    assert lr.height() == pytest.approx(6.8)


def test_apilayout_lamella_gap_centered_on_origin(qapp, qt_cleanup):
    """The classic lamella pair should straddle y=0 under the corner convention."""
    from elymaker.gui.scene import PatternScene
    from elymaker.gui.items import RectItem
    from elymaker.reader import read_ely

    path = os.path.join(os.path.dirname(__file__), "fixtures", "APILayout.ely")
    sc = PatternScene()
    qt_cleanup.append(sc)
    sc.load_layout(read_ely(path))

    rects = [it for it in sc.shape_items() if isinstance(it, RectItem)]
    # take the widest matched top/bottom pair (width ~20) nearest the origin
    wide = [it for it in rects if abs(it.width) > 19]
    assert len(wide) >= 2
    centers = sorted(it.center_layout()[1] for it in wide[:2])
    gap_mid = (centers[0] + centers[1]) / 2.0
    # milling boxes sit above and below the lamella, which is at ~y=0
    assert abs(gap_mid) < 1.0
