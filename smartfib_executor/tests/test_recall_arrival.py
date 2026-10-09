"""REGRESSION: the core milling recall must wait for ARRIVAL, not merely Idle.

MoveStage is non-blocking and DP_STAGE_IS reads Idle *before* the stage starts
moving. SmartSEM.wait_stage_idle (idle-only) therefore returned mid-travel, and
recall_position -- the move between every position in a mill batch -- used it, so
autofocus/refine/milling could run while the stage was still travelling. This was the
same bug fixed piecemeal in prep_cryo / setup_move / coincidence; this fixes the
primitive at the source. Also: a FAILED optional axis set (R/M/T) is now WARNED, not
silently ignored (it used to let a lamella be milled at the wrong orientation).
"""
from __future__ import annotations

import io, contextlib

from ..smartsem_com import SmartSEM, StagePosition, SmartSEMError
from ..positions import PositionRecord, StageAt, recall_position


class _LazyStage:
    """Reads Idle immediately after a move is commanded (premature Idle), and only
    reflects the new position after N polls -- exactly the observed instrument
    behaviour. Backs a real SmartSEM object so the real wait code runs."""
    def __init__(self, idle_polls=3, fail_axes=()):
        self.pos = StagePosition(0.0, 0.0, 0.030, 0.0, 0.0, 0.0)
        self._pending = None; self._countdown = 0; self._idle_polls = idle_polls
        self.fail_axes = set(fail_axes); self.sets = []

    # --- what SmartSEM's wait code calls ---
    def stage_is(self):            # DP_STAGE_IS: Idle even while "pending"
        return 0
    def get_stage_position(self):  # position lags the command by N polls
        if self._pending is not None:
            if self._countdown > 0:
                self._countdown -= 1
            else:
                self.pos = self._pending; self._pending = None
        return self.pos
    # --- what recall calls ---
    def set_value(self, m, v):
        if m in self.fail_axes:
            raise RuntimeError(f"{m} not supported")
        self.sets.append((m, v))
        if m.startswith("AP_STAGE_GOTO_"):
            ax = m.split("_")[-1]
            p = self._pending or self.pos
            new = {"X": "x_m", "Y": "y_m", "Z": "z_m", "T": "tilt_deg",
                   "R": "rot_deg", "M": "master_z_m"}[ax]
            d = p.__dict__.copy(); d[new] = float(v)
            self._pending = StagePosition(**d); self._countdown = self._idle_polls


def _sem(stage):
    """A real SmartSEM wired to the lazy stage (bypassing COM)."""
    sem = SmartSEM.__new__(SmartSEM)
    sem._get_stage_is_code = stage.stage_is
    sem.get_stage_position = stage.get_stage_position
    sem.set_value = stage.set_value
    sem.get_float = lambda m: 0.0
    return sem


def _rec(r=12.0, m=12.99e-3, t=None):
    return PositionRecord(name="s1", captured_unix_s=1.0, fib_magnification_x=2000.0,
                          fib_objective_potential_v=16400.0,
                          stage_at=StageAt(44.491e-3, 57.380e-3, 31.5e-3, r, m, t))


def test_recall_does_not_return_until_stage_arrives():
    st = _LazyStage(idle_polls=3)
    sem = _sem(st)
    import time as _t
    _t_sleep = _t.sleep
    try:
        _t.sleep = lambda s: None   # don't actually wait between polls
        recall_position(sem, _rec(), move_method="ap_goto", set_magnification=False,
                        set_objective=False, wait_stage_idle=True, stage_timeout_s=5.0)
    finally:
        _t.sleep = _t_sleep
    # Under idle-only this returned immediately with the stage still at the old
    # position; now the read-back must be AT the target when recall returns.
    p = st.get_stage_position()
    assert abs(p.x_m - 44.491e-3) < 1e-9 and abs(p.z_m - 31.5e-3) < 1e-9
    assert abs(p.rot_deg - 12.0) < 1e-9


