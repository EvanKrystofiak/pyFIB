"""Offline tests for the SEM working-distance / coincidence branch.

The most important assertions are SAFETY ones:
  * the guarded SEM cannot move the stage or set any parameter;
  * the Z sign is correct (+Z shortens WD);
  * out-of-window WD, over-cap moves, and out-of-bounds targets all refuse.

Run: python -m smartfib_executor.coincidence.tests
"""

from __future__ import annotations

from .. import limits as L
from ..sem import FocusSem, SimulatedFocusSem, CoincidenceSafetyError
from ..workflow import measure_and_recommend, run_coincidence


# ------------------------------- SAFETY: guard ------------------------------- #
class _SpySem:
    def __init__(self):
        self.moved = False
        self.set = False
    def get_float(self, m):
        return 0.0
    def get_str(self, m):
        return "Idle"
    def execute(self, c):
        pass
    def move_stage(self, *a, **k):
        self.moved = True
    def set_value(self, *a, **k):
        self.set = True


def test_guard_blocks_move_stage():
    g = FocusSem(_SpySem())
    try:
        g.move_stage(0, 0, 0.03, 0, 0, 0)
        raise AssertionError("move_stage was not blocked")
    except CoincidenceSafetyError:
        pass


def test_guard_blocks_set_value():
    g = FocusSem(_SpySem())
    try:
        g.set_value("AP_STAGE_GOTO_Z", 0.03)
        raise AssertionError("set_value was not blocked")
    except CoincidenceSafetyError:
        pass


def test_guard_blocks_arbitrary_commands():
    g = FocusSem(_SpySem())
    for cmd in ("CMD_STAGE_INIT", "CMD_BEAM_ON", "CMD_FIB_START_MILLING"):
        try:
            g.execute(cmd)
            raise AssertionError(f"{cmd} was not blocked")
        except CoincidenceSafetyError:
            pass


def test_guard_allows_reads_and_autofocus():
    g = FocusSem(_SpySem())
    assert g.get_float("AP_WD") == 0.0
    g.execute("CMD_AUTO_FOCUS_COARSE")
    g.execute("CMD_AUTO_FOCUS_FINE")


def test_measure_only_never_moves_the_stage():
    spy = SimulatedFocusSem(z_m=30e-3, wd_m=5.3e-3)
    guarded = FocusSem(spy)
    res = measure_and_recommend(guarded, stable_idle_s=0.0, logger=lambda m: None)
    assert res.ok
    kinds = {c[0] for c in guarded.calls}
    assert kinds <= {"get_float", "get_str", "execute"}, kinds
    execs = {c[1] for c in guarded.calls if c[0] == "execute"}
    assert execs <= {"CMD_AUTO_FOCUS_COARSE", "CMD_AUTO_FOCUS_FINE"}, execs


# ------------------------------- MOVE PATH ---------------------------------- #
def test_small_move_is_performed_and_verified_when_confirmed():
    """<= 0.5 mm and confirmed: pyFIB moves Z and verifies coincidence."""
    sem = SimulatedFocusSem(z_m=30e-3, wd_m=5.3e-3)
    guarded = FocusSem(sem)
    res = run_coincidence(guarded, allow_move=True, confirm_fn=lambda rec: True,
                          stable_idle_s=0.0, logger=lambda m: None)
    # A Z move was issued and the sim ended at the target Z / WD.
    assert any(c[0] == "move_z" for c in guarded.calls)
    assert abs(sem.z_m - 30.3e-3) < 1e-9
    assert res.ok and res.recommendation.already_at_target      # verified coincident


def test_small_move_not_performed_when_declined():
    sem = SimulatedFocusSem(z_m=30e-3, wd_m=5.3e-3)
    guarded = FocusSem(sem)
    run_coincidence(guarded, allow_move=True, confirm_fn=lambda rec: False,
                    stable_idle_s=0.0, logger=lambda m: None)
    assert not any(c[0] == "move_z" for c in guarded.calls)
    assert abs(sem.z_m - 30.0e-3) < 1e-9                         # unmoved


def test_large_move_is_recommended_not_performed():
    """> 0.5 mm: pyFIB must NOT move, even if confirm says yes."""
    sem = SimulatedFocusSem(z_m=30e-3, wd_m=5.8e-3)             # 0.8 mm correction
    guarded = FocusSem(sem)
    run_coincidence(guarded, allow_move=True, confirm_fn=lambda rec: True,
                    stable_idle_s=0.0, logger=lambda m: None)
    assert not any(c[0] == "move_z" for c in guarded.calls)
    assert abs(sem.z_m - 30.0e-3) < 1e-9                         # unmoved


