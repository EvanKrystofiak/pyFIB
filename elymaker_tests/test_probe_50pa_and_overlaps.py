"""Regression locked against a SmartFIB-CORRECTED lamella (Corrected_lamella.ely).

Two facts SmartFIB's own output establishes:
  1. The 50 pA probe is written INLINE, named "30kV:50pA ref" (trailing " ref") with
     a 45 nm spot -- NOT "30kV:50pA" / 50 nm (elymaker's earlier estimate, which
     SmartFIB did not recognise).
  2. Stage overlaps are per-stage: the rough transitions (700pA->300pA, and the
     outer 1.5nA->700pA) use ~0.35 um; the fine transitions (300->100, 100->50)
     stay at 0.15 um. The lamella gap at each stage is unchanged.
"""
import os, re, tempfile

from elymaker.builder import LayoutBuilder
from elymaker.lamella import LamellaSpec, build_lamella, default_stages
from elymaker.probes import get_probe, probe_names_by_current
from elymaker import writer as W

HERE = os.path.dirname(__file__)
REAL = os.path.join(HERE, "Corrected_lamella.ely")


def test_real_corrected_file_names_50pa_ref_inline():
    txt = open(REAL).read()
    assert 'name="30kV:50pA ref"' in txt
    assert 'diameter="4.5e-008 m"' in txt
    assert 'name="30kV:50pA"' not in txt.replace('name="30kV:50pA ref"', "")


def test_50pa_probe_name_and_diameter():
    p = get_probe("30kV:50pA ref")
    assert p.name == "30kV:50pA ref" and abs(p.diameter_m - 4.5e-8) < 1e-15
    # the plain key still resolves, but yields the REAL SmartFIB name
    assert get_probe("30kV:50pA").name == "30kV:50pA ref"
    # and the alias is not listed twice in the dropdown ordering
    names = probe_names_by_current()
    assert names.count("30kV:50pA ref") == 1 and "30kV:50pA" not in names


def test_authoring_50pa_writes_the_ref_name_inline():
    b = LayoutBuilder("t", recipe=r"D:\FIB Recipes\Vitriuos_Ice.epm")
    b.rect(x=0, y=0, width=12, height=0.2, depth_um=15, probe="30kV:50pA")
    p = tempfile.mktemp(suffix=".ely"); W.write_ely(b.build(), p)
    txt = open(p).read()
    assert '<PROBE name="30kV:50pA ref"' in txt and 'diameter="4.5e-08 m"' in txt \
        or 'diameter="4.5e-008 m"' in txt


def _top_boxes(rects):
    seen, out = [], []
    for r in rects:
        y, h = r.y, r.height
        lo, hi = (y + h, y) if h < 0 else (y, y + h)
        n = r.exposure.probe.name if (r.exposure and r.exposure.probe) else "?"
        if hi > 0 and r.width > 2 and n not in seen:
            seen.append(n); out.append((n, lo, hi))
    return out


def test_default_lamella_overlaps_match_corrected_pattern():
    tops = _top_boxes(build_lamella(LamellaSpec()))
    ov = {f: fhi - clo for (c, clo, chi), (f, flo, fhi) in zip(tops, tops[1:])}
    assert abs(ov["30kV:300pA"] - 0.35) < 1e-6      # rough: 700pA -> 300pA
    assert abs(ov["30kV:100pA"] - 0.15) < 1e-6      # fine:  300pA -> 100pA
    assert abs(ov["30kV:50pA ref"] - 0.15) < 1e-6   # fine:  100pA -> 50pA
    assert abs(ov["30kV:20pA"] - 0.15) < 1e-6


def test_per_stage_overlap_overrides_global():
    spec = LamellaSpec(overlap_um=0.15)
    spec.stages = default_stages()
    spec.stages[2].overlap_um = 0.5                # Fine 1 into Rough 2
    tops = _top_boxes(build_lamella(spec))
    ov = {f: fhi - clo for (c, clo, chi), (f, flo, fhi) in zip(tops, tops[1:])}
    assert abs(ov["30kV:100pA"] - 0.5) < 1e-6