def test_failed_optional_axis_set_warns_loudly():
    st = _LazyStage(idle_polls=0, fail_axes={"AP_STAGE_GOTO_R"})   # rotation unsupported
    sem = _sem(st)
    buf = io.StringIO()
    import time as _t
    _t_sleep = _t.sleep
    try:
        _t.sleep = lambda s: None
        with contextlib.redirect_stdout(buf):
            recall_position(sem, _rec(r=12.0), move_method="ap_goto",
                            set_magnification=False, set_objective=False,
                            wait_stage_idle=True, stage_timeout_s=5.0)
    finally:
        _t.sleep = _t_sleep
    out = buf.getvalue()
    assert "WARNING" in out and "AP_STAGE_GOTO_R" in out    # no longer silent
    # recall still completes (R excluded from the arrival check since it was never
    # commanded); X/Y/Z did arrive.
    assert abs(st.get_stage_position().x_m - 44.491e-3) < 1e-9


def test_genuine_non_arrival_times_out_with_axis_named():
    class _Stuck(_LazyStage):
        def get_stage_position(self):   # never reaches the target
            return self.pos
    st = _Stuck(); sem = _sem(st)
    import time as _t
    _t_sleep, _t_time = _t.sleep, _t.time
    clock = [0.0]
    try:
        _t.sleep = lambda s: clock.__setitem__(0, clock[0] + s)
        _t.time = lambda: clock[0]
        try:
            recall_position(sem, _rec(), move_method="ap_goto", set_magnification=False,
                            set_objective=False, wait_stage_idle=True, stage_timeout_s=2.0)
            raise AssertionError("should have timed out")
        except SmartSEMError as e:
            assert "arrival" in str(e) and "off by" in str(e)
    finally:
        _t.sleep, _t.time = _t_sleep, _t_time


# --------------------------------------------------------------------------------------
# v0.38.0: MoveStage is the default recall. INSTRUMENT FINDING 2026-10-01: a recall 14 um
# away in X with AP_STAGE_GOTO_* did not move the stage (still 43.505 mm, target
# 43.5186 mm) and the 0.3 mm arrival check let it through; MoveStage moved it.
# --------------------------------------------------------------------------------------
class _DeadbandStage(_LazyStage):
    """GOTO ignores changes smaller than `deadband_m` (the observed behaviour); MoveStage
    always moves. Tilt read-back via get_float('AP_STAGE_AT_T')."""
    def __init__(self, deadband_m=20e-6, tilt=12.0, tilt_readable=True, **k):
        super().__init__(**k)
        self.deadband_m = deadband_m
        self.pos = StagePosition(44.491e-3, 57.380e-3, 31.5e-3, tilt, 12.0, 12.99e-3)
        self.tilt_readable = tilt_readable
        self.moves = []

    def set_value(self, m, v):
        if m.startswith("AP_STAGE_GOTO_"):
            ax = {"X": "x_m", "Y": "y_m", "Z": "z_m", "T": "tilt_deg", "R": "rot_deg",
                  "M": "master_z_m"}[m.split("_")[-1]]
            cur = getattr(self._pending or self.pos, ax)
            tol = self.deadband_m if ax in ("x_m", "y_m", "z_m", "master_z_m") else 0.05
            if abs(float(v) - cur) < tol:
                self.sets.append((m, v))
                return                       # ignored, as observed
        super().set_value(m, v)

    def move_stage(self, x, y, z, t, r, m):
        self.moves.append((x, y, z, t, r, m))
        self._pending = StagePosition(x, y, z, t, r, m)
        self._countdown = self._idle_polls

    def get_float(self, mn):
        if mn == "AP_STAGE_AT_T":
            if not self.tilt_readable:
                raise RuntimeError("AP_STAGE_AT_T unreadable")
            return self.pos.tilt_deg
        return 0.0


