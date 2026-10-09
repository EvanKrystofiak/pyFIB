"""Run log (v0.40.0): records each site of an armed mill batch for the report.

The key safety property: the log can never change what the microscope does. A run
log that fails on every call must leave the batch's sequence of operations exactly
as it is with no log at all.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
from pathlib import Path

from ..runlog import RunLog, load_run_logs, refine_summary, SCHEMA


def _tmp() -> Path:
    return Path(tempfile.mkdtemp())


def _read(rl: RunLog) -> dict:
    return json.loads(rl.path.read_text(encoding="utf-8"))


class _Clock:
    def __init__(self, t=1790899257.0):
        self.t = t
    def __call__(self):
        self.t += 1.0
        return self.t


def test_lifecycle_and_saved_after_every_step():
    d = _tmp()
    ely = d / "std.ely"; ely.write_text("<ELAYOUT/>")
    msgs = []
    rl = RunLog(d, positions_file=str(d / "positions.json"), standard_pattern=str(ely),
                positions=["p1"], options={"move_method": "movestage"}, clock=_Clock(),
                printer=msgs.append)
    assert rl.path.parent == d / "runs" and rl.path.name.startswith("run_")
    assert _read(rl)["status"] == "running" and _read(rl)["schema"] == SCHEMA
    assert _read(rl)["standard_pattern"] == "std.ely"
    rl.site_start("p1", 1, 1)
    assert _read(rl)["sites"][0]["status"] == "running"          # on disk already
    rl.site_pattern(ely, "standard", "using the standard pattern")
    rl.site_end("milled")
    rl.finish("completed")
    data = _read(rl)
    s = data["sites"][0]
    assert s["status"] == "milled" and s["pattern"]["path"] == "std.ely"
    assert len(s["pattern"]["sha256"]) == 64 and s["duration_s"] > 0
    assert data["status"] == "completed" and data["finished"]
    assert any("Run log:" in m for m in msgs)
    logs = load_run_logs(d)
    assert len(logs) == 1 and logs[0]["_file"] == rl.path.name


def test_two_runs_in_the_same_second_get_separate_files():
    d = _tmp()
    a = RunLog(d, clock=lambda: 1790899257.0, printer=lambda m: None)
    b = RunLog(d, clock=lambda: 1790899257.0, printer=lambda m: None)
    assert a.path != b.path and b.path.name.endswith("_2.json")


def test_finish_closes_a_site_that_was_cut_off():
    d = _tmp()
    rl = RunLog(d, printer=lambda m: None)
    rl.site_start("p1", 1, 2)
    rl.finish("stopped", "interrupted (Ctrl+C)")
    s = _read(rl)["sites"][0]
    assert s["status"] == "stopped" and _read(rl)["status"] == "stopped"


def test_unwritable_session_never_raises_and_warns_once():
    d = _tmp()
    blocker = d / "not_a_dir"; blocker.write_text("x")
    msgs = []
    rl = RunLog(blocker, printer=msgs.append)          # runs/ can't be made inside a file
    rl.start_console()
    rl.site_start("p1", 1, 1)
    rl.site_refine(object())
    rl.site_end("milled")
    rl.finish("completed")
    assert len([m for m in msgs if "WARNING" in m]) == 1, msgs
    assert sys.stdout is sys.__stdout__ or not hasattr(sys.stdout, "_sink")


def test_console_copy_and_restore():
    d = _tmp()
    old_out = sys.stdout
    rl = RunLog(d, printer=lambda m: None)
    rl.start_console()
    print("hello from the batch")
    rl.finish("completed")
    assert sys.stdout is old_out
    assert "hello from the batch" in rl.console_path.read_text(encoding="utf-8")
    assert _read(rl)["console_log"] == rl.console_path.name


def test_console_copy_failure_keeps_real_output():
    from ..runlog import _Tee
    real = io.StringIO()

    class Broken:
        def write(self, s): raise OSError("disk full")
        def flush(self): pass
    warned = []
    t = _Tee(real, Broken(), warned.append)
    t.write("line 1\n"); t.write("line 2\n")
    assert real.getvalue() == "line 1\nline 2\n" and len(warned) == 1


def test_refine_summary_from_a_real_result():
    from ...refine.refine import RefineResult
    r = RefineResult(success=True, method="beam_shift", initial_drift_x_m=1.2e-6,
                     initial_drift_y_m=-0.5e-6, initial_drift_mag_m=1.3e-6,
                     residual_drift_m=0.05e-6, applied_beam_shift_delta_m=(1.2e-6, -0.5e-6),
                     applied_stage_delta_m=(-13.3e-6, 0.0), snr=12.0)
    r.coarse_fallback_tried = True
    r.coarse_stage_delta_m = (-30.85e-6, -30.17e-6)
    s = refine_summary(r)
    assert s["success"] and s["residual_um"] == 0.05 and s["stage_delta_um"] == [-13.3, 0.0]
    assert s["coarse_fallback_tried"] and s["coarse_stage_delta_um"] == [-30.85, -30.17]
    assert refine_summary(None) is None
    json.dumps(s)                                       # plain JSON


# ---------------------------------------------------------------- batch integration
def _batch(names, run_log, *, rf=None, fail_at=None, interrupt_at=None, cont=False):
    from ...tests.test_workflow import FakeSem, FakeRunner, _rec
    from ...positions import PositionDatabase
    from ...refine.refine import RefineResult
    import smartfib_executor.batch as batch
    events = []
    sem = FakeSem(events)
    db = PositionDatabase()
    for n in names:
        db.positions[n] = _rec(n)

    class Runner(FakeRunner):
        def run(self, job):
            if interrupt_at and self._name() == interrupt_at:
                raise KeyboardInterrupt
            super().run(job)
        def _name(self):
            return [e for e in events if e[0] == "recall"][-1][1]

    def fake_recall(sem, rec, **kw):
        events.append(("recall", rec.name))
        if fail_at == rec.name:
            raise RuntimeError("stage did not arrive")

    def fake_rf(sem, rec, cfg, *, ref_base_dir=None, dry_run=False, logger=None):
        events.append(("refine", rec.name))
        return RefineResult(success=True, method="beam_shift", initial_drift_x_m=1e-6,
                            initial_drift_y_m=0.0, initial_drift_mag_m=1e-6,
                            residual_drift_m=2e-8)

    real_runner, real_recall = batch.PatternRunner, batch.recall_position
    batch.PatternRunner = lambda sem: Runner(events)
    batch.recall_position = fake_recall
    err = None
    try:
        batch.mill_positions_sequential(
            sem, db=db, position_names=names, ely_local_path=Path("x.ely"),
            move_method="movestage", wait_stage_idle=False, settle_s=0.0,
            load_method="none", wait_strategy="none", done_confirm_s=0.0,
            refine_names=rf or [], _refine_fn=fake_rf, continue_on_error=cont,
            run_log=run_log)
    except BaseException as e:  # noqa: BLE001
        err = e
    finally:
        batch.PatternRunner, batch.recall_position = real_runner, real_recall
    return events, err


def test_batch_records_each_site():
    d = _tmp()
    rl = RunLog(d, printer=lambda m: None)
    events, err = _batch(["p1", "p2"], rl, rf=["p2"])
    assert err is None
    sites = _read(rl)["sites"]
    assert [s["name"] for s in sites] == ["p1", "p2"]
    assert all(s["status"] == "milled" for s in sites)
    assert sites[0]["recall"] == {"method": "movestage", "done": True}
    assert sites[0]["pattern"]["source"] == "standard"
    assert "refine" not in sites[0] and sites[1]["refine"]["residual_um"] == 0.02
    assert sites[0]["mill_conditions"]["beam_shift_um"] == [0.0, 0.0]
    assert sites[0]["stage_target"]["x_mm"] == 1.0


def test_batch_error_and_continue_are_recorded():
    d = _tmp()
    rl = RunLog(d, printer=lambda m: None)
    _events, err = _batch(["p1", "p2"], rl, fail_at="p1", cont=True)
    assert err is None
    s1, s2 = _read(rl)["sites"]
    assert s1["status"] == "error" and "stage did not arrive" in s1["message"]
    assert s2["status"] == "milled"


def test_batch_interrupt_is_recorded_as_stopped():
    d = _tmp()
    rl = RunLog(d, printer=lambda m: None)
    _events, err = _batch(["p1", "p2"], rl, interrupt_at="p1")
    assert isinstance(err, KeyboardInterrupt)
    sites = _read(rl)["sites"]
    assert len(sites) == 1 and sites[0]["status"] == "stopped"


def test_a_broken_run_log_cannot_change_the_batch():
    class Exploding:
        def __getattr__(self, name):
            def boom(*a, **k):
                raise RuntimeError(f"{name} exploded")
            return boom
    plain, err1 = _batch(["p1", "p2"], None, rf=["p1", "p2"])
    broken, err2 = _batch(["p1", "p2"], Exploding(), rf=["p1", "p2"])
    assert err1 is None and err2 is None
    assert plain == broken, (plain, broken)


def test_cli_armed_run_writes_and_finishes_the_log():
    import smartfib_executor.cli as C
    from ...positions import PositionDatabase
    from ...tests.test_workflow import _rec

    class _Sem:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def connect_local(self, *a): pass
        def connect_remoting(self): pass
        def get_float(self, m): return 2.0   # AP_STAGE_AT_T for the v0.46.0 tilt check
    d = _tmp(); pf = d / "positions.json"
    db = PositionDatabase(); db.positions["p1"] = _rec("p1"); db.save(pf)
    ely = d / "x.ely"; ely.write_text("<x/>")
    old_mps, old_sem = C.mill_positions_sequential, C.SmartSEM
    seen = {}
    try:
        C.SmartSEM = _Sem
        C.mill_positions_sequential = lambda sem, **kw: seen.update(kw)
        assert C.main(["mill-positions", str(ely), "p1", "--positions-file", str(pf), "--arm"]) == 0
        logs = load_run_logs(d)
        assert len(logs) == 1 and logs[0]["status"] == "completed"
        assert logs[0]["positions_requested"] == ["p1"] and logs[0]["kind"] == "mill"
        assert seen["run_log"] is not None

        def boom(sem, **kw):
            raise RuntimeError("milling failed")
        C.mill_positions_sequential = boom
        try:
            C.main(["mill-positions", str(ely), "p1", "--positions-file", str(pf), "--arm"])
        except RuntimeError:
            pass
        logs = load_run_logs(d)
        assert logs[-1]["status"] == "error" and "milling failed" in logs[-1]["message"]

        C.mill_positions_sequential = lambda sem, **kw: seen.update(kw)
        n = len(load_run_logs(d))
        C.main(["mill-positions", str(ely), "p1", "--positions-file", str(pf), "--arm",
                "--no-run-log"])
        assert len(load_run_logs(d)) == n and seen["run_log"] is None
        # A dry run never writes a log.
        C.main(["mill-positions", str(ely), "p1", "--positions-file", str(pf)])
        assert len(load_run_logs(d)) == n
    finally:
        C.mill_positions_sequential, C.SmartSEM = old_mps, old_sem


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
