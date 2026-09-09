import pytest

from elymaker import LayoutBuilder, get_probe, PROBES
from elymaker.reader import loads
from elymaker.model import Rect, Polygon, Arc, Text


def test_probe_library_seeded():
    assert "30kV:15nA" in PROBES
    p = get_probe("30kV:700pA")
    assert p.current_A == pytest.approx(7e-10)
    assert p.diameter_m == pytest.approx(2.1e-7)


def test_get_probe_unknown_lists_options():
    with pytest.raises(KeyError) as e:
        get_probe("nope")
    assert "Known probes" in str(e.value)


def test_build_waffle_roundtrips():
    b = LayoutBuilder("MyWaffle", fov_um=600)
    b.rect(x=0, y=20, width=18, height=6, depth_um=15, probe="30kV:15nA")
    b.rect(x=0, y=-20, width=18, height=6, depth_um=15, probe="30kV:15nA",
           angle_deg=180, area_fast_deg=180)
    lay = b.build()
    assert lay.shape_count() == 2

    lay2 = loads(b.dumps())
    shapes = [s for _, _, s in lay2.iter_shapes()]
    assert all(isinstance(s, Rect) for s in shapes)
    # depth coupling honoured
    for s in shapes:
        assert s.dose_factor == pytest.approx(15)
        assert s.z_extent_m == pytest.approx(15e-6)
    # SmartFIB negative-height convention by default
    assert shapes[0].height == pytest.approx(-6)
    # probe resolved from library
    assert shapes[0].exposure.probe.name == "30kV:15nA"


def test_height_sign_override():
    b = LayoutBuilder()
    b.rect(x=0, y=0, width=10, height=5, depth_um=2, height_sign=+1)
    r = b.build().structures[0].layer_refs[0].shapes[0]
    assert r.height == pytest.approx(5)


def test_all_shape_types_build_and_parse():
    b = LayoutBuilder("AllShapes")
    b.rect(x=0, y=0, width=10, height=4, depth_um=2, probe="30kV:300pA")
    b.polygon([(0, 0), (5, 0), (5, 5)], depth_um=3, probe="30kV:100pA")
    b.arc(cx=0, cy=0, r1=6, r2=5, a1_deg=120, a2_deg=240, depth_um=15,
          probe="30kV:700pA", arc_dir="cw")
    b.text(x=-20, y=-2, text="X", height=6, depth_um=2, probe="30kV:700pA")
    lay2 = loads(b.dumps())
    kinds = [type(s).__name__ for _, _, s in lay2.iter_shapes()]
    assert kinds == ["Rect", "Polygon", "Arc", "Text"]

    arc = [s for _, _, s in lay2.iter_shapes() if isinstance(s, Arc)][0]
    assert arc.arc_dir == "cw"
    txt = [s for _, _, s in lay2.iter_shapes() if isinstance(s, Text)][0]
    assert txt.text == "X"


def test_custom_recipe_per_shape():
    b = LayoutBuilder(recipe=r"D:\default.epm")
    b.rect(x=0, y=0, width=5, height=2, depth_um=2)
    b.rect(x=0, y=5, width=5, height=2, depth_um=2, recipe=r"D:\other.epm")
    shapes = b.build().structures[0].layer_refs[0].shapes
    assert shapes[0].exposure.file == r"D:\default.epm"
    assert shapes[1].exposure.file == r"D:\other.epm"