def test_stage_without_overlap_falls_back_to_global():
    spec = LamellaSpec(overlap_um=0.22)
    spec.stages = default_stages()
    for st in spec.stages:
        st.overlap_um = None
    tops = _top_boxes(build_lamella(spec))
    ov = {f: fhi - clo for (c, clo, chi), (f, flo, fhi) in zip(tops, tops[1:])}
    assert all(abs(v - 0.22) < 1e-6 for v in ov.values())


# ---- lamella dialog: optional fine/polish stages + overlap preserved ---- #
def test_dialog_stage_checkboxes_and_overlap_preserved():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from qtpy.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from elymaker.gui.lamella_dialog import LamellaDialog
    dlg = LamellaDialog(recipe=r"D:\FIB Recipes\Vitriuos_Ice.epm")
    rows = dlg._rows
    names = [r[0] for r in rows]
    # rough stages locked on; fine/polish switchable
    assert not rows[names.index("Rough 1")][3].isEnabled()
    assert rows[names.index("Fine 2")][3].isEnabled()
    # per-stage overlap survives the dialog (it used to be dropped -> global 0.15)
    assert [st.overlap_um for st in dlg.spec().stages] == [0.35, 0.35, 0.15, 0.15, 0.15]
    # unticking Fine 2 + Polish removes them from the spec
    for n, _, _, chk, _, _r in rows:
        if n in ("Fine 2", "Polish"):
            chk.setChecked(False)
    assert [st.name for st in dlg.spec().stages] == ["Rough 1", "Rough 2", "Fine 1"]


def test_default_colour_mode_is_probe_current():
    from elymaker.gui import items
    assert items.color_mode() == items.COLOR_BY_CURRENT


# ---- probe colours: categorical Okabe-Ito (option B) ---- #
def test_probe_colours_distinct_and_neighbours_far_apart():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from qtpy.QtWidgets import QApplication
    from qtpy.QtGui import QColor
    QApplication.instance() or QApplication([])
    from elymaker.gui.items import current_color, probe_palette, _NO_CURRENT
    lam = [1.5e-9, 7e-10, 3e-10, 1e-10, 5e-11, 2e-11]           # the lamella probes
    cols = [current_color(c, 255).getRgb()[:3] for c in lam]
    assert len(set(cols)) == len(cols)                           # pairwise distinct
    assert cols == [current_color(c, 255).getRgb()[:3] for c in lam]   # deterministic
    for a, b in zip(cols, cols[1:]):                              # neighbours far apart
        d = abs(QColor(*a).hue() - QColor(*b).hue()); d = min(d, 360 - d)
        assert d >= 60, (a, b, d)
    # all library probes get a unique colour; unknown current -> nearest; none -> grey
    pal = [c.getRgb()[:3] for _, c in probe_palette()]
    assert len(set(pal)) == len(pal)
    assert current_color(6.9e-10, 255).getRgb()[:3] == current_color(7e-10, 255).getRgb()[:3]
    assert current_color(None, 255).getRgb()[:3] == _NO_CURRENT.getRgb()[:3]


def test_probe_mode_uses_more_opaque_fill_and_heavier_edge():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from qtpy.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from elymaker.gui import items
    from elymaker.probes import get_probe
    from elymaker.model import Exposure

    class _Item:                      # minimal shape stand-in
        depth_um = 15.0
        ignored = False
        exposure = Exposure(file="x.epm", probe=get_probe("30kV:700pA"))
    items.set_color_mode(items.COLOR_BY_CURRENT)
    assert items._brush(_Item()).color().alpha() == items._CURRENT_FILL_ALPHA == 170
    assert items._pen(_Item()).widthF() == 2.0
    items.set_color_mode(items.COLOR_BY_DEPTH)
    assert items._brush(_Item()).color().alpha() == 110
    items.set_color_mode(items.COLOR_BY_CURRENT)   # restore the default


# ---- lamella: rounded INNER corners (per stage) ---- #
def _rounded_spec(names=("Rough 1",), **kw):
    from elymaker.lamella import LamellaSpec, default_stages
    st = default_stages()
    for s in st:
        s.round_inner = s.name in names
    return LamellaSpec(stages=st, recipe=r"D:\FIB Recipes\Vitriuos_Ice.epm", **kw)


