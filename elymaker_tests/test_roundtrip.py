"""Round-trip fidelity tests against the real .ely files.

These are the ground-truth test: every example file that the Crossbeam produced
must parse, re-serialize, and re-parse to a numerically identical model, with no
shapes or key attributes lost.
"""

import glob
import os

import pytest

from elymaker.reader import read_ely, loads
from elymaker.writer import dumps
from elymaker.model import Rect, Polygon, Arc, Text

HERE = os.path.dirname(__file__)
FIXTURES = sorted(glob.glob(os.path.join(HERE, "fixtures", "*.ely")))


def test_fixtures_present():
    assert len(FIXTURES) == 12, "expected 12 example .ely files"


@pytest.mark.parametrize("path", FIXTURES, ids=[os.path.basename(f) for f in FIXTURES])
def test_parses(path):
    lay = read_ely(path)
    assert lay.name
    assert lay.version_attr == "2.0"
    assert lay.shape_count() >= 1


@pytest.mark.parametrize("path", FIXTURES, ids=[os.path.basename(f) for f in FIXTURES])
def test_roundtrip_identical(path):
    lay = read_ely(path)
    text = dumps(lay)
    lay2 = loads(text)

    s1 = [s for _, _, s in lay.iter_shapes()]
    s2 = [s for _, _, s in lay2.iter_shapes()]
    assert len(s1) == len(s2)

    for a, b in zip(s1, s2):
        assert type(a) is type(b)
        assert a.dose_factor == pytest.approx(b.dose_factor)
        assert a.z_extent_m == pytest.approx(b.z_extent_m, abs=0, rel=1e-9)

        # exposure / probe fidelity
        assert (a.exposure is None) == (b.exposure is None)
        if a.exposure is not None:
            assert a.exposure.file == b.exposure.file
            assert (a.exposure.probe is None) == (b.exposure.probe is None)
            if a.exposure.probe is not None:
                assert a.exposure.probe.name == b.exposure.probe.name
                assert a.exposure.probe.current_A == pytest.approx(b.exposure.probe.current_A)
                assert a.exposure.probe.diameter_m == pytest.approx(b.exposure.probe.diameter_m)

        if isinstance(a, Rect):
            for attr in ("x", "y", "width", "height", "angle_deg"):
                assert getattr(a, attr) == pytest.approx(getattr(b, attr))
            assert a.imaging == b.imaging
            assert a.ignored == b.ignored
            assert a.edge_sel_marked_edge == b.edge_sel_marked_edge
            assert a.edge_sel_auto_scan_angle == b.edge_sel_auto_scan_angle
            assert (a.area_fast_deg is None) == (b.area_fast_deg is None)
            if a.area_fast_deg is not None:
                assert a.area_fast_deg == pytest.approx(b.area_fast_deg)
        elif isinstance(a, Polygon):
            assert len(a.points) == len(b.points)
            for (ax, ay), (bx, by) in zip(a.points, b.points):
                assert ax == pytest.approx(bx)
                assert ay == pytest.approx(by)
            assert a.contours == b.contours
            assert a.edge_sel_marked_edge == b.edge_sel_marked_edge
        elif isinstance(a, Arc):
            for attr in ("cx", "cy", "r1", "r2", "a1_deg", "a2_deg"):
                assert getattr(a, attr) == pytest.approx(getattr(b, attr))
            assert a.arc_dir == b.arc_dir
        elif isinstance(a, Text):
            assert a.text == b.text
            assert a.font == b.font
            assert a.height == pytest.approx(b.height)


@pytest.mark.parametrize("path", FIXTURES, ids=[os.path.basename(f) for f in FIXTURES])
def test_no_extra_leftover_attrs(path):
    """We modelled every attribute in the examples: nothing should land in extra."""
    lay = read_ely(path)
    for _st, lref, sh in lay.iter_shapes():
        assert sh.extra == {}, "unmodelled shape attrs: %r" % sh.extra
        if sh.exposure is not None:
            assert sh.exposure.extra == {}, "unmodelled exposure attrs: %r" % sh.exposure.extra
            if sh.exposure.probe is not None:
                assert sh.exposure.probe.extra == {}, "unmodelled probe attrs"
        assert lref.extra == {}


@pytest.mark.parametrize("path", FIXTURES, ids=[os.path.basename(f) for f in FIXTURES])
def test_dose_depth_coupling(path):
    """z_extent == dose_factor * 1e-6 in every example file."""
    lay = read_ely(path)
    for _st, _lr, sh in lay.iter_shapes():
        assert sh.z_extent_m == pytest.approx(sh.dose_factor * 1e-6, rel=1e-9)


def test_output_is_valid_xml():
    import xml.etree.ElementTree as ET
    for path in FIXTURES:
        lay = read_ely(path)
        ET.fromstring(dumps(lay))  # raises if malformed
