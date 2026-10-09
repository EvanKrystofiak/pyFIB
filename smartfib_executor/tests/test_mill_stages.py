"""Milling stages (v0.51.0): Rough Mill -> Prepolish -> Polish, batch and manual.

Evan 2026-10-07: Prepolish / Polish get their own patterns; Prepolish refines to the Rough
Mill after image, Polish to the Prepolish one; after images named
<pos>_1-rough_<time>.bmp / _2-prepolish_ / _3-polish_; a position without the previous
stage's after image is listed up front and skipped (OK needed); manual Go to / Align /
Push pattern (no start) / Record after image in every tab. All offline: fake microscope.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from .. import mill_stages as MS
from ..positions import PositionDatabase, PositionRecord, StageAt


def _rec(name, *, t=2.0, ely=None):
    return PositionRecord(name=name, captured_unix_s=1.0, fib_magnification_x=2000.0,
                          fib_objective_potential_v=16400.0,
                          stage_at=StageAt(x_m=1e-3, y_m=2e-3, z_m=3e-3, r_deg=12.0, m_m=0.0,
                                           t_deg=t),
                          image_bmp=f"images/{name}.bmp", ely_pattern=ely,
                          image_width_px=1024, image_height_px=768)


def _session(names=("position_1", "position_2", "position_12")):
    d = Path(tempfile.mkdtemp())
    (d / "images").mkdir()
    (d / "patterns").mkdir()
    db = PositionDatabase()
    for n in names:
        db.positions[n] = _rec(n, ely="patterns/authored.ely")
        (d / "images" / f"{n}.bmp").write_bytes(b"BM")
    (d / "patterns" / "authored.ely").write_text("<authored/>")
    pf = d / "positions.json"
    db.save(pf)
    (d / "rough.ely").write_text("<rough/>")
    (d / "prepolish.ely").write_text("<prepolish/>")
    (d / "polish.ely").write_text("<polish/>")
    return d, pf


class _Sem:
    """Fake microscope: idle, tilt 2.0, stage at position 1; records set/execute in order."""
    def __init__(self, *, milling=False):
        self.calls = []
        self.milling = milling
        self.beam = {"AP_FIB_BEAM_SHIFT_X": 3e-6, "AP_FIB_BEAM_SHIFT_Y": -1e-6}

    def get_float(self, m):
        if m == "AP_STAGE_AT_T":
            return 2.0
        if m == "AP_STAGE_AT_X":
            return 1e-3
        if m == "AP_STAGE_AT_Y":
            return 2e-3
        if m == "DP_FIB_MODE":
            return 2.0 if self.milling else 0.0
        if m in self.beam:
            return self.beam[m]
        return 0.0

    def get_str(self, m):
        return "Imaging"

    def set_value(self, m, v):
        self.calls.append(("set", m, v))
        if m in self.beam:
            self.beam[m] = float(v)

    def execute(self, c, *a):
        self.calls.append(("execute", c))

    def move_stage(self, *a):
        self.calls.append(("move",) + tuple(a))

    def wait_stage_arrival(self, **kw):
        self.calls.append(("arrive",))


# --------------------------------------------------------------------------- naming
def test_names_history_and_references():
    d, pf = _session()
    t = 1791378900.0                                   # fixed time -> fixed stamp
    p = MS.after_image_path(d / "images", "position_99", "prepolish", t)
    assert MS.AFTER_RE.match(p.name).group("stage") == "prepolish"
    assert p.name.startswith("position_99_2-prepolish_") and p.suffix == ".bmp"
    assert MS.after_image_path(d / "images", "position_12", "rough", t).name \
        .startswith("position_12_1-rough_")
    assert MS.after_image_path(d / "images", "position_12", "polish", t).name \
        .startswith("position_12_3-polish_")
    p.write_bytes(b"BM")                               # same second again -> never overwritten
    assert MS.after_image_path(d / "images", "position_99", "prepolish", t).name.endswith("_2.bmp")
    try:
        MS.after_image_path(d, "x", "fine", t)
        raise AssertionError("unknown stage accepted")
    except ValueError:
        pass

    db = PositionDatabase.load(pf)
    r1, r12 = db.get("position_1"), db.get("position_12")
    # rough: the capture image
    ref, desc, why = MS.reference_for_stage(r1, "rough", d)
    assert ref == d / "images" / "position_1.bmp" and why is None
    # prepolish: nothing yet -> missing
    assert MS.reference_for_stage(r1, "prepolish", d)[2] == "no Rough Mill after image"
    # newest file wins; a pre-v0.51.0 rough after image counts; position_1 != position_12
    (d / "images" / "position_1_after_20261006_101010.bmp").write_bytes(b"BM")
    (d / "images" / "position_12_1-rough_20261007_090000.bmp").write_bytes(b"BM")
    assert MS.reference_for_stage(r1, "prepolish", d)[0].name == "position_1_after_20261006_101010.bmp"
    (d / "images" / "position_1_1-rough_20261007_101010.bmp").write_bytes(b"BM")
    assert MS.reference_for_stage(r1, "prepolish", d)[0].name == "position_1_1-rough_20261007_101010.bmp"
    # positions.json record wins over a newer stray file
    r1b = MS.with_after_image(r1, "rough", d / "images" / "position_1_after_20261006_101010.bmp", d)
    assert r1b.stage_after_images == {"rough": "images/position_1_after_20261006_101010.bmp"}
    assert MS.reference_for_stage(r1b, "prepolish", d)[0].name == "position_1_after_20261006_101010.bmp"
    # polish needs a PREPOLISH image (a rough one is not enough)
    assert MS.reference_for_stage(r12, "polish", d)[2] == "no Prepolish after image"
    plan = MS.plan_stage(db, ["position_1", "position_2", "position_12"], "prepolish", d)
    assert plan.allowed == ["position_1", "position_12"] and list(plan.missing) == ["position_2"]
    assert len(MS.stage_image_history(d / "images", "position_1", "rough")) == 2
    assert MS.stamp_of("position_1_2-prepolish_20261007_141500.bmp") == "2026-10-07 14:15:00"


def test_positions_json_round_trip_record_and_recapture_keep_the_images():
    d, pf = _session()
    img = d / "images" / "position_2_1-rough_20261007_101010.bmp"
    img.write_bytes(b"BM")
    db = PositionDatabase.load(pf)
    rel = MS.record_after_image(pf, "position_2", "rough", img, db=db)
    assert rel == "images/position_2_1-rough_20261007_101010.bmp"
    assert db.get("position_2").stage_after_images == {"rough": rel}       # in memory too
    again = PositionDatabase.load(pf)
    assert again.get("position_2").stage_after_images == {"rough": rel}
    assert again.get("position_2").ely_pattern == "patterns/authored.ely"   # rest untouched
    assert again.get("position_1").stage_after_images is None
    hash(again.get("position_2"))                                         # still hashable
    new = _rec("position_2")
    kept = MS.carry_over(again.get("position_2"), new)
    assert kept.stage_after_images == {"rough": rel} and MS.carry_over(None, new) is new
    raw = json.loads(pf.read_text())
    raw["positions"]["position_1"].pop("stage_after_images")               # older file
    pf.write_text(json.dumps(raw))
    assert PositionDatabase.load(pf).get("position_1").stage_after_images is None


# --------------------------------------------------------------------------- batch
class _Log:
    def __init__(self): self.calls = []
    def __getattr__(self, name):
        return lambda *a, **k: self.calls.append((name, a))


def _batch(d, pf, names, stage, *, ely, ref_ok=None, after=True,
           load_method="smartfib_drop"):
    import smartfib_executor.batch as batch
    import smartfib_executor.after_image as AI
    db = PositionDatabase.load(pf)
    sem, log = _Sem(), _Log()
    recalled, jobs, refs, cfgs = [], [], {}, {}

    class _Runner:
        def __init__(self, sem): pass
        def run(self, job): jobs.append(job)

    def fake_refine(sem, rec, cfg, *, ref_base_dir=None, dry_run=False, logger=None, **kw):
        refs[rec.name] = kw.get("reference_path")
        cfgs[rec.name] = cfg

    def fake_grab(sem, rec, images_dir, *, milling_active, log=print, stage=None, **kw):
        p = MS.after_image_path(images_dir, rec.name, stage) if stage else Path(images_dir) / "x.bmp"
        p.write_bytes(b"BM")
        return p, None

    old = batch.PatternRunner, batch.recall_position, AI.grab_after_mill_image
    batch.PatternRunner = _Runner
    batch.recall_position = lambda sem, rec, **kw: recalled.append(rec.name)
    AI.grab_after_mill_image = fake_grab
    err = None
    try:
        with redirect_stdout(io.StringIO()) as out:
            try:
                batch.mill_positions_sequential(
                    sem, db=db, position_names=names, ely_local_path=ely, db_path=pf,
                    move_method="movestage", wait_stage_idle=False, settle_s=0.0,
                    load_method=load_method, wait_strategy="none", done_confirm_s=0.0,
                    refine_names=names, ref_base_dir=d, run_log=log, after_image=after,
                    after_image_dir=d / "images", stage=stage, reference_skip_ok=ref_ok,
                    _refine_fn=fake_refine)
            except MS.StageReferenceError as e:
                err = e
    finally:
        batch.PatternRunner, batch.recall_position, AI.grab_after_mill_image = old
    return dict(recalled=recalled, jobs=jobs, refs=refs, log=log, err=err, sem=sem,
                out=out.getvalue(), cfgs=cfgs)


def test_rough_then_prepolish_then_polish_batches():
    d, pf = _session()
    names = ["position_1", "position_2"]
    # Rough: authored pattern still overrides; refine to the capture image; images recorded.
    r = _batch(d, pf, names, "rough", ely=d / "rough.ely")
    assert r["err"] is None and r["recalled"] == names
    assert [j.ely_local_path.name for j in r["jobs"]] == ["authored.ely", "authored.ely"]
    assert r["refs"] == {"position_1": None, "position_2": None}           # capture image
    assert r["cfgs"]["position_1"].abort_on_failure is False               # rough: as before
    db = PositionDatabase.load(pf)
    rough1 = db.get("position_1").stage_after_images["rough"]
    assert "_1-rough_" in rough1 and (d / rough1).exists()
    # Prepolish: the prepolish .ely EVERYWHERE (authored bypassed), refine to rough images.
    r = _batch(d, pf, names, "prepolish", ely=d / "prepolish.ely")
    assert r["err"] is None and r["recalled"] == names, r["out"]
    assert [j.ely_local_path.name for j in r["jobs"]] == ["prepolish.ely", "prepolish.ely"]
    assert r["refs"]["position_1"] == d / rough1
    assert r["cfgs"]["position_1"].abort_on_failure is True                # never mill unrefined
    assert ("stage_check", ("prepolish", {})) in r["log"].calls
    db = PositionDatabase.load(pf)
    pre2 = db.get("position_2").stage_after_images["prepolish"]
    assert "_2-prepolish_" in pre2 and db.get("position_2").stage_after_images["rough"]
    # Polish: refine to the prepolish images.
    r = _batch(d, pf, ["position_2"], "polish", ely=d / "polish.ely")
    assert r["refs"]["position_2"] == d / pre2
    assert "_3-polish_" in PositionDatabase.load(pf).get("position_2").stage_after_images["polish"]
    assert "authored" not in r["out"].split("using the Polish pattern")[0][-200:]


def test_missing_reference_refuses_before_any_motion_unless_oked():
    d, pf = _session()
    (d / "images" / "position_1_1-rough_20261007_101010.bmp").write_bytes(b"BM")
    names = ["position_1", "position_2"]
    r = _batch(d, pf, names, "prepolish", ely=d / "prepolish.ely")
    assert r["err"] is not None and "position_2" in str(r["err"]) and "nothing moved" in str(r["err"])
    assert r["recalled"] == [] and r["jobs"] == [] and r["sem"].calls == []  # not even beam zero
    r = _batch(d, pf, names, "prepolish", ely=d / "prepolish.ely", ref_ok={"position_2"})
    assert r["err"] is None and r["recalled"] == ["position_1"] and len(r["jobs"]) == 1
    assert ("stage_check", ("prepolish", {"position_2": "no Rough Mill after image"})) in r["log"].calls
    assert "[stage] NOT milling position_2" in r["out"]
    r = _batch(d, pf, ["position_2"], "polish", ely=d / "polish.ely", ref_ok={"position_2"})
    assert r["recalled"] == [] and "nothing to mill" in r["out"]


# --------------------------------------------------------------------------- push
def test_push_loads_and_prepares_but_never_starts():
    from ..runner import PatternJob, PatternRunner
    d, _pf = _session()
    ely = Path(__file__).resolve().parents[1] / "assets" / "patterns" / "default.ely"
    sem = _Sem()
    job = PatternJob(ely_local_path=ely, smartfib_drop_dir=d / "drop", arm=True,
                     start_milling=False, wait_strategy="none",
                     smartfib_load_delay_s=0.0, smartfib_prepare_delay_s=0.0)
    with redirect_stdout(io.StringIO()) as out:
        PatternRunner(sem).run(job)
    cmds = [c[1] for c in sem.calls if c[0] == "execute"]
    assert cmds == ["CMD_SMARTFIB_LOAD_ELY", "CMD_SMARTFIB_PREPARE_EXPOSURE"], cmds
    assert (d / "drop" / "ApiLayout.ely").exists() and "milling NOT started" in out.getvalue()
    sem = _Sem()
    job.arm = False                                                         # dry run says so
    with redirect_stdout(io.StringIO()) as out:
        PatternRunner(sem).run(job)
    assert sem.calls == [] and "CMD_FIB_START_MILLING is NOT sent" in out.getvalue()


# --------------------------------------------------------------------------- manual
def test_manual_goto_align_push_record_and_the_manual_run_log():
    from .. import site_ops as SO
    from ..milling_workflow import RefineConfig
    d, pf = _session()
    db = PositionDatabase.load(pf)
    rec = db.get("position_1")
    logs = []
    SO.GUARD_S = SO.GUARD_MOTION_S = 0.0        # the window itself: test_not_milling_window
    # every action refuses while SmartSEM indicates milling -- and changes nothing
    busy = _Sem(milling=True)
    for fn in (lambda: SO.site_goto(busy, rec, log=logs.append),
               lambda: SO.site_push(busy, rec, "rough", d / "rough.ely", d, drop_dir=d,
                                    drop_name="A.ely", log=logs.append),
               lambda: SO.site_after_image(busy, db, rec, "rough", pf, log=logs.append)):
        try:
            fn()
            raise AssertionError("not refused")
        except SO.SiteRefused as e:
            assert "milling" in str(e)
    assert busy.calls == []
    # goto: beam shift zeroed BEFORE the move, then objective + mag
    sem = _Sem()
    import smartfib_executor.positions as P
    SO.site_goto(sem, rec, log=logs.append)
    kinds = [c[0] if c[0] != "set" else c[1] for c in sem.calls]
    assert kinds.index("AP_FIB_BEAM_SHIFT_X") < kinds.index("move") < \
        kinds.index("AP_FIB_OBJECTIVE_POTENTIAL") < kinds.index("AP_FIB_MAGNIFICATION")
    assert sem.calls[kinds.index("move")][4] == 2.0                          # recorded tilt
    # align (prepolish): refused without a rough after image; then aligns to it
    try:
        SO.site_align(sem, rec, "prepolish", d, RefineConfig(), log=logs.append)
        raise AssertionError("not refused")
    except SO.SiteRefused as e:
        assert "no Rough Mill after image" in str(e)
    rough = d / "images" / "position_1_1-rough_20261007_101010.bmp"
    rough.write_bytes(b"BM")
    seen = {}

    class _Res:
        success, method, residual_drift_m = True, "beam_shift", 1e-7
        initial_drift_x_m, initial_drift_y_m = 2e-6, 1e-6
        applied_beam_shift_delta_m, applied_stage_delta_m = (2e-6, 1e-6), (0.0, 0.0)
        message = "ok"

    def fake_refine(sem, rec, cfg, **kw):
        seen.update(kw)
        return _Res()
    SO.site_align(sem, rec, "prepolish", d, RefineConfig(), log=logs.append,
                  _refine_fn=fake_refine)
    assert seen["reference_path"] == rough
    # push (prepolish): the prepolish pattern even though position_1 has an authored one
    sem = _Sem()

    class _R:
        def __init__(self, s): pass
        def run(self, job):
            assert job.start_milling is False and job.arm is True
            seen["pushed"] = job.ely_local_path
    SO.site_push(sem, rec, "prepolish", d / "prepolish.ely", d, drop_dir=d / "drop",
                 drop_name="A.ely", log=logs.append, _runner_cls=_R)
    assert seen["pushed"].name == "prepolish.ely"
    # rough push uses the authored pattern
    SO.site_push(sem, rec, "rough", d / "rough.ely", d, drop_dir=d / "drop",
                 drop_name="A.ely", log=logs.append, _runner_cls=_R)
    assert seen["pushed"].name == "authored.ely"
    SO.site_push(sem, rec, "prepolish", d / "prepolish.ely", d, drop_dir=d / "drop",
                 drop_name="A.ely", log=logs.append, _runner_cls=_R)
    # far from the site -> warned (not refused)
    far = _Sem(); far.get_float = lambda m, f=far.get_float: 5e-3 if m == "AP_STAGE_AT_X" else f(m)
    SO.site_push(far, rec, "prepolish", d / "prepolish.ely", d, drop_dir=d / "drop",
                 drop_name="A.ely", log=logs.append, _runner_cls=_R)
    assert any("um from position_1's recorded position" in l for l in logs)

    # record the prepolish after image -> positions.json + a manual run log with the
    # align and the push of this position and stage
    def fake_grab(sem, rec, images_dir, *, milling_active, log=print, stage=None, **kw):
        assert milling_active() is False
        p = MS.after_image_path(images_dir, rec.name, stage)
        p.write_bytes(b"BM")
        return p, None
    path, warn = SO.site_after_image(sem, db, rec, "prepolish", pf, log=logs.append,
                                     _grab_fn=fake_grab)
    assert "_2-prepolish_" in path.name and warn is None
    assert PositionDatabase.load(pf).get("position_1").stage_after_images["prepolish"] \
        == f"images/{path.name}"
    run = json.loads(sorted((d / "runs").glob("run_*.json"))[-1].read_text())
    assert run["kind"] == "manual" and run["stage"] == "prepolish" and run["status"] == "completed"
    site = run["sites"][0]
    assert site["status"] == "milled" and site["after_image"] == f"images/{path.name}"
    assert site["pattern"]["path"] == "prepolish.ely" and site["refine"]["reference"] == rough.name
    state = json.loads((d / "runs" / SO.MANUAL_STATE).read_text())
    assert "position_1|prepolish" not in state and "position_1|rough" in state  # consumed


# --------------------------------------------------------------------------- CLI
def _cli(argv, sem=None, *, tty=False):
    import smartfib_executor.cli as C
    captured, connected = {}, []
    sem = sem or _Sem()
    old = C.mill_positions_sequential, C.SmartSEM, sys.stdin

    class _Ctx:
        def __enter__(self):
            connected.append(1)
            return sem
        def __exit__(self, *a): pass
    sem.connect_local = sem.connect_remoting = lambda *a: None

    class _In(io.StringIO):
        def isatty(self): return tty
    C.mill_positions_sequential = lambda s, **kw: captured.update(kw)
    C.SmartSEM = _Ctx
    sys.stdin = _In("n\n")
    try:
        with redirect_stdout(io.StringIO()) as out:
            rc = C.main(argv)
    finally:
        C.mill_positions_sequential, C.SmartSEM, sys.stdin = old
    return rc, captured, out.getvalue(), bool(connected)


def test_cli_stage_needs_the_ok_before_connecting():
    d, pf = _session()
    (d / "images" / "position_1_1-rough_20261007_101010.bmp").write_bytes(b"BM")
    base = ["mill-positions", str(d / "prepolish.ely"), "position_1", "position_2",
            "--positions-file", str(pf), "--stage", "prepolish", "--refine-all"]
    rc, cap, out, conn = _cli(base)                                         # dry run
    assert rc == 0 and not conn and "position_2: SKIP -- no Rough Mill after image" in out
    assert "REFINE to position_1_1-rough_20261007_101010.bmp" in out and "_2-prepolish_" in out
    rc, cap, out, conn = _cli(base + ["--arm"])                             # GUI/pipe: refuse
    assert rc == 3 and cap == {} and not conn, out
    assert "--accept-reference-skips position_2" in out
    run = json.loads(sorted((d / "runs").glob("run_*.json"))[-1].read_text())
    assert run["stage"] == "prepolish" and "nothing moved" in run["message"]
    rc, cap, out, conn = _cli(base + ["--arm"], tty=True)                  # terminal, "n"
    assert rc == 3 and not conn
    rc, cap, out, conn = _cli(base + ["--arm", "--accept-reference-skips", "position_2"])
    assert rc == 0 and cap["stage"] == "prepolish" and cap["reference_skip_ok"] == {"position_2"}
    assert cap["after_image"] is True                                       # --stage turns it on
    rc, cap, out, conn = _cli(base + ["--arm", "--accept-reference-skips", "position_2",
                                      "--no-after-image"])
    assert cap["after_image"] is False
    rc, cap, out, conn = _cli(base + ["--tilt-check"])                     # tilt over the rest
    line = [l for l in out.splitlines() if l.startswith("TILT-CHECK:")][0]
    assert "allowed=position_1 " in line and "position_2" not in line.split("excluded=")[1]


def test_cli_site_dry_runs_never_connect():
    d, pf = _session()
    common = ["--positions-file", str(pf)]
    rc, _c, out, conn = _cli(["site", "goto", "position_1", "--stage", "polish"] + common)
    assert rc == 0 and not conn and "Zero FIB beam shift" in out and "T=2.00 deg" in out
    rc, _c, out, conn = _cli(["site", "push", "position_1", "--stage", "polish",
                              "--ely", str(d / "polish.ely")] + common)
    assert rc == 0 and not conn and "NOT sent" in out and "polish.ely" in out
    rc, _c, out, conn = _cli(["site", "push", "position_1", "--stage", "rough"] + common)
    assert rc == 2 and not conn and "needs --ely" in out                    # push needs --ely
    try:                                                                    # --stage required
        _cli(["site", "after-image", "position_1"] + common)
        raise AssertionError("--stage not required")
    except SystemExit as e:
        assert e.code == 2
    rc, _c, out, conn = _cli(["site", "align", "position_1", "--stage", "polish"] + common)
    assert rc == 2 and "no Prepolish after image" in out and not conn
    rc, _c, out, conn = _cli(["site", "after-image", "position_12", "--stage", "polish"] + common)
    assert rc == 0 and "position_12_3-polish_<time>.bmp" in out and not conn
    rc, _c, out, conn = _cli(["site", "goto", "position_1", "--stage", "rough", "--arm"] + common,
                             _Sem(milling=True))
    assert rc == 4 and conn and "refused" in out                             # armed, milling
    rc, _c, out, conn = _cli(["site", "align", "position_1", "--stage", "polish", "--arm"] + common)
    assert rc == 2 and not conn and "no Prepolish after image" in out       # before connecting


# --------------------------------------------------------------------------- report
def test_report_documents_each_stage_without_overriding_rough():
    from ..report.runlog import RunLog
    from ..report.html_report import build_report
    d, pf = _session()
    db = PositionDatabase.load(pf)
    from ..report.tests.test_report import _bmp
    for n in ("position_1_1-rough_20261007_101010", "position_1_2-prepolish_20261007_121212"):
        _bmp(d / "images" / f"{n}.bmp", 40)
    _bmp(d / "images" / "position_1.bmp", 30)
    rl = RunLog(d, kind="mill", stage="rough", positions_file=str(pf), printer=lambda m: None)
    rl.stage_check("rough", {})
    rl.site_start("position_1", 1, 1, db.get("position_1"))
    rl.site_pattern(d / "patterns" / "authored.ely", "authored", "authored")
    rl.site_after_image(d / "images" / "position_1_1-rough_20261007_101010.bmp")
    rl.site_end("milled"); rl.finish("completed")
    rl = RunLog(d, kind="mill", stage="prepolish", positions_file=str(pf), printer=lambda m: None)
    rl.stage_check("prepolish", {"position_2": "no Rough Mill after image"})
    rl.site_start("position_1", 1, 1, db.get("position_1"))
    rl.site_pattern(d / "prepolish.ely", "stage", "prepolish")
    rl.site_after_image(d / "images" / "position_1_2-prepolish_20261007_121212.bmp")
    rl.site_end("error", "stopped by hand"); rl.finish("stopped")
    h = build_report(pf, export_map=False, mosaic=False).path.read_text(encoding="utf-8")
    import re
    sec = re.search(r"id='pos-position_1'.*?</section>", h, re.S).group(0)
    from ..report.tests.test_report import _line
    r1 = _line(h, "position_1", "Rough")
    assert "s-milled" in r1 and "authored.ely (authored)" in r1             # rough unchanged
    assert 'data-view="prepolish" data-views="before rough prepolish"' in sec
    assert 'class="im-prepolish iv v-prepolish"' in sec
    p1 = _line(h, "position_1", "Prepolish")
    assert "s-error" in p1 and "prepolish.ely" in p1 and "stopped by hand" in p1
    assert "after image position_1_2-prepolish_20261007_121212.bmp" in p1
    assert h.index("</i>Rough</span>", h.index("id='pos-position_1'")) < \
        h.index("</i>Prepolish</span>", h.index("id='pos-position_1'"))     # listed in order
    p2 = _line(h, "position_2", "Prepolish")
    assert "s-assumed" in _line(h, "position_2", "Rough")
    assert "s-not_milled" in p2 and "no Rough Mill after image" in p2       # prepolish skip
    assert "All prepolish" in h and ">Prepolish</td>" in h                  # runs table stage
    assert "<h3>Prepolish · prepolish.ely</h3>" in h                        # Patterns section


def test_not_milling_window_one_reading_is_not_enough():
    """Review 2026-10-07: the milling state can read idle for a moment between exposures,
    so the manual buttons watch it for a window and refuse on ANY milling reading."""
    from .. import site_ops as SO
    sem, t = _Sem(), [0.0]
    reads = iter([False, False, True])
    import smartfib_executor.batch as batch
    old = batch._is_milling_active
    batch._is_milling_active = lambda s: next(reads)
    try:
        SO._milling_guard(sem, "Go to x", 10.0, log=lambda m: None, clock=lambda: t[0],
                          sleep=lambda dt: t.__setitem__(0, t[0] + dt))
        raise AssertionError("not refused")
    except SO.SiteRefused:
        pass
    finally:
        batch._is_milling_active = old
    assert sem.calls == [] and t[0] == 1.0                                  # 3rd poll at 1 s
    t[0] = 0.0
    SO._milling_guard(sem, "Push", 5.0, log=lambda m: None, clock=lambda: t[0],
                      sleep=lambda dt: t.__setitem__(0, t[0] + dt))
    assert t[0] >= 5.0 and sem.calls == []                                  # read-only


def test_review_guards_load_method_recapture_old_files_and_suspect_frames():
    import dataclasses
    d, pf = _session()
    (d / "images" / "position_1_1-rough_20261007_101010.bmp").write_bytes(b"BM")
    r = None
    try:
        _batch(d, pf, ["position_1"], "prepolish", ely=d / "prepolish.ely", load_method="none")
        raise AssertionError("load_method none accepted")
    except ValueError as e:
        assert "smartfib_drop" in str(e)
    rc, cap, out, conn = _cli(["mill-positions", str(d / "prepolish.ely"), "--positions-file",
                               str(pf), "--stage", "polish", "--load-method", "none", "--arm"])
    assert rc == 2 and not conn and cap == {}
    rc, cap, out, conn = _cli(["mill-positions", str(d / "prepolish.ely"), "position_1",
                               "--positions-file", str(pf), "--stage", "prepolish"])
    assert "position_1 will be milled WITHOUT refine" in out
    # re-capture at ANOTHER place drops the map; same place keeps it
    db = PositionDatabase.load(pf)
    old = MS.with_after_image(db.get("position_1"), "rough",
                              d / "images" / "position_1_1-rough_20261007_101010.bmp", d)
    moved = dataclasses.replace(_rec("position_1"), stage_at=StageAt(1.2e-3, 2e-3, 3e-3, 12.0, 0, 2.0))
    assert MS.carry_over(old, moved).stage_after_images is None
    near = dataclasses.replace(_rec("position_1"), stage_at=StageAt(1.01e-3, 2e-3, 3e-3, 12.0, 0, 2.0))
    assert MS.carry_over(old, near).stage_after_images == old.stage_after_images
    # files from before the (re-)capture are never a reference
    from datetime import datetime
    late = dataclasses.replace(_rec("position_1"), captured_unix_s=datetime(
        2026, 10, 7, 12, 0, 0).timestamp())
    assert MS.reference_for_stage(late, "prepolish", d)[0] is None
    # an unconfirmed (stale) staged frame is kept as *_suspect.bmp and never used
    from ..after_image import grab_after_mill_image
    from .test_after_image import ScopeSem, _Rec
    import smartfib_executor.frame_grab as FG
    oldw = FG.wait_frozen
    FG.wait_frozen = lambda sem, t: False
    try:
        path, warn = grab_after_mill_image(ScopeSem(), _Rec(), d / "images",
                                           milling_active=lambda: False, frame_wait_s=0.0,
                                           frozen_timeout_s=0.0, sleep=lambda s: None,
                                           log=lambda m: None, stage="rough")
    finally:
        FG.wait_frozen = oldw
    assert path is None and "NOT used" in warn
    assert len(list((d / "images").glob("position_3_1-rough_*_suspect.bmp"))) == 1
    assert MS.stage_image_history(d / "images", "position_3", "rough") == []
    assert not list(d.glob("*.tmp"))                                        # atomic saves


def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t(); print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1; print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            import traceback; traceback.print_exc()
            failed += 1; print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
