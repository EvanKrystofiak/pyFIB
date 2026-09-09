"""Offline tests for Prep CryoFIBSEM.

Focus: DRY-RUN safety (touches nothing), the exact step order, the stage axis
choreography (Z-first to CLEM SAFE; M-first/Z-last to Cryo Insertion, with M moves
allowed), and that a failed EHT-off aborts before valves/stage.
"""

from __future__ import annotations

from ..scope import SimulatedPrepSem
from ..steps import (run_prep_cryo, CLEM_SAFE_NAME, CRYO_INSERTION_NAME,
                     SEM_TARGET_KV_V, SEM_TARGET_CURRENT_A)


class _FakeClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _db(clem_z=0.032, clem_m=0.5e-3, cryo_z=0.025, cryo_m=2e-3):
    from ...positions import PositionDatabase, PositionRecord, StageAt

    def rec(name, x, y, z, m):
        return PositionRecord(name=name, captured_unix_s=1.0,
                              fib_magnification_x=2000.0,
                              fib_objective_potential_v=16400.0,
                              stage_at=StageAt(x, y, z, 0.0, m, 0.0))
    db = PositionDatabase()
    db.positions[CLEM_SAFE_NAME] = rec(CLEM_SAFE_NAME, 1e-3, 2e-3, clem_z, clem_m)
    db.positions[CRYO_INSERTION_NAME] = rec(CRYO_INSERTION_NAME, 5e-3, 6e-3, cryo_z, cryo_m)
    return db


class _RecordingSem(SimulatedPrepSem):
    """Records every move_stage tuple so we can inspect the axis choreography."""
    def __init__(self, **k):
        super().__init__(**k)
        self.moves = []

    def move_stage(self, x, y, z, t, r, m):
        self.moves.append({"x": x, "y": y, "z": z, "t": t, "r": r, "m": m})
        super().move_stage(x, y, z, t, r, m)


def _changed_axes(a, b, tol=1e-12):
    return {k for k in a if abs(a[k] - b[k]) > tol}


# ------------------------------- DRY-RUN SAFETY ------------------------------- #
def test_dry_run_touches_nothing():
    sem = _RecordingSem()
    before_stage = dict(sem.stage)
    res = run_prep_cryo(sem, _db(), armed=False, logger=lambda m: None,
                        clock_sleep=lambda s: None, clock_now=lambda: 0.0)
    assert res.ok
    assert sem.commands == [] and sem.set_values == {}
    assert sem.moves == [] and sem.stage == before_stage


# ------------------------------- ORDER + STATE ------------------------------- #
def test_armed_command_order_and_states():
    sem = _RecordingSem(eht_v=2000.0, column_valve=1, fib_valve=1)
    clk = _FakeClock()
    res = run_prep_cryo(sem, _db(), armed=True, logger=lambda m: None,
                        clock_sleep=clk.sleep, clock_now=clk.now)
    assert res.ok
    assert sem.commands == ["CMD_EHT_OFF", "CMD_CLOSE_COL_CHAMBER_VALVE",
                            "CMD_FIB_GUN_VALVE_CLOSE"]
    assert sem.eht_v == 0.0 and sem.column_valve == 0 and sem.fib_valve == 0
    assert sem.set_values["AP_MANUALKV"] == SEM_TARGET_KV_V
    assert sem.set_values["AP_IPROBE"] == SEM_TARGET_CURRENT_A


def test_ends_at_cryo_insertion_with_m_moved():
    sem = _RecordingSem()
    clk = _FakeClock()
    run_prep_cryo(sem, _db(cryo_m=2e-3), armed=True, logger=lambda m: None,
                  clock_sleep=clk.sleep, clock_now=clk.now)
    assert abs(sem.stage["x"] - 5e-3) < 1e-12
    assert abs(sem.stage["z"] - 0.025) < 1e-12
    assert abs(sem.stage["m"] - 2e-3) < 1e-12          # M moves ARE allowed


# --------------------------- STAGE AXIS CHOREOGRAPHY --------------------------- #
def test_axis_ordering_clem_then_cryo():
    sem = _RecordingSem()
    clk = _FakeClock()
    run_prep_cryo(sem, _db(), armed=True, logger=lambda m: None,
                  clock_sleep=clk.sleep, clock_now=clk.now)
    # 6 phases: CLEM (z, xyrt, m) then Cryo (m, xyrt, z)
    assert len(sem.moves) == 6, len(sem.moves)
    start = {"x": 0.0, "y": 0.0, "z": 0.030, "t": 0.0, "r": 0.0, "m": 0.0}
    seq = [start] + sem.moves
    changed = [_changed_axes(seq[i], seq[i + 1]) for i in range(6)]
    # CLEM SAFE: Z first, then X/Y/R/T, then M
    assert changed[0] == {"z"}, changed[0]
    assert changed[1] <= {"x", "y", "r", "t"} and "z" not in changed[1] and "m" not in changed[1]
    assert changed[2] == {"m"}, changed[2]
    # Cryo Insertion: M first, then X/Y/R/T, then Z
    assert changed[3] == {"m"}, changed[3]
    assert changed[4] <= {"x", "y", "r", "t"} and "z" not in changed[4] and "m" not in changed[4]
    assert changed[5] == {"z"}, changed[5]


