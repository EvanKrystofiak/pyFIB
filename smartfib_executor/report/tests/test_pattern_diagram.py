"""Dimensioned pattern drawing (v0.42.0), checked against Evan's default.ely."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from ..pattern_diagram import analyze_layout, diagram_svg, fmt_current, fmt_um, pattern_section
from ..html_report import DEFAULT_PATTERN


def _info():
    from elymaker.reader import read_ely
    return analyze_layout(read_ely(str(DEFAULT_PATTERN)), "default.ely")


def test_default_pattern_parts():
    info = _info()
    assert len(info.parts) == 4 and all(p.mirrored for p in info.parts)
    assert [fmt_current(p.rep.current_A) for p in info.parts] == ["700 pA", "300 pA",
                                                                   "100 pA", "50 pA"]
    assert {round(p.rep.depth_um, 3) for p in info.parts} == {12.0}
    assert [fmt_um(p.rep.width_um) for p in info.parts] == ["16.5", "15", "13.5", "12"]
    assert [fmt_um(p.rep.height_um) for p in info.parts] == ["2.65", "1.3", "0.7", "0.34"]
    assert abs(info.gap_um - 0.275) < 1e-6                     # the lamella
    assert info.bbox == (-8.25, -4.4775, 8.25, 4.4775)


def test_svg_labels_each_mirror_pair_once():
    svg = diagram_svg(_info())
    for t in ("700 pA · 12 µm deep", "300 pA · 12 µm deep", "100 pA · 12 µm deep",
              "50 pA · 12 µm deep", "gap 0.275 µm (lamella)"):
        assert svg.count(t) == 1, t
    for t in ("16.5 µm", "15 µm", "13.5 µm", "12 µm", "2.65 µm", "1.3 µm", "0.7 µm", "0.34 µm"):
        assert len(re.findall(r">" + re.escape(t) + r"<", svg)) == 1, t
    assert svg.count('<polygon class="sh') == 8                # every shape drawn


def test_widths_on_top_heights_on_right_current_on_left():
    info = _info()
    svg = diagram_svg(info)
    m = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
    W = float(m.group(1))

    def text_xy(label):
        mm = re.search(r'<text class="(?:dl|cl)" x="([\d.]+)" y="([\d.]+)"[^>]*>' + re.escape(label) + "<", svg)
        return float(mm.group(1)), float(mm.group(2))
    pts = [tuple(map(float, p.split(","))) for p in
           re.search(r'<polygon class="sh [^"]*" points="([^"]+)"', svg).group(1).split()]
    top_px = min(y for _x, y in pts)
    assert text_xy("16.5 µm")[1] < top_px                      # widths above the drawing
    assert text_xy("2.65 µm")[0] > 0.7 * W                     # heights on the right
    assert text_xy("700 pA · 12 µm deep")[0] < 0.3 * W         # current/depth on the left


def test_asymmetric_pattern_without_probe():
    d = Path(tempfile.mkdtemp()); p = d / "one.ely"
    p.write_text("""<?xml version="1.0" encoding="UTF-8"?><ELAYOUT version="2.0" name="one">
<LAYER_LIST><LAYER name="Layer"/></LAYER_LIST><STRUCTURE_LIST><STRUCTURE name="S">
<LAYER_REFERENCE frame_cx="0" frame_cy="0" frame_size="60" ref="Layer">
<RECT dose_factor="8" z_extent="8e-006 m" height="-0.3" width="9.25" angle="0 deg" x="-4.1" y="1.0" imaging="false">
<EXPOSURE version="2.1" file="D:\\FIB Recipes\\Vitriuos_Ice.epm"></EXPOSURE></RECT>
</LAYER_REFERENCE></STRUCTURE></STRUCTURE_LIST></ELAYOUT>""", encoding="utf-8")
    html, warns = pattern_section(p)
    assert "current not set · 8 µm deep" in html and "9.25 µm" in html and "0.3 µm" in html
    assert "lamella" not in html                                # no centre gap here
    assert "above centre" in html


def test_unreadable_pattern_is_a_warning():
    d = Path(tempfile.mkdtemp()); p = d / "bad.ely"
    p.write_text("<nope/>", encoding="utf-8")
    html, warns = pattern_section(p)
    assert html == "" and warns and "could not read" in warns[0]


def test_formatting():
    assert fmt_current(7e-10) == "700 pA" and fmt_current(5e-11) == "50 pA"
    assert fmt_current(1e-9) == "1 nA" and fmt_current(3.5e-9) == "3.5 nA"
    assert fmt_current(None) == "current not set"
    assert fmt_um(16.5) == "16.5" and fmt_um(0.275) == "0.275" and fmt_um(12.0) == "12"


def test_report_shows_the_standard_pattern_after_the_layout():
    from .test_report import _session, _build
    d, pf, db, std = _session()
    h = _build(d, export_map=False).path.read_text(encoding="utf-8")
    assert "<h2 id='pattern'>Patterns</h2>" in h                         # v0.51.1: all patterns
    assert "<h3>Rough · default.ely</h3>" in h
    assert h.index("id='layout'") < h.index("id='pattern'") < h.index("id='runs'")
    assert "gap 0.275 µm (lamella)" in h


def test_current_colours_are_ordered_and_shared():
    from ..pattern_diagram import current_classes, class_for
    cm = current_classes([7e-10, 3e-10, 1e-10, 5e-11, 7e-10])
    assert [class_for(cm, a) for a in (5e-11, 1e-10, 3e-10, 7e-10)] == ["c0", "c1", "c2", "c3"]
    assert class_for(cm, None) == "cn" and class_for(cm, 2e-9) == "cn"
    many = current_classes([i * 1e-11 for i in range(1, 10)])      # 9 currents, 6 colours
    assert set(many.values()) <= {f"c{i}" for i in range(6)} and many[10000] == "c0"


def test_position_overlays_use_the_drawing_colours():
    """Same current -> same colour on the position images and in the pattern drawing."""
    from .test_report import _session, _build
    d, pf, db, std = _session()
    h = _build(d, export_map=False).path.read_text(encoding="utf-8")
    i = h.index("id='pos-position_10'")
    sect = h[i:h.index("</section>", i)]
    ov = re.search(r'<g class="ov ov-pattern">(.*?)</g>', sect).group(1)
    counts = {c: len(re.findall(r'<polygon class="' + c + '"', ov)) for c in ("c0", "c1", "c2", "c3")}
    assert counts == {"c0": 2, "c1": 2, "c2": 2, "c3": 2}, counts      # default.ely, 4 pairs
    draw = h[h.index("id='pattern'"):h.index("id='runs'")]
    assert len(re.findall(r'class="sh c3"', draw)) == 2              # 700 pA pair in drawing
    assert re.search(r'<i class="sw c3"></i>700 pA', sect)           # key under the image
    assert re.search(r'<i class="sw c0"></i>50 pA', sect)


def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t(); print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1; print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1; print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