def test_default_lamella_is_unchanged_all_rects():
    from elymaker.lamella import LamellaSpec, build_lamella
    from elymaker.model import Rect
    out = build_lamella(LamellaSpec())
    assert all(isinstance(s, Rect) for s in out) and len(out) == 10


def test_rounded_stage_is_polygon_marked_on_straight_inner_edge():
    from elymaker.lamella import build_lamella
    from elymaker.model import Polygon, Rect
    spec = _rounded_spec(("Rough 1", "Fine 1"))
    out = build_lamella(spec)
    for i, sh in enumerate(out):
        name = spec.stages[i // 2].name
        if name not in ("Rough 1", "Fine 1"):
            assert isinstance(sh, Rect)
            continue
        assert isinstance(sh, Polygon)
        n, k = len(sh.points), sh.edge_sel_marked_edge
        (x0, y0), (x1, y1) = sh.points[k - 1], sh.points[k % n]
        ys = [p[1] for p in sh.points]
        inner_y = min(ys) if i % 2 == 0 else max(ys)          # top box: bottom edge
        assert abs(y0 - inner_y) < 1e-9 and abs(y1 - inner_y) < 1e-9
        # the marked edge is the full straight part: width minus the two fillets
        xs = [p[0] for p in sh.points]
        assert abs(abs(x1 - x0) - ((max(xs) - min(xs)) - 2 * 1.0 if name == "Rough 1"
                                   else (max(xs) - min(xs)) - 2 * 0.35)) < 1e-6


def test_only_inner_corners_are_rounded():
    from elymaker.lamella import build_lamella
    top, bot = build_lamella(_rounded_spec())[:2]
    ys_t = [p[1] for p in top.points]; ys_b = [p[1] for p in bot.points]
    # outer edge stays square: exactly two vertices at the outer y, at the box ends
    for pts, outer in ((top.points, max(ys_t)), (bot.points, min(ys_b))):
        at_outer = [p for p in pts if abs(p[1] - outer) < 1e-12]
        assert len(at_outer) == 2


def test_radius_auto_and_capped_at_half_width_step():
    from elymaker.lamella import corner_radius_um
    spec = _rounded_spec()                         # frac 0.5, h_step 2.0
    assert corner_radius_um(spec, 2.65, 20.0) == 1.0     # 1.325 capped at h_step/2
    assert abs(corner_radius_um(spec, 0.70, 16.0) - 0.35) < 1e-12
    spec.corner_radius_frac = 0.0
    assert corner_radius_um(spec, 2.65, 20.0) == 0.0


def test_rounded_lamella_round_trips_through_writer_and_reader(tmp_path):
    from elymaker.lamella import build_lamella
    from elymaker.builder import LayoutBuilder
    from elymaker.writer import write_ely
    from elymaker.reader import read_ely
    spec = _rounded_spec(("Rough 1", "Rough 2"))
    shapes = build_lamella(spec)
    b = LayoutBuilder("lam", recipe=spec.recipe)
    lay = b.build(); lay.structures[0].layer_refs[0].shapes = shapes
    p = tmp_path / "lam.ely"; write_ely(lay, str(p))
    back = read_ely(str(p)).structures[0].layer_refs[0].shapes
    assert [type(s).__name__ for s in back] == [type(s).__name__ for s in shapes]
    for a, s in zip(back, shapes):
        assert a.edge_sel_marked_edge == s.edge_sel_marked_edge
        if hasattr(s, "points"):
            assert len(a.points) == len(s.points)


def test_dialog_round_checkbox_per_stage():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from qtpy.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from elymaker.gui.lamella_dialog import LamellaDialog
    dlg = LamellaDialog(recipe=r"D:\FIB Recipes\Vitriuos_Ice.epm")
    assert not any(r[5].isChecked() for r in dlg._rows)           # off by default
    for r in dlg._rows:
        if r[0] == "Rough 2":
            r[5].setChecked(True)
    dlg.corner_frac.setValue(0.4)
    spec = dlg.spec()
    assert [s.name for s in spec.stages if s.round_inner] == ["Rough 2"]
    assert abs(spec.corner_radius_frac - 0.4) < 1e-9
