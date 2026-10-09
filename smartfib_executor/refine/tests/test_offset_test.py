"""Tests for the T06 offset-injection script (refine/offset_test.py)."""
from __future__ import annotations

import tempfile
from pathlib import Path

from ..sources import SimulatedRefineScope
from ..offset_test import run_offset_test, default_offsets, main, INJECT_MAX_M
from .. import geometry as g

NOSLEEP = lambda s: None


class _Rec(SimulatedRefineScope):
    """Counts stage moves and grabs."""
    def __init__(self, **k):
        super().__init__(**k); self.moves = []; self.nimg = 0
    def move_stage_xy(self, x, y):
        self.moves.append((x, y)); super().move_stage_xy(x, y)
    def grab_fib_image(self, mag, scan_rate=None):
        self.nimg += 1; return super().grab_fib_image(mag, scan_rate=scan_rate)


class _WrongHanded(_Rec):
    """Image moves the OPPOSITE way to the calibrated model (a sign error)."""
    def move_stage_xy(self, x, y):
        b = list(self.true_offset)
        super().move_stage_xy(x, y)
        self.true_offset[0] = b[0] - (self.true_offset[0] - b[0])
        self.true_offset[1] = b[1] - (self.true_offset[1] - b[1])


def _sim(cls=_Rec, **k):
    k.setdefault("stage_tilt_deg", 12.0)
    return cls(magnification=2000.0, noise_sigma=4.0, **k)


def test_default_offsets_give_13um_image_drift_at_each_tilt():
    for tilt in (2.0, 12.0, 30.0):
        o = default_offsets(tilt); sx, sy = g.stage_scale(tilt)
        assert abs(o["x"] * sx - 13e-6) < 1e-9 and abs(o["y"] * sy - 13e-6) < 1e-9
        assert max(o.values()) <= INJECT_MAX_M


def test_plan_only_passes_moves_only_for_the_offset_and_restores():
    s = _sim(); st0, b0 = s.get_stage_xy(), s.get_beam_shift()
    r = run_offset_test(s, live=False, sleep=NOSLEEP)
    assert r.ok and [a.verdict for a in r.axes] == ["PASS (plan)"] * 2
    assert s.get_beam_shift() == b0 and s.get_stage_xy() == st0
    for a in r.axes:
        assert a.plan_error_m < 0.5e-6 and not a.live_run
    assert r.return_offset_m is not None and r.return_offset_m < 0.2e-6


def test_live_corrects_both_axes():
    s = _sim(); st0 = s.get_stage_xy()
    r = run_offset_test(s, live=True, sleep=NOSLEEP)
    assert r.ok
    for a in r.axes:
        assert a.live_pass and a.live_residual_m < 0.3e-6
        along = a.live_applied_stage_m[0 if a.axis == "x" else 1]
        assert along * a.offset_achieved_m < 0            # reversed the offset
    assert s.get_stage_xy() == st0


def test_wrong_sign_stops_before_live_and_restores():
    s = _sim(_WrongHanded); st0, b0 = s.get_stage_xy(), s.get_beam_shift()
    r = run_offset_test(s, live=True, sleep=NOSLEEP)
    assert not r.ok and len(r.axes) == 1                   # stopped after axis X
    assert "WRONG SIGN" in r.axes[0].verdict and not r.axes[0].live_run
    assert s.get_stage_xy() == st0 and s.get_beam_shift() == b0


def test_refuses_outside_model_before_any_motion_or_imaging():
    for kw in (dict(stage_tilt_deg=45.0), dict(stage_rot_deg=316.4)):
        s = _sim(**kw)
        r = run_offset_test(s, live=True, sleep=NOSLEEP)
        assert not r.ok and r.message.startswith("REFUSED") and s.moves == [] and s.nimg == 0


def test_refuses_offset_above_injection_limit_before_motion():
    s = _sim()
    r = run_offset_test(s, offsets_m={"x": 35e-6}, axes=("x",), sleep=NOSLEEP)
    assert not r.ok and "injection limit" in r.message and s.moves == [] and s.nimg == 0


def test_small_offset_flagged_as_stage_path_not_exercised():
    s = _sim()
    r = run_offset_test(s, offsets_m={"x": 5e-6}, axes=("x",), live=True, sleep=NOSLEEP)
    assert not r.ok and "not exercised" in r.axes[0].verdict and not r.axes[0].live_run