def test_allow_move_false_never_moves_even_when_small():
    sem = SimulatedFocusSem(z_m=30e-3, wd_m=5.3e-3)
    guarded = FocusSem(sem)
    run_coincidence(guarded, allow_move=False, confirm_fn=lambda rec: True,
                    stable_idle_s=0.0, logger=lambda m: None)
    assert not any(c[0] == "move_z" for c in guarded.calls)


def test_guard_move_z_revalidates_over_cap():
    """move_z must refuse an over-cap target even if called directly."""
    sem = SimulatedFocusSem(z_m=30e-3, wd_m=5.3e-3)
    guarded = FocusSem(sem)
    try:
        guarded.move_z(31.0e-3)          # +1.0 mm > 0.5 mm cap
        raise AssertionError("move_z did not refuse an over-cap move")
    except CoincidenceSafetyError:
        pass
    assert abs(sem.z_m - 30.0e-3) < 1e-9


def test_guard_move_z_revalidates_hard_bounds():
    sem = SimulatedFocusSem(z_m=39.8e-3, wd_m=5.0e-3)
    guarded = FocusSem(sem)
    try:
        guarded.move_z(40.2e-3)          # beyond the 40 mm ceiling
        raise AssertionError("move_z did not refuse crossing 40 mm")
    except CoincidenceSafetyError:
        pass
    assert abs(sem.z_m - 39.8e-3) < 1e-9


def test_guard_move_z_only_changes_z():
    """The move must command every other axis to its current value."""
    moved = {}

    class _Sem(SimulatedFocusSem):
        def move_stage(self, x, y, z, t, r, m):
            moved.update(dict(x=x, y=y, z=z, t=t, r=r, m=m))
            super().move_stage(x, y, z, t, r, m)

    sem = _Sem(z_m=30e-3, wd_m=5.2e-3)
    FocusSem(sem).move_z(30.2e-3)
    assert abs(moved["z"] - 30.2e-3) < 1e-9
    assert moved["x"] == 0.0 and moved["y"] == 0.0 and moved["t"] == 0.0


# ------------------------------- SIGN + math -------------------------------- #
def test_sign_wd_too_far_raises_z_toward_pole():
    rec = L.recommend_z_move(5.3e-3, 30.0e-3)
    assert abs(rec.delta_z_m - 0.3e-3) < 1e-9    # +0.3 mm
    assert abs(rec.z_target_m - 30.3e-3) < 1e-9
    assert rec.safe_to_recommend


def test_sign_wd_too_close_lowers_z():
    rec = L.recommend_z_move(4.7e-3, 30.0e-3)
    assert abs(rec.delta_z_m + 0.3e-3) < 1e-9    # -0.3 mm
    assert abs(rec.z_target_m - 29.7e-3) < 1e-9
    assert rec.safe_to_recommend


def test_already_at_target_needs_no_move():
    rec = L.recommend_z_move(5.0e-3, 30.0e-3)
    assert rec.already_at_target
    assert abs(rec.delta_z_m) < L.AT_TARGET_TOL_M


# ------------------------------- CLAMPS ------------------------------------- #
def test_over_cap_move_is_refused():
    # WD 6.0 mm -> delta 1.0 mm > 0.5 mm cap.
    rec = L.recommend_z_move(6.0e-3, 30.0e-3)
    assert not rec.within_step_cap
    assert not rec.safe_to_recommend
    assert any("per-move cap" in m for m in rec.messages)


def test_wd_outside_window_is_refused():
    rec = L.recommend_z_move(12.0e-3, 30.0e-3)
    assert not rec.wd_in_window
    assert not rec.safe_to_recommend
    assert any("trusted window" in m for m in rec.messages)


def test_hard_upper_bound_never_crossed():
    # Near the ceiling: a +0.4 mm move from 39.8 would land at 40.2 -> refuse.
    rec = L.recommend_z_move(5.4e-3, 39.8e-3)
    assert rec.z_target_m > L.Z_HARD_MAX_M
    assert not rec.within_hard_bounds
    assert not rec.safe_to_recommend
    assert any("hard limits" in m for m in rec.messages)


def test_hard_lower_bound_never_crossed():
    # From 20.1 mm, WD 4.6 mm -> delta -0.4 mm -> 19.7 mm < 20 mm -> refuse.
    rec = L.recommend_z_move(4.6e-3, 20.1e-3)
    assert rec.z_target_m < L.Z_HARD_MIN_M
    assert not rec.within_hard_bounds
    assert not rec.safe_to_recommend


