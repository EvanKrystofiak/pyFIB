"""Tests for v0.9.0: headless job runner + integration surface."""

import json
import os
import subprocess
import sys

import pytest


# --------------------------------------------------------------------------- #
# run_job: three sources, all headless (no Qt)
# --------------------------------------------------------------------------- #
def test_run_job_from_lamella_spec(tmp_path):
    from elymaker.handoff import HandoffJob, run_job
    from elymaker.lamella import LamellaSpec
    from elymaker.reader import read_ely
    out = str(tmp_path / "lam.ely")
    job = HandoffJob(image="x.bmp", pixel_size_m=5e-8, out=out)
    n = run_job(job, LamellaSpec(trenches=True))
    assert n == 12
    assert read_ely(out).shape_count() == 12


def test_run_job_from_existing_ely(tmp_path):
    from elymaker.handoff import HandoffJob, run_job
    from elymaker.builder import LayoutBuilder
    from elymaker.reader import read_ely
    prev = str(tmp_path / "prev.ely")
    b = LayoutBuilder("prev", fov_um=50)
    b.rect(x=-5, y=5, width=10, height=-4, depth_um=2.0, probe="30kV:100pA")
    b.save(prev)
    out = str(tmp_path / "copy.ely")
    job = HandoffJob(image="x.bmp", pixel_size_m=5e-8, out=out, load=prev)
    assert run_job(job) == 1
    assert read_ely(out).shape_count() == 1


def test_run_job_empty(tmp_path):
    from elymaker.handoff import HandoffJob, run_job
    from elymaker.reader import read_ely
    out = str(tmp_path / "empty.ely")
    assert run_job(HandoffJob(image="x.bmp", pixel_size_m=5e-8, out=out)) == 0
    assert read_ely(out).shape_count() == 0


def test_run_job_creates_output_dir(tmp_path):
    from elymaker.handoff import HandoffJob, run_job
    out = str(tmp_path / "nested" / "deep" / "p.ely")
    run_job(HandoffJob(image="x.bmp", pixel_size_m=5e-8, out=out))
    assert os.path.exists(out)


def test_run_job_is_qt_free(tmp_path):
    """The whole headless path must not import Qt."""
    code = (
        "import sys; from elymaker.handoff import HandoffJob, run_job;"
        "from elymaker.lamella import LamellaSpec;"
        "run_job(HandoffJob(image='x',pixel_size_m=5e-8,out=r'%s'), LamellaSpec());"
        "print('QT' if any(m.startswith(('PyQt5','qtpy','PySide2','PySide6','PyQt6')) "
        "for m in sys.modules) else 'NOQT')"
    ) % str(tmp_path / "q.ely").replace("\\", "\\\\")
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert res.stdout.strip().endswith("NOQT"), res.stdout + res.stderr


# --------------------------------------------------------------------------- #
# CLI: python -m elymaker.handoff run job.json
# --------------------------------------------------------------------------- #
def test_handoff_cli_run(tmp_path):
    from elymaker.handoff import HandoffJob, save_job
    from elymaker.lamella import LamellaSpec, build_lamella
    from elymaker.model import Layout, Layer, LayerReference, Structure, VersionInfo
    from elymaker.writer import write_ely
    # a prev pattern to load
    prev = tmp_path / "prev.ely"
    lay = Layout(name="p", version=VersionInfo(created="", modified=""), layers=[Layer()])
    lref = LayerReference(ref="Layer", frame_size=50.0)
    lref.shapes = build_lamella(LamellaSpec())
    lay.structures = [Structure(name="S", version=VersionInfo(created="", modified=""), layer_refs=[lref])]
    write_ely(lay, str(prev))
    out = tmp_path / "out.ely"
    save_job(HandoffJob(image="x.bmp", pixel_size_m=5e-8, out=str(out), load=str(prev)),
             str(tmp_path / "job.json"))
    res = subprocess.run(
        [sys.executable, "-m", "elymaker.handoff", "run", str(tmp_path / "job.json")],
        capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert out.exists()
    from elymaker.reader import read_ely
    assert read_ely(str(out)).shape_count() == 10


# --------------------------------------------------------------------------- #
# integration surface is importable Qt-free
# --------------------------------------------------------------------------- #
def test_core_public_api_importable_without_qt():
    code = (
        "import sys, elymaker;"
        "from elymaker.handoff import HandoffJob, save_job, load_job, run_job;"
        "from elymaker.lamella import LamellaSpec, build_lamella, default_stages;"
        "from elymaker.builder import LayoutBuilder;"
        "print('QT' if any(m.startswith(('PyQt5','qtpy','PySide2','PySide6','PyQt6')) "
        "for m in sys.modules) else 'NOQT')"
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert res.stdout.strip() == "NOQT", res.stdout + res.stderr


# --------------------------------------------------------------------------- #
# ready-marker (file + marker handshake)
# --------------------------------------------------------------------------- #
def test_ready_marker_fields_and_staleness(tmp_path):
    from elymaker.handoff import (HandoffJob, run_job, read_ready_marker,
                                  marker_path_for, marker_is_current)
    from elymaker.lamella import LamellaSpec
    out = str(tmp_path / "pos.ely")
    job = HandoffJob(image="x.bmp", pixel_size_m=5e-8, out=out, site="grid1_pos3")
    run_job(job, LamellaSpec(), write_marker=True)
    m = read_ready_marker(marker_path_for(out))
    assert m["schema"] == "elymaker.ready.v1"
    assert m["ely"] == os.path.abspath(out)
    assert m["content_hash"].startswith("sha256:")
    assert m["target_site"] == "grid1_pos3"
    assert m["state"] == "ready"                 # ready != mill
    assert m["shape_count"] == 10
    assert marker_is_current(m) is True
    # editing the .ely makes the marker stale
    with open(out, "a") as fh:
        fh.write("<!-- x -->")
    assert marker_is_current(m) is False


def test_ready_marker_rejects_foreign_schema(tmp_path):
    from elymaker.handoff import read_ready_marker
    p = tmp_path / "m.json"
    p.write_text('{"schema": "other.v1"}')
    with pytest.raises(ValueError):
        read_ready_marker(str(p))


def test_ready_marker_atomic_no_tmp_left(tmp_path):
    from elymaker.handoff import HandoffJob, run_job, marker_path_for
    out = str(tmp_path / "p.ely")
    run_job(HandoffJob(image="x", pixel_size_m=5e-8, out=out, site="s"),
            write_marker=True)
    assert os.path.exists(marker_path_for(out))
    assert not os.path.exists(marker_path_for(out) + ".tmp")   # temp cleaned


def test_job_site_field_roundtrips(tmp_path):
    from elymaker.handoff import HandoffJob, save_job, load_job
    save_job(HandoffJob(image="i.png", pixel_size_m=1e-8, out="o.ely",
                        site="lamella_7"), str(tmp_path / "job.json"))
    assert load_job(str(tmp_path / "job.json")).site == "lamella_7"