def test_t07_30um_is_refused_by_the_aliasing_guard():
    s = _sim(); st0 = s.get_stage_xy()
    r = run_offset_test(s, offsets_m={"x": 30e-6}, axes=("x",), live=True, sleep=NOSLEEP)
    assert not r.ok and "no confident measurement" in r.axes[0].verdict
    assert "aliased" in r.axes[0].plan_message and s.get_stage_xy() == st0


def test_restore_failure_is_loud():
    logs = []
    class _Stuck(_Rec):
        def move_stage_xy(self, x, y):
            if len(self.moves) >= 1:
                self.moves.append((x, y)); return          # stage stops responding
            super().move_stage_xy(x, y)
    s = _Stuck(magnification=2000.0, noise_sigma=4.0, stage_tilt_deg=12.0)
    import smartfib_executor.refine.offset_test as ot
    old = ot._confirm_at
    try:
        def fast(scope, target, tol, **k):
            return old(scope, target, tol, timeout_s=0.3, sleep=NOSLEEP)
        ot._confirm_at = fast
        r = run_offset_test(s, axes=("x",), live=False, logger=logs.append, sleep=NOSLEEP)
    finally:
        ot._confirm_at = old
    assert not r.ok and any("RESTORE FAILED" in l and "MANUALLY" in l for l in logs)


def test_error_mid_axis_is_recorded_and_restored():
    class _GrabFails(_Rec):
        def grab_fib_image(self, mag, scan_rate=None):
            if self.nimg >= 1:
                raise RuntimeError("grab failed")
            return super().grab_fib_image(mag, scan_rate=scan_rate)
    s = _GrabFails(magnification=2000.0, noise_sigma=4.0, stage_tilt_deg=12.0)
    st0 = s.get_stage_xy()
    r = run_offset_test(s, axes=("x",), sleep=NOSLEEP)
    assert not r.ok and "error" in r.axes[0].verdict and s.get_stage_xy() == st0


def test_cli_simulate_writes_results_and_images():
    d = Path(tempfile.mkdtemp()) / "out"
    rc = main(["--simulate", "--live", "--out", str(d)])
    assert rc == 0 and (d / "results.json").exists()
    assert (d / "x_2_offset.png").exists() and (d / "9_returned.png").exists()


# ---- offset_position: file-only decoy for the pipeline test ---- #
def _pf_with(name="site_1"):
    from ...positions import PositionDatabase, PositionRecord, StageAt
    d = Path(tempfile.mkdtemp()); pf = d / "positions.json"
    db = PositionDatabase()
    db.positions[name] = PositionRecord(
        name=name, captured_unix_s=1.0, fib_magnification_x=2000.0,
        fib_objective_potential_v=16400.0, image_bmp="images/site_1.bmp",
        stage_at=StageAt(44.0e-3, 57.0e-3, 31.5e-3, 12.0, 12.99e-3, None),
        ely_pattern="patterns/site_1.ely")
    db.save(pf)
    return pf


def test_offset_position_makes_decoy_with_known_offset():
    from ...positions import PositionDatabase
    from ..offset_position import main as mk
    pf = _pf_with()
    assert mk([str(pf), "site_1", "--dx-um", "14"]) == 0
    db = PositionDatabase.load(pf)
    a, b = db.positions["site_1"], db.positions["site_1_dx14"]
    assert abs((b.stage_at.x_m - a.stage_at.x_m) - 14e-6) < 1e-12
    assert b.stage_at.y_m == a.stage_at.y_m and b.stage_at.z_m == a.stage_at.z_m
    assert b.stage_at.r_deg == a.stage_at.r_deg and b.stage_at.t_deg == a.stage_at.t_deg
    assert b.image_bmp == a.image_bmp and b.fib_magnification_x == a.fib_magnification_x
    assert b.ely_pattern is None and "TEST DECOY" in b.notes      # never mills the lamella
    assert a.ely_pattern == "patterns/site_1.ely"                 # original untouched
    assert (pf.parent / "positions.json.bak").exists()


def test_offset_position_refuses_big_or_zero_offsets_and_bad_names():
    from ..offset_position import main as mk
    pf = _pf_with()
    assert mk([str(pf), "site_1", "--dx-um", "35"]) == 2
    assert mk([str(pf), "site_1"]) == 2
    assert mk([str(pf), "nope", "--dx-um", "14"]) == 2
    assert mk([str(pf), "site_1", "--dy-um", "-12"]) == 0          # negative ok
    assert mk([str(pf), "site_1", "--dy-um", "-12"]) == 2          # already exists


def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
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
