"""Headless GUI-logic tests.

These construct real Qt objects using the *offscreen* platform plugin (no
display needed) and verify the safety-critical logic: that a shape drawn in the
scene converts to the correct model geometry, that a full scene round-trips
through the .ely writer/reader unchanged, and that the properties panel writes
edits back correctly.

They are skipped automatically if no Qt binding is installed.
"""

import glob
import os

import pytest

# ensure a windowless Qt platform before any QApplication is created
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

qtpy = pytest.importorskip("qtpy")

from elymaker.reader import read_ely, loads  # noqa: E402
from elymaker.writer import dumps  # noqa: E402
from elymaker.model import Rect, Polygon, Arc, Text  # noqa: E402

HERE = os.path.dirname(__file__)
FIXTURES = sorted(glob.glob(os.path.join(HERE, "fixtures", "*.ely")))


@pytest.fixture()
def scene(qapp, qt_cleanup):
    from elymaker.gui.scene import PatternScene
    s = PatternScene()
    qt_cleanup.append(s)
    return s


# --------------------------------------------------------------------------- #
# item <-> model geometry
# --------------------------------------------------------------------------- #
def test_rect_item_roundtrip(qapp):
    from elymaker.gui.items import RectItem
    from elymaker.model import Rect as MRect

    src = MRect(dose_factor=15, z_extent_m=15e-6, x=12.0, y=-6.0,
                width=18.0, height=-6.79, angle_deg=0.0, imaging=True,
                edge_sel_marked_edge=3)
    it = RectItem.from_model(src)
    out = it.to_model()
    assert out.x == pytest.approx(12.0)
    assert out.y == pytest.approx(-6.0)      # y sign preserved through flip
    assert out.width == pytest.approx(18.0)
    assert out.height == pytest.approx(-6.79)
    assert out.imaging is True
    assert out.edge_sel_marked_edge == 3
    assert out.dose_factor == pytest.approx(15)
    assert out.z_extent_m == pytest.approx(15e-6)


def test_rect_y_up_convention(qapp):
    """A rect placed at layout +y sits above the origin (scene -y)."""
    from elymaker.gui.items import RectItem
    from elymaker.model import Rect as MRect
    it = RectItem.from_model(MRect(dose_factor=2, z_extent_m=2e-6,
                                   x=0, y=25, width=5, height=-2))
    assert it.pos().y() == pytest.approx(-25)   # scene y is down
    assert it.to_model().y == pytest.approx(25)


def test_polygon_item_roundtrip(qapp):
    from elymaker.gui.items import PolygonItem
    from elymaker.model import Polygon as MPoly
    pts = [(-11.9, 4.06), (-11.9, -4.26), (-10.86, -4.26), (-10.86, 4.06)]
    src = MPoly(dose_factor=2, z_extent_m=2e-6, points=pts, edge_sel_marked_edge=3)
    out = PolygonItem.from_model(src).to_model()
    assert len(out.points) == 4
    for (ax, ay), (bx, by) in zip(pts, out.points):
        assert ax == pytest.approx(bx)
        assert ay == pytest.approx(by)
    assert out.edge_sel_marked_edge == 3


def test_arc_item_roundtrip(qapp):
    from elymaker.gui.items import ArcItem
    from elymaker.model import Arc as MArc
    src = MArc(dose_factor=15, z_extent_m=15e-6, cx=-20.16, cy=-0.585,
               r1=5.99, r2=5.125, a1_deg=300, a2_deg=420, arc_dir="cw")
    out = ArcItem.from_model(src).to_model()
    assert out.cx == pytest.approx(-20.16)
    assert out.cy == pytest.approx(-0.585)
    assert out.r1 == pytest.approx(5.99)
    assert out.a1_deg == pytest.approx(300)
    assert out.arc_dir == "cw"


def test_text_item_roundtrip(qapp):
    from elymaker.gui.items import TextItem
    from elymaker.model import Text as MText
    src = MText(dose_factor=2, z_extent_m=2e-6, x=-20.35, y=-2.28,
                height=6, text="X", font="sans serif")
    out = TextItem.from_model(src).to_model()
    assert out.text == "X"
    assert out.x == pytest.approx(-20.35)
    assert out.y == pytest.approx(-2.28)
    assert out.height == pytest.approx(6)