def _sem_ms(stage):
    sem = _sem(stage)
    sem.move_stage = stage.move_stage
    sem.get_float = stage.get_float
    return sem


def _no_sleep(fn):
    import time as _t
    s = _t.sleep
    try:
        _t.sleep = lambda x: None
        return fn()
    finally:
        _t.sleep = s


def _rec_dx(dx_m, t=None):
    return PositionRecord(name="s1_dx", captured_unix_s=1.0, fib_magnification_x=2000.0,
                          fib_objective_potential_v=16400.0,
                          stage_at=StageAt(44.491e-3 + dx_m, 57.380e-3, 31.5e-3, 12.0,
                                           12.99e-3, t))


def test_default_recall_is_movestage_everywhere():
    import inspect
    from .. import batch, cli
    assert inspect.signature(recall_position).parameters["move_method"].default == "movestage"
    assert inspect.signature(batch.mill_positions_sequential).parameters[
        "move_method"].default == "movestage"
    p = cli.build_parser()
    for argv in (["goto-position", "p1"], ["goto-and-mill", "p1", "x.ely"],
                 ["mill-positions", "x.ely"]):
        assert p.parse_args(argv).move_method == "movestage", argv


def test_small_recall_moves_with_default_but_not_with_ap_goto():
    # The 2026-10-01 finding, reproduced: ap_goto leaves a 14 um recall unmoved and the
    # loose arrival check passes it; the default (MoveStage) actually moves.
    st = _DeadbandStage()
    _no_sleep(lambda: recall_position(_sem_ms(st), _rec_dx(14e-6), move_method="ap_goto",
                                      set_objective=False, wait_stage_idle=True,
                                      stage_timeout_s=5.0))
    assert abs(st.get_stage_position().x_m - 44.491e-3) < 1e-12      # did NOT move
    st = _DeadbandStage()
    _no_sleep(lambda: recall_position(_sem_ms(st), _rec_dx(14e-6), set_objective=False,
                                      wait_stage_idle=True, stage_timeout_s=5.0))
    assert len(st.moves) == 1
    assert st.moves[0][0] == 44.491e-3 + 14e-6       # MoveStage commanded the 14 um
    # NOTE (bug list #4): the 0.3 mm arrival check can return before a 14 um move has
    # finished -- the lazy stage here has not caught up yet. A few polls later it has.
    for _ in range(5):
        st.get_stage_position()
    assert abs(st.get_stage_position().x_m - (44.491e-3 + 14e-6)) < 1e-12


def test_movestage_holds_current_tilt_when_none_recorded():
    st = _DeadbandStage(tilt=12.0)
    _no_sleep(lambda: recall_position(_sem_ms(st), _rec_dx(5e-3), set_objective=False,
                                      wait_stage_idle=True, stage_timeout_s=5.0))
    assert st.moves[0][3] == 12.0                 # tilt unchanged
    st = _DeadbandStage(tilt=12.0)
    _no_sleep(lambda: recall_position(_sem_ms(st), _rec_dx(5e-3, t=10.0), set_objective=False,
                                      wait_stage_idle=True, stage_timeout_s=5.0))
    assert st.moves[0][3] == 10.0                 # a recorded tilt is used (bug list #2)


def test_movestage_refuses_when_tilt_unknown():
    # The old fallback commanded tilt 0.0 if AP_STAGE_AT_T could not be read -- a swing
    # from the milling tilt to 0 deg during a recall. Now it refuses and moves nothing.
    st = _DeadbandStage(tilt_readable=False)
    try:
        _no_sleep(lambda: recall_position(_sem_ms(st), _rec_dx(5e-3), set_objective=False,
                                          wait_stage_idle=True, stage_timeout_s=5.0))
    except SmartSEMError as e:
        assert "tilt" in str(e)
    else:
        raise AssertionError("recall moved without knowing the tilt")
    assert st.moves == []


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