def test_limits_are_the_agreed_values():
    assert L.TARGET_WD_M == 5.0e-3
    assert L.MAX_Z_STEP_M == 0.5e-3
    assert L.Z_HARD_MIN_M == 20.0e-3 and L.Z_HARD_MAX_M == 40.0e-3
    assert L.WD_SANE_MIN_M == 3.0e-3 and L.WD_SANE_MAX_M == 8.0e-3


# ------------------------------- PRECONDITIONS ------------------------------ #
def test_refuses_when_beam_off():
    guarded = FocusSem(SimulatedFocusSem(eht_v=0.0))
    res = measure_and_recommend(guarded, stable_idle_s=0.0, logger=lambda m: None)
    assert not res.ok and "beam appears OFF" in res.reason


def test_runs_when_tilted_now_allowed():
    """The 0-tilt rule was removed: a tilted stage no longer refuses."""
    guarded = FocusSem(SimulatedFocusSem(tilt_deg=15.0, wd_m=5.3e-3))
    res = measure_and_recommend(guarded, stable_idle_s=0.0, logger=lambda m: None)
    assert res.ok and res.recommendation is not None


def test_move_z_allowed_while_tilted():
    """move_z no longer refuses on tilt (Z clamps remain the pole-touch guard)."""
    sem = SimulatedFocusSem(z_m=30e-3, wd_m=5.2e-3, tilt_deg=20.0)
    FocusSem(sem).move_z(30.2e-3)
    assert abs(sem.z_m - 30.2e-3) < 1e-9


# ------------------------- coincidence imaging (SEM then FIB) ------------------------- #
def test_adjust_captures_sem_then_fib_images(tmp=None):
    import tempfile, os
    from ..clock import FakeClock
    d = tempfile.mkdtemp()
    sem = SimulatedFocusSem(z_m=30e-3, wd_m=5.3e-3)
    res = run_coincidence(FocusSem(sem), allow_move=True, confirm_fn=lambda r: True,
                          stable_idle_s=0.0, clock=FakeClock(), image_out_dir=d,
                          logger=lambda m: None)
    assert res.ok
    assert res.sem_image_path and res.fib_image_path
    assert os.path.exists(res.sem_image_path) and os.path.exists(res.fib_image_path)
    # SEM was captured before FIB (mode switch order).
    calls = [c for c in FocusSem(sem).calls]  # fresh guard has none; check via sem order
    # verify the images are named as expected
    assert res.sem_image_path.endswith("_sem.bmp")
    assert res.fib_image_path.endswith("_fib.bmp")


def test_dry_run_does_not_capture_images():
    import tempfile
    d = tempfile.mkdtemp()
    sem = SimulatedFocusSem(z_m=30e-3, wd_m=5.3e-3)
    res = run_coincidence(FocusSem(sem), allow_move=False, confirm_fn=lambda r: True,
                          stable_idle_s=0.0, image_out_dir=d, logger=lambda m: None)
    assert res.sem_image_path is None and res.fib_image_path is None


def test_imaging_capture_order_sem_before_fib():
    """The imaging module switches to SEM, grabs, then FIB, grabs -- in that order."""
    from ..imaging import capture_sem_then_fib
    from ..clock import FakeClock
    import tempfile
    order = []

    class _Sem(SimulatedFocusSem):
        def execute(self, c):
            if c in ("CMD_FIB_MODE_SEM", "CMD_FIB_MODE_FIB"):
                order.append(c)
            super().execute(c)
        def grab_bmp(self, path, **k):
            order.append(("grab", "sem" if "_sem" in str(path) else "fib"))
            super().grab_bmp(path, **k)

    sem = _Sem(z_m=30e-3, wd_m=5.0e-3)
    capture_sem_then_fib(FocusSem(sem), tempfile.mkdtemp(), clock=FakeClock(),
                         logger=lambda m: None)
    # SEM mode + grab must both precede FIB mode + grab.
    assert order.index("CMD_FIB_MODE_SEM") < order.index("CMD_FIB_MODE_FIB")
    assert order.index(("grab", "sem")) < order.index(("grab", "fib"))


def test_end_to_end_recommends_correct_move():
    guarded = FocusSem(SimulatedFocusSem(z_m=30e-3, wd_m=5.3e-3))
    res = measure_and_recommend(guarded, stable_idle_s=0.0, logger=lambda m: None)
    assert res.ok and res.recommendation is not None
    assert abs(res.recommendation.delta_z_m - 0.3e-3) < 1e-9


def test_verify_after_move_reports_coincidence():
    sem = SimulatedFocusSem(z_m=30e-3, wd_m=5.3e-3)
    sem._operator_set_z(30.3e-3)          # operator made the recommended move
    res = measure_and_recommend(FocusSem(sem), stable_idle_s=0.0, logger=lambda m: None)
    assert res.ok and res.recommendation.already_at_target


