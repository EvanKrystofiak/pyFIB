import glob
import os

import pytest

from elymaker import LayoutBuilder, validate
from elymaker.reader import read_ely
from elymaker.preview import to_svg

HERE = os.path.dirname(__file__)
FIXTURES = sorted(glob.glob(os.path.join(HERE, "fixtures", "*.ely")))


def test_clean_layout_no_errors():
    b = LayoutBuilder("Clean", fov_um=600)
    b.rect(x=0, y=0, width=18, height=6, depth_um=15, probe="30kV:15nA")
    issues = validate(b.build())
    errors = [i for i in issues if i.startswith("ERROR")]
    assert errors == []


def test_degenerate_rect_flagged():
    b = LayoutBuilder("Bad")
    b.rect(x=0, y=0, width=0, height=6, depth_um=15, probe="30kV:15nA")
    issues = validate(b.build())
    assert any(i.startswith("ERROR") and "degenerate" in i for i in issues)


def test_bad_polygon_flagged():
    b = LayoutBuilder("Bad")
    b.polygon([(0, 0), (1, 1)], depth_um=2, probe="30kV:100pA")
    issues = validate(b.build())
    assert any("need >= 3" in i for i in issues)


def test_out_of_fov_warns():
    b = LayoutBuilder("Big", fov_um=20)  # tiny FOV
    b.rect(x=100, y=0, width=18, height=6, depth_um=15, probe="30kV:15nA")
    issues = validate(b.build())
    assert any("outside field of view" in i for i in issues)


def test_dose_depth_mismatch_warns():
    b = LayoutBuilder("Mismatch")
    r = b.rect(x=0, y=0, width=10, height=4, depth_um=2, probe="30kV:100pA")
    r.z_extent_m = 9e-6   # break the coupling by hand
    issues = validate(b.build())
    assert any("implies z_extent" in i for i in issues)


@pytest.mark.parametrize("path", FIXTURES, ids=[os.path.basename(f) for f in FIXTURES])
def test_real_files_validate_without_errors(path):
    """Real, instrument-authored files should not trip ERROR-level checks."""
    lay = read_ely(path)
    issues = validate(lay)
    errors = [i for i in issues if i.startswith("ERROR")]
    assert errors == [], "unexpected errors in real file: %r" % errors


@pytest.mark.parametrize("path", FIXTURES, ids=[os.path.basename(f) for f in FIXTURES])
def test_preview_renders(path):
    lay = read_ely(path)
    svg = to_svg(lay)
    assert svg.startswith("<svg")
    assert "</svg>" in svg
