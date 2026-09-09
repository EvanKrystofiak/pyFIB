"""Offline tests for setup moves (via CLEM SAFE)."""

from __future__ import annotations

from ..scope import MoveScope
from ..steps import (move_to_setup_position, CLEM_SAFE_NAME,
                     CLEM_NEAR_LINEAR_M, ARRIVE_LINEAR_M)


class _FakeClock:
    def __init__(self): self.t = 0.0
    def now(self): return self.t
    def sleep(self, s): self.t += s


class _SimStage:
    """Stage snaps to each commanded move; records moves; always idle."""
    def __init__(self, start):
        self.stage = dict(start)
        self.moves = []

    def get_float(self, m):
        if m == "DP_STAGE_IS":
            return 0.0
        if m.startswith("AP_STAGE_AT_"):
            return self.stage[m.split("_")[-1].lower()]
        raise KeyError(m)

    def move_stage(self, x, y, z, t, r, m):
        self.moves.append({"x": x, "y": y, "z": z, "t": t, "r": r, "m": m})
        self.stage.update({"x": x, "y": y, "z": z, "t": t, "r": r, "m": m})


class _StuckStage(_SimStage):
    """Commands are accepted but the stage never actually reaches the target."""
    def move_stage(self, x, y, z, t, r, m):
        self.moves.append({"x": x, "y": y, "z": z, "t": t, "r": r, "m": m})
        # deliberately do NOT update self.stage -> arrival can't confirm


def _db():
    from ...positions import PositionDatabase, PositionRecord, StageAt

    def rec(name, x, y, z, t, r, m):
        return PositionRecord(name=name, captured_unix_s=0.0,
                              fib_magnification_x=2000.0,
                              fib_objective_potential_v=0.0,
                              stage_at=StageAt(x, y, z, r, m, t))
    db = PositionDatabase()
    # CLEM SAFE and a far destination (values in metres)
    db.positions[CLEM_SAFE_NAME] = rec(CLEM_SAFE_NAME, 40.561e-3, 57.164e-3, 23.0e-3,
                                       0.0, 12.0, 12.99e-3)
    db.positions["Cryo Insertion"] = rec("Cryo Insertion", 43.413e-3, 49.754e-3,
                                         28.312e-3, 0.0, 352.389, 12.90e-3)
    return db


def _clem_dict(db):
    st = db.positions[CLEM_SAFE_NAME].stage_at
    return {"x": st.x_m, "y": st.y_m, "z": st.z_m, "t": st.t_deg,
            "r": st.r_deg, "m": st.m_m}


def _changed(a, b, tol=1e-12):
    return {k for k in a if abs(a[k] - b[k]) > tol}


# ------------------------------- CHOREOGRAPHY ------------------------------- #
def test_far_start_goes_via_clem_with_correct_axis_order():
    db = _db()
    start = {"x": 0.0, "y": 0.0, "z": 0.010, "t": 0.0, "r": 0.0, "m": 0.0}
    sem = _SimStage(start)
    clk = _FakeClock()
    res = move_to_setup_position(MoveScope(sem, armed=True), db, "Cryo Insertion",
                                 logger=lambda m: None,
                                 clock_sleep=clk.sleep, clock_now=clk.now)
    assert res.ok and not res.skipped_clem
    assert len(sem.moves) == 6, len(sem.moves)
    seq = [start] + sem.moves
    ch = [_changed(seq[i], seq[i + 1]) for i in range(6)]
    # to CLEM SAFE: Z, then X/Y/T/R, then M
    assert ch[0] == {"z"}
    assert ch[1] <= {"x", "y", "t", "r"} and "z" not in ch[1] and "m" not in ch[1]
    assert ch[2] == {"m"}
    # to destination: M, then X/Y/T/R, then Z
    assert ch[3] == {"m"}
    assert ch[4] <= {"x", "y", "t", "r"} and "z" not in ch[4] and "m" not in ch[4]
    assert ch[5] == {"z"}


