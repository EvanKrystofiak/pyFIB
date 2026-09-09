"""Tests for the v0.2.1 feedback items: probe ordering, the marked-edge picker,
and edge geometry. Pure-logic tests need no Qt; the widget test uses offscreen.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# --------------------------------------------------------------------------- #
# probes ordered by current (pA -> nA), not alphabetically
# --------------------------------------------------------------------------- #
def test_probe_names_by_current_orders_pa_before_na():
    from elymaker.probes import probe_names_by_current, PROBES

    names = probe_names_by_current()
    # currents must be monotonically non-decreasing
    currents = [PROBES[n].current_A for n in names]
    assert currents == sorted(currents)
    # the highest-current probes come last, after all pA probes
    assert names[-1] == "30kV:30nA"
    assert names[-2] == "30kV:15nA"
    # 10pA is smallest
    assert names[0] == "30kV:10pA"
    # sanity: alphabetical order would have put 100pA before 10pA/20pA
    assert names.index("30kV:20pA") < names.index("30kV:100pA")


# --------------------------------------------------------------------------- #
# edge geometry + index<->direction mapping (pure)
# --------------------------------------------------------------------------- #
def test_rect_edge_index_depends_on_height_sign(qapp):
    """marked_edge is a vertex-order index: which physical edge it is depends
    on the box's height sign. edge 3 = bottom for h<0 but = top for h>0."""
    from elymaker.gui.items import RectItem

    down = RectItem(20, -2.7)      # extends down (typical)
    assert down.direction_for_index(3) == "bottom"
    assert down.direction_for_index(1) == "top"
    assert down.index_for_direction("bottom") == 3
    assert down.index_for_direction("top") == 1

    up = RectItem(0.2, 2.0)        # extends up (positive height)
    assert up.direction_for_index(3) == "top"     # sign flips the physical edge
    assert up.index_for_direction("top") == 3
    assert up.index_for_direction("bottom") == 1


def test_edge_segment_local_positions(qapp):
    from elymaker.gui.items import RectItem
    # width=10, height=-4  -> local corners (0,0)(10,0)(10,4)(0,4)
    it = RectItem(10, -4)
    _, _, mid1 = it._edge_segment_local(1)   # top edge, y=0
    assert (mid1.x(), mid1.y()) == pytest.approx((5.0, 0.0))
    _, _, mid3 = it._edge_segment_local(3)   # bottom edge, y=4
    assert (mid3.x(), mid3.y()) == pytest.approx((5.0, 4.0))
    _, _, mid4 = it._edge_segment_local(4)   # left edge, x=0
    assert (mid4.x(), mid4.y()) == pytest.approx((0.0, 2.0))


# --------------------------------------------------------------------------- #
# RectItem marked-edge direction helpers
# --------------------------------------------------------------------------- #
def test_rectitem_marked_edge_direction_helpers(qapp):
    from elymaker.gui.items import RectItem

    it = RectItem(10, -4)          # w>0, h<0
    assert it.marked_edge_dir() is None
    it.set_marked_edge_dir("bottom")
    assert it.edge_sel_marked_edge == 3      # bottom is edge 3 for h<0
    assert it.marked_edge_dir() == "bottom"
    it.set_marked_edge_dir("left")
    assert it.edge_sel_marked_edge == 4
    assert it.marked_edge_dir() == "left"
    it.set_marked_edge_dir(None)
    assert it.edge_sel_marked_edge is None


def test_polygon_marked_edge_follows_point_order(qapp):
    """The lamella file's left box: 4-pt polygon whose edge 3 is the inner
    (right) edge facing the lamella."""
    from elymaker.gui.items import PolygonItem
    from elymaker.gui.coords import layout_to_scene
    pts = [layout_to_scene(x, y) for x, y in
           [(9.78, 19.64), (9.78, 11.32), (10.82, 11.32), (10.82, 19.64)]]
    poly = PolygonItem(pts)
    assert poly.direction_for_index(3) == "right"
    assert poly.direction_for_index(1) == "left"
    assert poly.index_for_direction("right") == 3


# --------------------------------------------------------------------------- #
# EdgeSelector widget: pure direction picker
# --------------------------------------------------------------------------- #
def test_edge_selector_widget(qapp, qt_cleanup):
    from elymaker.gui.widgets import EdgeSelector

    sel = EdgeSelector()
    qt_cleanup.append(sel)
    got = []
    sel.edgeChanged.connect(lambda d: got.append(d))

    sel.choose("bottom")
    assert sel.direction() == "bottom"
    assert got[-1] == "bottom"

    sel.set_direction("top")            # programmatic: no emit
    assert sel.direction() == "top"

    sel.choose(None)
    assert sel.direction() is None
    assert got[-1] is None


# --------------------------------------------------------------------------- #
# marked edge survives a full scene export/reload via the panel
# --------------------------------------------------------------------------- #
def test_marked_edge_roundtrips_through_scene(qapp, qt_cleanup):
    from elymaker.gui.scene import PatternScene
    from elymaker.gui.items import RectItem
    from elymaker.writer import dumps
    from elymaker.reader import loads

    sc = PatternScene()
    qt_cleanup.append(sc)
    it = RectItem(18, -6)
    it.set_marked_edge_dir("bottom")
    sc.add_shape_item(it)

    reparsed = loads(dumps(sc.to_layout()))
    rects = [s for _, _, s in reparsed.iter_shapes()]
    assert rects[0].edge_sel_marked_edge == it.edge_sel_marked_edge