# --------------------------------------------------------------------------- #
# full scene -> file -> scene
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", FIXTURES, ids=[os.path.basename(f) for f in FIXTURES])
def test_scene_load_export_matches(scene, path):
    """Load a real file into the scene, export back, compare geometry."""
    original = read_ely(path)
    scene.load_layout(original)
    exported = scene.to_layout()

    a = [s for _, _, s in original.iter_shapes()]
    b = [s for _, _, s in exported.iter_shapes()]
    assert len(a) == len(b)
    for x, y in zip(a, b):
        assert type(x) is type(y)
        assert x.dose_factor == pytest.approx(y.dose_factor)
        assert x.z_extent_m == pytest.approx(y.z_extent_m)
        if isinstance(x, Rect):
            assert x.x == pytest.approx(y.x)
            assert x.y == pytest.approx(y.y)
            assert x.width == pytest.approx(y.width)
            assert x.height == pytest.approx(y.height)
            assert x.imaging == y.imaging
            assert x.edge_sel_marked_edge == y.edge_sel_marked_edge
        elif isinstance(x, Polygon):
            assert len(x.points) == len(y.points)
            for (ax, ay), (bx, by) in zip(x.points, y.points):
                assert ax == pytest.approx(bx)
                assert ay == pytest.approx(by)
        elif isinstance(x, Arc):
            assert x.cx == pytest.approx(y.cx)
            assert x.r1 == pytest.approx(y.r1)
            assert x.arc_dir == y.arc_dir
        elif isinstance(x, Text):
            assert x.text == y.text


@pytest.mark.parametrize("path", FIXTURES, ids=[os.path.basename(f) for f in FIXTURES])
def test_scene_export_writes_valid_ely(scene, path):
    """Scene export must produce a file the core can re-read to same shape count."""
    scene.load_layout(read_ely(path))
    text = dumps(scene.to_layout())
    reparsed = loads(text)
    assert reparsed.shape_count() == read_ely(path).shape_count()


def test_scene_preserves_layer_exposure(scene):
    """The layer-level default exposure (Markup_Standard) survives a scene cycle."""
    path = os.path.join(HERE, "fixtures", "Markup_Standard.ely")
    scene.load_layout(read_ely(path))
    exported = scene.to_layout()
    assert exported.layers[0].exposure is not None
    assert exported.layers[0].exposure.file.endswith(".epm")


# --------------------------------------------------------------------------- #
# draw a waffle from scratch in the scene, save, reload
# --------------------------------------------------------------------------- #
def test_build_waffle_in_scene(scene):
    from elymaker.gui.items import RectItem
    from elymaker.gui.canvas import ShapeDefaults
    from elymaker.probes import get_probe
    from elymaker.model import Exposure

    scene.layout_name = "SceneWaffle"
    d = ShapeDefaults()
    d.depth_um = 15.0
    d.exposure_factory = lambda: Exposure(file=r"D:\r.epm", probe=get_probe("30kV:15nA"))

    # top box at layout y=+25, bottom at y=-25
    for yy in (25.0, -25.0):
        it = RectItem(18.0, -6.0)
        from elymaker.gui.coords import layout_to_scene
        sx, sy = layout_to_scene(0.0, yy)
        it.setPos(sx, sy)
        d.apply(it)
        scene.add_shape_item(it)

    exported = scene.to_layout()
    assert exported.name == "SceneWaffle"
    ys = sorted(s.y for _, _, s in exported.iter_shapes())
    assert ys[0] == pytest.approx(-25.0)
    assert ys[1] == pytest.approx(25.0)
    for _, _, s in exported.iter_shapes():
        assert s.dose_factor == pytest.approx(15)
        assert s.exposure.probe.name == "30kV:15nA"


# --------------------------------------------------------------------------- #
# properties panel writes edits back to the item
# --------------------------------------------------------------------------- #
def test_properties_panel_edits(qapp):
    from elymaker.gui.items import RectItem
    from elymaker.gui.properties import PropertiesPanel
    from elymaker.model import Rect as MRect

    it = RectItem.from_model(MRect(dose_factor=2, z_extent_m=2e-6,
                                   x=0, y=0, width=10, height=-4))
    panel = PropertiesPanel()
    panel.set_item(it)

    panel.depth.setValue(20.0)
    panel.w.setValue(18.5)
    panel.imaging.setChecked(True)
    panel.marked_edge.choose("bottom")   # bottom -> index 3 (tentative mapping)

    out = it.to_model()
    assert out.dose_factor == pytest.approx(20.0)
    assert out.z_extent_m == pytest.approx(20e-6)
    assert out.width == pytest.approx(18.5)
    assert out.imaging is True
    assert out.edge_sel_marked_edge == 3
