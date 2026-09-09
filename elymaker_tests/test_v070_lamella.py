"""Tests for v0.7.0: lamella pattern generator."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# --------------------------------------------------------------------------- #
# core geometry (pure)
# --------------------------------------------------------------------------- #
def test_build_lamella_count_and_symmetry():
    from elymaker.lamella import LamellaSpec, build_lamella
    rects = build_lamella(LamellaSpec(final_thickness_um=1.0))
    assert len(rects) == 10                         # 5 stages x top/bottom
    for i in range(0, 10, 2):
        tc = rects[i].y + rects[i].height / 2
        bc = rects[i + 1].y + rects[i + 1].height / 2
        assert tc == pytest.approx(-bc)             # mirror across centerline


def test_lamella_widths_decrease_inward():
    from elymaker.lamella import LamellaSpec, build_lamella
    rects = build_lamella(LamellaSpec(final_width_um=12, h_step_um=2))
    widths = [abs(rects[i].width) for i in range(0, 10, 2)]   # R1..Polish
    assert widths == [20, 18, 16, 14, 12]           # outermost widest
    assert widths == sorted(widths, reverse=True)


def test_lamella_final_thickness_and_overlap():
    from elymaker.lamella import LamellaSpec, build_lamella
    spec = LamellaSpec(final_thickness_um=0.3, overlap_um=0.15)
    r = build_lamella(spec)
    # polish inner edges leave exactly the final thickness
    polish_top_inner = r[8].y + r[8].height       # bottom edge of top polish box
    polish_bot_inner = r[9].y                      # top edge of bottom polish box
    assert (polish_top_inner - polish_bot_inner) == pytest.approx(0.3)
    # adjacent top stages overlap by `overlap` (Polish and Fine2 on the top side)
    po_top = (r[8].y + r[8].height, r[8].y)        # (inner, outer)
    f2_top = (r[6].y + r[6].height, r[6].y)
    ov = min(po_top[1], f2_top[1]) - max(po_top[0], f2_top[0])
    assert ov == pytest.approx(0.15)


def test_lamella_heights_shrink_toward_lamella():
    from elymaker.lamella import LamellaSpec, build_lamella
    r = build_lamella(LamellaSpec())
    heights = [abs(r[i].height) for i in range(0, 10, 2)]   # R1..Polish
    assert heights == pytest.approx([2.65, 1.30, 0.70, 0.27, 0.20])
    assert heights == sorted(heights, reverse=True)         # decreasing (fast fine)


def test_lamella_trenches_optional_and_geometry():
    from elymaker.lamella import LamellaSpec, build_lamella
    from elymaker.model import Rect
    off = build_lamella(LamellaSpec(trenches=False))
    on = build_lamella(LamellaSpec(trenches=True, trench_width_um=1.0,
                                   trench_offset_um=2.2))
    assert len(off) == 10 and len(on) == 12                 # two extra
    # the two trenches are the tall narrow boxes
    trenches = [b for b in on if abs(b.height) > abs(b.width) * 3]
    assert len(trenches) == 2
    # offset 2.2 outside the widest (Rough1, width 20 -> half 10) box
    r1_half = 10.0
    inner_edges = sorted(min(t.x, t.x + t.width) if (t.x + t.width) > 0
                         else max(t.x, t.x + t.width) for t in trenches)
    # left trench inner (right) edge and right trench inner (left) edge
    left_inner = max(t.x + t.width for t in trenches if t.x < 0)
    right_inner = min(t.x for t in trenches if t.x > 0)
    assert left_inner == pytest.approx(-(r1_half + 2.2))
    assert right_inner == pytest.approx(r1_half + 2.2)


def test_lamella_marked_edges_face_inward():
    from elymaker.lamella import build_lamella, LamellaSpec
    r = build_lamella(LamellaSpec())
    for i in range(0, 10, 2):
        assert r[i].edge_sel_marked_edge == 3       # top box marks bottom (inner)
        assert r[i + 1].edge_sel_marked_edge == 1   # bottom box marks top (inner)


def test_lamella_currents_and_order():
    from elymaker.lamella import build_lamella, LamellaSpec
    r = build_lamella(LamellaSpec())
    currents = [r[i].exposure.probe.current_A for i in range(0, 10, 2)]
    # outer -> inner: 700, 300, 100, 50, 20 pA (decreasing)
    assert currents == [pytest.approx(7e-10), pytest.approx(3e-10),
                        pytest.approx(1e-10), pytest.approx(5e-11),
                        pytest.approx(2e-11)]


def test_lamella_centered_on_origin_default():
    from elymaker.lamella import build_lamella, LamellaSpec
    r = build_lamella(LamellaSpec())
    # each box is centered in x on 0
    for box in r:
        assert (box.x + box.width / 2) == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# dialog + GUI generate / regenerate
# --------------------------------------------------------------------------- #
@pytest.fixture()
def win(qapp, qt_cleanup):
    from elymaker.gui.main_window import MainWindow
    w = MainWindow(); w.show(); w.view.resize(300, 300)
    qt_cleanup.append(w)
    w.new_layout()
    return w


def test_dialog_spec_roundtrip(qapp, qt_cleanup):
    from elymaker.gui.lamella_dialog import LamellaDialog
    dlg = LamellaDialog(depth_um=2.0, recipe="D:/r.epm")
    qt_cleanup.append(dlg)
    dlg.width.setValue(10); dlg.thickness.setValue(0.3); dlg.hstep.setValue(1.5)
    spec = dlg.spec()
    assert spec.final_width_um == pytest.approx(10)
    assert spec.final_thickness_um == pytest.approx(0.3)
    assert spec.depth_um == pytest.approx(2.0)
    assert spec.recipe == "D:/r.epm"
    assert len(spec.stages) == 5


def test_generate_lamella_adds_ten_boxes(win):
    from elymaker.lamella import LamellaSpec
    win.new_lamella(spec=None) if False else None
    # call the generator directly with a spec (bypass modal exec)
    from elymaker.gui.items import item_from_shape
    from elymaker.lamella import build_lamella
    for m in build_lamella(LamellaSpec()):
        it = item_from_shape(m); win.scene.add_shape_item(it); win._last_lamella.append(it)
    assert win.scene.to_layout().shape_count() == 10


def test_regenerate_replaces_previous(win, monkeypatch):
    # stub the dialog so new_lamella runs without a modal window
    from elymaker.lamella import LamellaSpec
    from elymaker.gui import lamella_dialog

    class FakeDlg:
        def __init__(self, *a, **k): pass
        Accepted = 1
        def exec_(self): return 1
        def spec(self): return LamellaSpec(final_thickness_um=1.0)
        @property
        def replace_previous(self): return True

    monkeypatch.setattr("elymaker.gui.main_window.LamellaDialog", FakeDlg, raising=False)
    # patch the local import inside new_lamella
    import elymaker.gui.main_window as mw
    monkeypatch.setattr(lamella_dialog, "LamellaDialog", FakeDlg, raising=True)

    win.new_lamella()
    assert win.scene.to_layout().shape_count() == 10
    win.new_lamella()                       # regenerate, replace on
    assert win.scene.to_layout().shape_count() == 10   # still 10, not 20


def test_generated_lamella_saves_legal_ely(win, tmp_path):
    from elymaker.gui.items import item_from_shape
    from elymaker.lamella import build_lamella, LamellaSpec
    from elymaker.reader import read_ely
    from elymaker.validate import validate
    for m in build_lamella(LamellaSpec(final_thickness_um=0.3)):
        win.scene.add_shape_item(item_from_shape(m))
    out = str(tmp_path / "lam.ely")
    win._path = out
    win._write(out)
    lay = read_ely(out)
    assert lay.shape_count() == 10
    assert not [i for i in validate(lay) if i.startswith("ERROR")]