# ------------------------------- ABORT PATHS ------------------------------- #
class _StuckEhtSem(_RecordingSem):
    """CMD_EHT_OFF does not actually turn the beam off (models a stuck EHT)."""
    def execute(self, c):
        self.commands.append(c)
        if c == "CMD_CLOSE_COL_CHAMBER_VALVE":
            self.column_valve = 0
        elif c == "CMD_FIB_GUN_VALVE_CLOSE":
            self.fib_valve_open = False
        # deliberately NOT handling CMD_EHT_OFF -> eht_v stays high


def test_aborts_when_eht_never_off_before_valves_or_stage():
    sem = _StuckEhtSem(eht_v=2000.0, column_valve=1)
    clk = _FakeClock()
    res = run_prep_cryo(sem, _db(), armed=True, logger=lambda m: None,
                        clock_sleep=clk.sleep, clock_now=clk.now)
    assert not res.ok and "EHT did not turn off" in res.reason
    # column valve NOT closed, no FIB valve command, no stage move
    assert "CMD_CLOSE_COL_CHAMBER_VALVE" not in sem.commands
    assert "CMD_FIB_GUN_VALVE_CLOSE" not in sem.commands
    assert sem.moves == []


def test_missing_waypoint_refuses():
    from ...positions import PositionDatabase
    res = run_prep_cryo(SimulatedPrepSem(), PositionDatabase(), armed=True,
                        logger=lambda m: None,
                        clock_sleep=lambda s: None, clock_now=lambda: 0.0)
    assert not res.ok and "CLEM SAFE" in res.reason


# ------------------------------- FIB VALVE LOGIC ------------------------------- #
def test_fib_valve_already_closed_no_command():
    sem = _RecordingSem(fib_valve=0)
    clk = _FakeClock()
    res = run_prep_cryo(sem, _db(), armed=True, logger=lambda m: None,
                        clock_sleep=clk.sleep, clock_now=clk.now)
    assert res.ok and "CMD_FIB_GUN_VALVE_CLOSE" not in sem.commands
    assert "fib_valve_already_closed" in res.steps_done


def test_fib_valve_unreadable_issues_close_anyway():
    class _NoValveState(_RecordingSem):
        def get_float(self, m):
            if m == "DP_FIB_GUN_VALVE":
                raise KeyError(m)          # state unreadable
            return super().get_float(m)
    sem = _NoValveState()
    clk = _FakeClock()
    res = run_prep_cryo(sem, _db(), armed=True, logger=lambda m: None,
                        clock_sleep=clk.sleep, clock_now=clk.now)
    assert res.ok and "CMD_FIB_GUN_VALVE_CLOSE" in sem.commands
    assert "fib_valve_close_unverified" in res.steps_done


def test_fib_valve_transit_gets_closed():
    """A transit/unknown valve state (not confirmed closed) must be closed."""
    sem = _RecordingSem(fib_valve=3)   # transit
    clk = _FakeClock()
    res = run_prep_cryo(sem, _db(), armed=True, logger=lambda m: None,
                        clock_sleep=clk.sleep, clock_now=clk.now)
    assert res.ok and "CMD_FIB_GUN_VALVE_CLOSE" in sem.commands
    assert "fib_valve_closed" in res.steps_done and sem.fib_valve == 0


class _LazyStage(_RecordingSem):
    """Reports Idle for the first N polls AFTER a move is commanded, and only THEN
    reflects the new position -- reproducing the real premature-Idle-before-motion."""
    def __init__(self, idle_polls=3, **k):
        super().__init__(**k)
        self._idle_polls = int(idle_polls)
        self._pending = None
        self._countdown = 0

    def move_stage(self, x, y, z, t, r, m):
        # record the command but do NOT apply the position yet (stage still idle-
        # reading but not actually there)
        self.moves.append({"x": x, "y": y, "z": z, "t": t, "r": r, "m": m})
        self._pending = (x, y, z, t, r, m)
        self._countdown = self._idle_polls

    def get_stage_position(self):
        # apply the pending move only after the idle window elapses
        if self._pending is not None:
            if self._countdown > 0:
                self._countdown -= 1
            else:
                super().move_stage(*self._pending)
                self._pending = None
        return super().get_stage_position()


def test_wait_requires_arrival_not_just_idle():
    """REGRESSION: the stage reads Idle before it moves; the wait must hold until the
    read-back position actually reaches each phase target, so no phase is skipped."""
    sem = _LazyStage(idle_polls=3)
    clk = _FakeClock()
    res = run_prep_cryo(sem, _db(), armed=True, logger=lambda m: None,
                        clock_sleep=clk.sleep, clock_now=clk.now)
    assert res.ok, res.reason              # would FAIL (arrival off) under idle-only
    assert "at_clem_safe" in res.steps_done and "at_cryo_insertion" in res.steps_done


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
