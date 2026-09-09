"""Tests for v0.5.0: pyFIB -> elymaker handoff (job contract + preload)."""

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# --------------------------------------------------------------------------- #
# job contract (pure, no Qt)
# --------------------------------------------------------------------------- #
def test_job_roundtrip_and_units(tmp_path):
    from elymaker.handoff import HandoffJob, save_job, load_job
    job = HandoffJob(image="fib.png", pixel_size_m=5.58e-8, out="pos1.ely",
                     fov_width_m=5.7e-5, image_width_px=1024, image_height_px=768)
    p = tmp_path / "job.json"
    save_job(job, str(p))
    loaded = load_job(str(p))
    assert loaded.pixel_size_um() == pytest.approx(0.0558)
    assert loaded.fov_width_um() == pytest.approx(57.0)
    # relative paths resolve against the job dir
    assert loaded.image == str(tmp_path / "fib.png")
    assert loaded.out == str(tmp_path / "pos1.ely")


def test_job_rejects_foreign_schema(tmp_path):
    from elymaker.handoff import load_job
    p = tmp_path / "j.json"
    p.write_text(json.dumps({"schema": "something.else", "image": "a.png",
                             "pixel_size_m": 1e-8, "out": "o.ely"}))
    with pytest.raises(ValueError):
        load_job(str(p))


def test_job_requires_core_fields(tmp_path):
    from elymaker.handoff import load_job
    p = tmp_path / "j.json"
    p.write_text(json.dumps({"image": "a.png"}))   # missing pixel_size_m/out
    with pytest.raises(ValueError):
        load_job(str(p))


# --------------------------------------------------------------------------- #
# CLI arg resolution (pure)
# --------------------------------------------------------------------------- #
def test_resolve_handoff_from_flags():
    from elymaker.gui.app import _build_parser, resolve_handoff
    args = _build_parser().parse_args(
        ["--image", "f.png", "--pixel-size", "5e-8", "--out", "o.ely",
         "--fov-width", "5e-5"])
    kw = resolve_handoff(args)
    assert kw["image_path"] == "f.png"
    assert kw["pixel_size_um"] == pytest.approx(0.05)
    assert kw["fov_width_um"] == pytest.approx(50.0)
    assert kw["out_path"] == "o.ely"


def test_resolve_handoff_none_when_no_args():
    from elymaker.gui.app import _build_parser, resolve_handoff
    assert resolve_handoff(_build_parser().parse_args([])) is None


def test_resolve_handoff_partial_flags_error():
    from elymaker.gui.app import _build_parser, resolve_handoff
    args = _build_parser().parse_args(["--image", "f.png"])   # missing pixel/out
    with pytest.raises(SystemExit):
        resolve_handoff(args)


def test_resolve_handoff_from_job(tmp_path):
    from elymaker.handoff import HandoffJob, save_job
    from elymaker.gui.app import _build_parser, resolve_handoff
    save_job(HandoffJob(image="im.png", pixel_size_m=1e-8, out="p.ely"),
             str(tmp_path / "job.json"))
    args = _build_parser().parse_args(["--job", str(tmp_path / "job.json")])
    kw = resolve_handoff(args)
    assert kw["pixel_size_um"] == pytest.approx(0.01)
    assert kw["image_path"] == str(tmp_path / "im.png")


# --------------------------------------------------------------------------- #
# GUI preload (offscreen)
# --------------------------------------------------------------------------- #
@pytest.fixture()
def win(qapp, qt_cleanup):
    from elymaker.gui.main_window import MainWindow
    w = MainWindow(); w.show(); w.view.resize(300, 300)
    qt_cleanup.append(w)
    return w


def _make_png(path, w=200, h=160):
    from qtpy.QtGui import QPixmap
    pix = QPixmap(w, h); pix.fill()
    pix.save(str(path), "PNG")
    return str(path)


def test_open_handoff_sets_centered_image_and_out(win, tmp_path):
    img = _make_png(tmp_path / "fib.png", 200, 160)
    out = str(tmp_path / "pos1.ely")
    win.open_handoff(img, pixel_size_um=0.05, out_path=out, fov_width_um=10.0)
    cal = win.scene.calibration
    assert cal is not None
    # image centred on the FOV centre = layout origin (milling convention)
    assert cal.center_x_um == pytest.approx(0.0)
    assert cal.center_y_um == pytest.approx(0.0)
    assert cal.pixel_size_um == pytest.approx(0.05)
    assert cal.image_w_px == 200 and cal.image_h_px == 160
    # Save is wired to the job's out path
    assert win._path == out
    assert win.scene.frame_size_um == pytest.approx(10.0)


def test_open_handoff_preloads_existing_ely(win, tmp_path):
    from elymaker.builder import LayoutBuilder
    # make an existing pattern
    ely = str(tmp_path / "prev.ely")
    b = LayoutBuilder("prev", fov_um=50)
    b.rect(x=-5, y=5, width=10, height=-4, depth_um=2.0, probe="30kV:100pA")
    b.save(ely)
    img = _make_png(tmp_path / "fib.png")
    out = str(tmp_path / "pos1.ely")
    win.open_handoff(img, pixel_size_um=0.05, out_path=out, load_path=ely)
    assert win.scene.to_layout().shape_count() == 1     # existing pattern loaded
    assert win.scene.calibration is not None            # over the image


def test_handoff_save_writes_out_path(win, tmp_path):
    from elymaker.reader import read_ely
    from elymaker.gui.items import RectItem
    from elymaker.gui.coords import layout_to_scene
    img = _make_png(tmp_path / "fib.png")
    out = str(tmp_path / "result.ely")
    win.open_handoff(img, pixel_size_um=0.05, out_path=out)
    it = RectItem(10, -4); sx, sy = layout_to_scene(0, 0); it.setPos(sx, sy)
    win.view.defaults.apply(it); win.scene.add_shape_item(it); win._on_shape_created(it)
    win.save_file()                       # should write to out (no dialog)
    assert os.path.exists(out)
    assert read_ely(out).shape_count() == 1
