"""Batch tilt limit (v0.46.0, tilt_guard.py).

Evan 2026-10-05: tilt must be recorded; recalling tilts far apart is the danger. Mill
batches only: 2 deg limit; positions out of range are reported up front and NOT milled;
the batch runs only after the user OKs that list. Other stage moves are unchanged.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from ..positions import PositionDatabase, PositionRecord, StageAt
from ..tilt_guard import (BATCH_TILT_LIMIT_DEG, TiltLimitError, parse_machine_line,
                          plan_batch_tilts)


def _rec(name, t):
    return PositionRecord(name=name, captured_unix_s=1.0, fib_magnification_x=2000.0,
                          fib_objective_potential_v=16400.0,
                          stage_at=StageAt(x_m=1e-3, y_m=2e-3, z_m=3e-3, r_deg=12.0, m_m=0.0,
                                           t_deg=t))


def test_plan_rules():
    assert BATCH_TILT_LIMIT_DEG == 2.0
    p = plan_batch_tilts(["a", "b", "c", "d", "e"],
                         {"a": 1.97, "b": 30.0, "c": None, "d": 3.97, "e": 3.98}, 1.97)
    ex = p.excluded
    assert p.allowed == ["a", "c", "d"] and set(ex) == {"b", "e"}, p.rows
    assert "28.03 deg from the current 1.97" in ex["b"]           # 2.00 allowed, 2.01 not
    # per-recall step: within 2 deg of the start, but 3 deg from the previous site
    q = plan_batch_tilts(["x", "y"], {"x": 0.5, "y": 3.5}, 2.0)
    assert q.allowed == ["x"] and "3.00 deg from the previous site's 0.50" in q.excluded["y"]
    got = parse_machine_line(p.machine_line())
    assert got == {"live": 1.97, "limit": 2.0, "allowed": ["a", "c", "d"],
                   "excluded": ["b", "e"]}
    assert parse_machine_line("[tilt] something else") is None
    assert any("will NOT be milled: b, e" in l for l in p.table_lines())


class _Sem:
    """Fake microscope: a scripted live tilt; records every set/execute."""
    def __init__(self, tilts):
        self.tilts = list(tilts); self.calls = []
    def get_float(self, m):
        if m == "AP_STAGE_AT_T":
            t = self.tilts[0] if len(self.tilts) == 1 else self.tilts.pop(0)
            if isinstance(t, Exception):
                raise t
            return t
        return 0.0
    def get_str(self, m):
        return "Imaging"
    def set_value(self, m, v):
        self.calls.append(("set", m, v))
    def execute(self, *a, **k):
        self.calls.append(("execute",) + a)


class _Log:
    def __init__(self): self.calls = []
    def __getattr__(self, name):
        return lambda *a, **k: self.calls.append((name, a))


def _batch(sem, tilts_by_name, *, skip_ok=None, continue_on_error=False):
    import smartfib_executor.batch as batch
    db = PositionDatabase()
    for n, t in tilts_by_name.items():
        db.positions[n] = _rec(n, t)
    recalled, milled, log = [], [], _Log()

    class _Runner:
        def __init__(self, sem): pass
        def run(self, job): milled.append(1)
    old = batch.PatternRunner, batch.recall_position
    batch.PatternRunner = _Runner
    batch.recall_position = lambda sem, rec, **kw: recalled.append(rec.name)
    try:
        with redirect_stdout(io.StringIO()) as out:
            err = None
            try:
                batch.mill_positions_sequential(
                    sem, db=db, position_names=list(tilts_by_name), ely_local_path=Path("x.ely"),
                    move_method="movestage", wait_stage_idle=False, settle_s=0.0,
                    load_method="none", wait_strategy="none", done_confirm_s=0.0,
                    continue_on_error=continue_on_error, run_log=log, tilt_skip_ok=skip_ok)
            except TiltLimitError as e:
                err = e
    finally:
        batch.PatternRunner, batch.recall_position = old
    return recalled, len(milled), log, err, out.getvalue()


def test_batch_refuses_before_any_motion_unless_the_skips_are_ok():
    sem = _Sem([1.97])
    recalled, n, log, err, out = _batch(sem, {"a": 1.97, "b": 30.0, "c": 1.97})
    assert err is not None and "b" in str(err) and "nothing moved" in str(err)
    assert recalled == [] and n == 0 and sem.calls == []          # not even the beam-shift zero
    sem = _Sem([1.97])
    recalled, n, log, err, out = _batch(sem, {"a": 1.97, "b": 30.0, "c": 1.97}, skip_ok={"b"})
    assert err is None and recalled == ["a", "c"] and n == 2, out
    assert ("tilt_check", (1.97, 2.0, {"b": "28.03 deg from the current 1.97 deg"})) in log.calls
    assert "[tilt] NOT milling b" in out
    sem = _Sem([RuntimeError("COM")])                               # tilt unreadable
    recalled, n, log, err, out = _batch(sem, {"a": 1.97})
    assert err is not None and recalled == [] and sem.calls == []


def test_live_tilt_recheck_before_each_recall():
    # starts at 1.97; someone tilts to 10 deg after the first site -> the next site refuses
    sem = _Sem([1.97, 1.97, 10.0])
    recalled, n, log, err, out = _batch(sem, {"a": 1.97, "b": 1.97}, continue_on_error=True)
    assert recalled == ["a"] and n == 1, out
    assert "tilt guard: b is recorded at 1.97 deg, the stage is at 10.00" in out


def _cli(argv, sem, *, tty=False):
    import smartfib_executor.cli as C
    captured = {}
    old = C.mill_positions_sequential, C.SmartSEM, sys.stdin

    class _Ctx:
        def __enter__(self): return sem
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
    return rc, captured, out.getvalue()


def _session():
    d = Path(tempfile.mkdtemp()); pf = d / "positions.json"
    db = PositionDatabase()
    for n, t in (("p1", 1.97), ("p2", 30.0), ("p3", 2.5)):
        db.positions[n] = _rec(n, t)
    db.save(pf)
    ely = d / "x.ely"; ely.write_text("<x/>")
    return d, pf, ely


def test_cli_tilt_check_is_read_only_and_parseable():
    d, pf, ely = _session()
    sem = _Sem([1.97])
    rc, cap, out = _cli(["mill-positions", str(ely), "--positions-file", str(pf),
                         "--tilt-check", "--arm"], sem)
    assert rc == 0 and cap == {} and sem.calls == []               # no batch, no set/execute
    line = [l for l in out.splitlines() if l.startswith("TILT-CHECK:")][0]
    got = parse_machine_line(line)
    assert got["allowed"] == ["p1", "p3"] and got["excluded"] == ["p2"]
    assert not (d / "runs").exists()                               # not a run


def test_cli_armed_needs_the_ok_and_mills_only_the_rest():
    d, pf, ely = _session()
    base = ["mill-positions", str(ely), "--positions-file", str(pf), "--arm"]
    rc, cap, out = _cli(base, _Sem([1.97]))                        # GUI / pipe: no prompt
    assert rc == 3 and cap == {} and "--accept-tilt-skips p2" in out, out
    run = json.loads(sorted((d / "runs").glob("run_*.json"))[-1].read_text())
    assert run["status"] == "stopped" and "nothing moved" in run["message"]
    rc, cap, out = _cli(base, _Sem([1.97]), tty=True)              # terminal, answers "n"
    assert rc == 3 and cap == {}
    rc, cap, out = _cli(base + ["--accept-tilt-skips", "p2"], _Sem([1.97]))
    assert rc == 0 and cap["tilt_skip_ok"] == {"p2"}, out
    rc, cap, out = _cli(base + ["--move-method", "none"], _Sem([RuntimeError("x")]))
    assert rc == 0 and "tilt_skip_ok" in cap                       # no stage moves: no check
    rc, cap, out = _cli(["mill-positions", str(ely), "--positions-file", str(pf)], _Sem([1.97]))
    assert rc == 0 and cap == {} and "T=30.00 deg" in out and "Tilt limit" in out   # dry run


def test_report_shows_skipped_positions_as_not_milled():
    from ..report.runlog import RunLog
    from ..report.tests.test_report import _session as rsession, _build
    d, pf, db, std = rsession()
    rl = RunLog(d, kind="mill", positions_file=str(pf), standard_pattern=str(std),
                positions=["position_1", "position_2"], printer=lambda *a: None)
    rl.tilt_check(1.97, 2.0, {"position_2": "28.03 deg from the current 1.97 deg"})
    rl.site_start("position_1", 1, 1, db.positions["position_1"])
    rl.site_end("milled")
    rl.finish("completed")
    res = _build(d, export_map=False, mosaic=False)
    h = res.path.read_text(encoding="utf-8")
    import re
    from ..report.tests.test_report import _line
    l2 = _line(h, "position_2")                                    # v0.51.1 one-line entry
    assert "s-not_milled" in l2 and "skipped: 28.03 deg" in l2 and "assumed" not in l2, l2
    assert "s-milled" in _line(h, "position_1")


def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    bad = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception as e:  # noqa: BLE001
            bad += 1
            import traceback
            print(f"FAIL  {t.__name__}: {e!r}")
            traceback.print_exc()
    print(f"\n{len(tests) - bad}/{len(tests)} passed")
    return bad


if __name__ == "__main__":
    raise SystemExit(1 if _run_all() else 0)