def test_ends_at_destination():
    db = _db()
    sem = _SimStage({"x": 0, "y": 0, "z": 0.010, "t": 0, "r": 0, "m": 0})
    clk = _FakeClock()
    move_to_setup_position(MoveScope(sem, armed=True), db, "Cryo Insertion",
                           logger=lambda m: None, clock_sleep=clk.sleep,
                           clock_now=clk.now)
    d = db.positions["Cryo Insertion"].stage_at
    assert abs(sem.stage["x"] - d.x_m) < 1e-12 and abs(sem.stage["z"] - d.z_m) < 1e-12
    assert abs(sem.stage["m"] - d.m_m) < 1e-12


# --------------------------- SKIP-WHEN-NEAR-CLEM --------------------------- #
def test_skips_clem_when_already_close():
    db = _db()
    clem = _clem_dict(db)
    # within 0.3 mm on X/Y/Z, EXACT on T/R/M
    start = dict(clem)
    start["x"] += 0.2e-3; start["z"] -= 0.1e-3
    sem = _SimStage(start)
    clk = _FakeClock()
    res = move_to_setup_position(MoveScope(sem, armed=True), db, "Cryo Insertion",
                                 logger=lambda m: None, clock_sleep=clk.sleep,
                                 clock_now=clk.now)
    assert res.ok and res.skipped_clem
    assert len(sem.moves) == 3          # only the destination phases


def test_does_not_skip_when_m_not_exact():
    db = _db()
    clem = _clem_dict(db)
    start = dict(clem)
    start["m"] += 0.5e-3                 # M must be EXACT -> not "near"
    sem = _SimStage(start)
    clk = _FakeClock()
    res = move_to_setup_position(MoveScope(sem, armed=True), db, "Cryo Insertion",
                                 logger=lambda m: None, clock_sleep=clk.sleep,
                                 clock_now=clk.now)
    assert res.ok and not res.skipped_clem
    assert len(sem.moves) == 6          # full via-CLEM pass


def test_does_not_skip_when_xyz_beyond_tolerance():
    db = _db()
    clem = _clem_dict(db)
    start = dict(clem)
    start["x"] += CLEM_NEAR_LINEAR_M * 2   # 0.6 mm on X -> beyond 0.3 mm
    sem = _SimStage(start)
    clk = _FakeClock()
    res = move_to_setup_position(MoveScope(sem, armed=True), db, "Cryo Insertion",
                                 logger=lambda m: None, clock_sleep=clk.sleep,
                                 clock_now=clk.now)
    assert not res.skipped_clem and len(sem.moves) == 6


# ----------------------------- ARRIVAL CONFIRM ----------------------------- #
def test_refuses_when_arrival_not_confirmed():
    db = _db()
    sem = _StuckStage({"x": 0, "y": 0, "z": 0.010, "t": 0, "r": 0, "m": 0})
    clk = _FakeClock()
    res = move_to_setup_position(MoveScope(sem, armed=True), db, "Cryo Insertion",
                                 logger=lambda m: None, clock_sleep=clk.sleep,
                                 clock_now=clk.now)
    assert not res.ok and "CLEM SAFE" in res.reason


# ------------------------------- EDGE CASES ------------------------------- #
def test_destination_is_clem_safe():
    db = _db()
    sem = _SimStage({"x": 0, "y": 0, "z": 0.010, "t": 0, "r": 0, "m": 0})
    clk = _FakeClock()
    res = move_to_setup_position(MoveScope(sem, armed=True), db, CLEM_SAFE_NAME,
                                 logger=lambda m: None, clock_sleep=clk.sleep,
                                 clock_now=clk.now)
    assert res.ok and len(sem.moves) == 3   # just the CLEM SAFE pass


def test_missing_destination_refuses():
    res = move_to_setup_position(MoveScope(_SimStage({"x": 0, "y": 0, "z": 0, "t": 0,
                                                      "r": 0, "m": 0}), armed=True),
                                 _db(), "Nope", logger=lambda m: None,
                                 clock_sleep=lambda s: None, clock_now=lambda: 0.0)
    assert not res.ok and "Nope" in res.reason


def test_dry_run_moves_nothing():
    db = _db()
    sem = _SimStage({"x": 0, "y": 0, "z": 0.010, "t": 0, "r": 0, "m": 0})
    res = move_to_setup_position(MoveScope(sem, armed=False), db, "Cryo Insertion",
                                 logger=lambda m: None,
                                 clock_sleep=lambda s: None, clock_now=lambda: 0.0)
    assert res.ok and sem.moves == []


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