# --------- ported gisdep autofocus: two-phase confirmed idle (FakeClock) --------- #
def test_autofocus_completes_with_sustained_idle_fakeclock():
    """10 s sustained-Idle window completes instantly under FakeClock."""
    from ..autofocus import autofocus, AutoFocusConfig
    from ..clock import FakeClock
    sem = SimulatedFocusSem(z_m=30e-3, wd_m=5.3e-3)
    clk = FakeClock()
    res = autofocus(FocusSem(sem), clock=clk, cfg=AutoFocusConfig(sustained_idle_s=10.0),
                    logger=lambda m: None)
    assert abs(res.wd_after_m - 5.3e-3) < 1e-9
    assert res.did_coarse and res.did_fine
    # It really did wait the virtual 10 s window (twice: coarse + fine).
    assert clk.now() >= 20.0


def test_autofocus_raises_when_routine_never_starts():
    """require_started: if DP_AUTO_FUNCTION never leaves Idle -> raise."""
    from ..autofocus import autofocus, AutoFocusConfig, AutoFunctionNeverStarted
    from ..clock import FakeClock
    sem = SimulatedFocusSem(z_m=30e-3, wd_m=5.3e-3, never_busy=True)
    try:
        autofocus(FocusSem(sem), clock=FakeClock(),
                  cfg=AutoFocusConfig(sustained_idle_s=10.0, startup_timeout_s=5.0),
                  logger=lambda m: None)
        raise AssertionError("expected AutoFunctionNeverStarted")
    except AutoFunctionNeverStarted:
        pass


def test_wait_for_idle_flicker_resets_sustained_timer():
    """A busy flicker mid-settle resets the sustained-Idle timer."""
    from ..autofocus import wait_for_auto_idle, AutoFocusConfig
    from ..clock import FakeClock

    class _Seq:
        # leaves idle (1), then flickers idle/busy before finally holding idle
        def __init__(self, seq):
            self.seq = list(seq); self.i = 0
        def execute(self, c):
            pass
        def get_float(self, m):
            if m == "DP_AUTO_FUNCTION":
                v = self.seq[min(self.i, len(self.seq) - 1)]; self.i += 1
                return float(v)
            raise KeyError(m)

    # start busy, idle briefly, busy again (reset), then idle forever
    sem = _Seq([1, 0, 0, 1, 0] + [0] * 100)
    clk = FakeClock()
    wait_for_auto_idle(sem, clock=clk,
                       cfg=AutoFocusConfig(sustained_idle_s=5.0, poll_interval_s=0.5),
                       logger=lambda m: None)
    # It consumed the busy-again flicker (index advanced well past it).
    assert sem.i > 5


def test_autofocus_abort_issues_cmd_and_raises():
    from ..autofocus import autofocus, AutoFocusConfig, AutoFocusAborted
    from ..clock import FakeClock

    class _Abort:
        def is_set(self):
            return True

    class _Sem(SimulatedFocusSem):
        def __init__(self, **k):
            super().__init__(**k); self.aborted = False
        def execute(self, c):
            if c == "CMD_ABORT_AUTO":
                self.aborted = True
            super().execute(c)

    sem = _Sem(z_m=30e-3, wd_m=5.3e-3)
    # FocusSem forbids CMD_ABORT_AUTO... so abort talks to the raw sem here.
    try:
        autofocus(sem, clock=FakeClock(), cfg=AutoFocusConfig(),
                  abort=_Abort(), logger=lambda m: None)
        raise AssertionError("expected AutoFocusAborted")
    except AutoFocusAborted:
        pass
    assert sem.aborted


def test_overall_timeout_raises():
    from ..autofocus import wait_for_auto_idle, AutoFocusConfig, AutoFunctionTimeout
    from ..clock import FakeClock

    class _StuckBusy:
        def __init__(self):
            self.i = 0
        def execute(self, c):
            pass
        def get_float(self, m):
            if m == "DP_AUTO_FUNCTION":
                self.i += 1
                return 1.0            # never returns to idle
            raise KeyError(m)

    try:
        wait_for_auto_idle(_StuckBusy(), clock=FakeClock(),
                           cfg=AutoFocusConfig(sustained_idle_s=5.0,
                                               overall_timeout_s=3.0,
                                               poll_interval_s=0.5),
                           logger=lambda m: None)
        raise AssertionError("expected AutoFunctionTimeout")
    except AutoFunctionTimeout:
        pass


def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t(); print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1; print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1; print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
