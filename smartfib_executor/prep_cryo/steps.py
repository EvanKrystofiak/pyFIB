"""Prep CryoFIBSEM: make the FIB ready for sample insertion.

Sequence (in order):
  1. Turn SEM EHT off; WAIT until EHT is off; then close the column chamber valve.
  2. Close the FIB gun valve IF it is open (do NOT turn the FIB off).
  3. Set SEM accelerating voltage = 2.00 keV and probe current = 20 pA.
  4. Move the stage to "Cryo Insertion" VIA "CLEM SAFE":
       to CLEM SAFE  -> Z first, then X/Y/R/T, then M   (CLEM Z-first convention)
       to Cryo Insertion -> M first, then X/Y/R/T, then Z
     (like gisdep, but M moves are allowed here). Each phase waits for the stage to
     go idle before the next.
  5. Read the FIB probe and report it.

Everything is dry-run unless armed. The stage choreography is the highest-risk part;
it runs only when armed AND after both named positions are confirmed present with full
coordinates. If EHT never confirms off, the sequence ABORTS before touching valves or
the stage.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .scope import PrepScope

# Targets / names
SEM_TARGET_KV_V = 2000.0        # 2.00 keV; AP_MANUALKV uses Zeiss scientific numbering
                               # (2000 V shows as 2.00e+03) -- CONFIRMED.
SEM_TARGET_CURRENT_A = 20.0e-12  # 20 pA; AP_IPROBE takes the NUMBER (it also has a
                                 # "20 pA" text form, but we set the number) -- CONFIRMED.
CLEM_SAFE_NAME = "CLEM SAFE"
CRYO_INSERTION_NAME = "Cryo Insertion"

# EHT-off confirmation
EHT_OFF_THRESHOLD_V = 50.0
EHT_OFF_TIMEOUT_S = 120.0
EHT_POLL_S = 0.5

# Stage
STAGE_IDLE_TIMEOUT_S = 180.0
STAGE_POLL_S = 0.25

# Valve states (CONFIRMED on the instrument, like the other valve reads):
#   0 = Closed, 1 = Open, and (like the GIS valves) a transit/unknown value exists
#   (e.g. 3) that is hard to catch. We therefore CLOSE unless the read is confirmed
#   0 (closed) -- so open OR transit both get closed.
VALVE_CLOSED = 0


@dataclass
class PrepResult:
    ok: bool
    reason: str = ""
    steps_done: List[str] = field(default_factory=list)
    fib_probe_idx: Optional[int] = None
    fib_probe_a: Optional[float] = None


def _wait(scope: PrepScope, pred, *, timeout_s, poll_s, clock_sleep, clock_now):
    t0 = clock_now()
    while clock_now() - t0 < timeout_s:
        if pred():
            return True
        clock_sleep(poll_s)
    return False


def _wait_eht_off(scope: PrepScope, log, sleep, now) -> bool:
    if not scope.armed:
        log("    [dry-run] would wait for AP_ACTUALKV to fall below "
            f"{EHT_OFF_THRESHOLD_V:.0f} V")
        return True

    def off():
        kv = scope.get_float("AP_ACTUALKV")
        return kv is not None and abs(kv) <= EHT_OFF_THRESHOLD_V

    return _wait(scope, off, timeout_s=EHT_OFF_TIMEOUT_S, poll_s=EHT_POLL_S,
                 clock_sleep=sleep, clock_now=now)


def _wait_stage_idle(scope: PrepScope, log, sleep, now) -> bool:
    if not scope.armed:
        return True

    def idle():
        v = scope.get_float("DP_STAGE_IS")
        return v is not None and int(round(v)) == 0

    return _wait(scope, idle, timeout_s=STAGE_IDLE_TIMEOUT_S, poll_s=STAGE_POLL_S,
                 clock_sleep=sleep, clock_now=now)


def _at(pos, dest) -> bool:
    """True if the read-back position matches the commanded dest within tolerance."""
    for ax in ("x", "y", "z", "m"):
        if abs(pos[ax] - dest[ax]) > ARRIVE_LINEAR_M:
            return False
    for ax in ("t", "r"):
        if abs(pos[ax] - dest[ax]) > ARRIVE_ANGULAR_DEG:
            return False
    return True


def _wait_move_complete(scope: PrepScope, dest, log, sleep, now) -> bool:
    """Block until the stage is idle AND actually at ``dest``.

    Critical: MoveStage is non-blocking and DP_STAGE_IS can read Idle *before* the
    stage starts moving. Requiring the read-back position to reach the target (not
    just Idle) prevents proceeding mid-travel -- the working gisdep convention.
    """
    if not scope.armed:
        return True
    t0 = now()
    while now() - t0 < STAGE_IDLE_TIMEOUT_S:
        v = scope.get_float("DP_STAGE_IS")
        idle = v is not None and int(round(v)) == 0
        pos = _read_stage(scope)
        if idle and pos is not None and _at(pos, dest):
            return True
        sleep(STAGE_POLL_S)
    return False


def _read_stage(scope: PrepScope):
    """Current (x,y,z,t,r,m) in metres/deg, or None if unreadable."""
    keys = ["AP_STAGE_AT_X", "AP_STAGE_AT_Y", "AP_STAGE_AT_Z",
            "AP_STAGE_AT_T", "AP_STAGE_AT_R", "AP_STAGE_AT_M"]
    vals = [scope.get_float(k) for k in keys]
    if any(v is None for v in vals):
        return None
    return dict(zip("xyztrm", vals))


# Arrival confirmation tolerance (per axis) after a phased move.
ARRIVE_LINEAR_M = 0.3e-3        # 0.3 mm on X, Y, Z, M
ARRIVE_ANGULAR_DEG = 0.05      # 0.05 deg on T, R
_LINEAR_AXES = {"x", "y", "z", "m"}


def _confirm_arrival(scope: PrepScope, target, *, label, log) -> bool:
    """Read the stage back and confirm arrival within tolerance (armed only)."""
    if not scope.armed:
        log(f"    [dry-run] would confirm arrival at {label}")
        return True
    cur = _read_stage(scope)
    if cur is None:
        log(f"    ERROR: could not read stage to confirm {label}.")
        return False
    for ax, tv in target.items():
        if tv is None:
            continue
        tol = ARRIVE_LINEAR_M if ax in _LINEAR_AXES else ARRIVE_ANGULAR_DEG
        if abs(cur[ax] - tv) > tol:
            unit = "mm" if ax in _LINEAR_AXES else "deg"
            scale = 1e3 if ax in _LINEAR_AXES else 1.0
            log(f"    ERROR: not at {label}: {ax.upper()} off by "
                f"{abs(cur[ax] - tv) * scale:.4f} {unit} (tol {tol * scale:g}).")
            return False
    log(f"    confirmed at {label}.")
    return True


def _target_from_record(rec):
    """(x,y,z,t,r,m) target dict from a PositionRecord (t may be None)."""
    st = rec.stage_at
    return {"x": st.x_m, "y": st.y_m, "z": st.z_m,
            "t": st.t_deg, "r": st.r_deg, "m": st.m_m}


def _move_axes(scope: PrepScope, current, target, axes, *, note, log, sleep, now):
    """Move only ``axes`` to their target, holding the rest at ``current``.

    ``current`` is the position to hold non-moved axes at (threaded from the previous
    phase, so the plan is correct even in dry-run where nothing physically moves). A
    target axis of None (e.g. tilt not recorded) is held at current. Returns the new
    current position (the commanded destination) on success, or None on failure.
    """
    dest = dict(current)
    for ax in axes:
        tv = target.get(ax)
        if tv is not None:
            dest[ax] = tv
    scope.move_stage(dest["x"], dest["y"], dest["z"], dest["t"], dest["r"], dest["m"],
                     note=note)
    if not _wait_move_complete(scope, dest, log, sleep, now):
        log("    ERROR: stage did not reach the target within timeout.")
        return None
    return dest


def _phased_move(scope: PrepScope, current, target, phases, *, label, log,
                 sleep, now):
    """Run an ordered list of axis-group phases to reach ``target``.

    Returns the new current position on success, or None on failure.
    """
    log(f"  Move to {label} (axis order: {' -> '.join('/'.join(p) for p in phases)})")
    for phase in phases:
        current = _move_axes(scope, current, target, phase,
                             note=f"{label}: {'/'.join(phase)}", log=log,
                             sleep=sleep, now=now)
        if current is None:
            return None
    return current


def run_prep_cryo(sem, db, *, armed: bool = False,
                  logger: Optional[Callable[[str], None]] = None,
                  clock_sleep=time.sleep, clock_now=time.monotonic) -> PrepResult:
    """Run the full Prep CryoFIBSEM sequence. Dry-run unless armed."""
    log = logger or (lambda m: None)
    scope = PrepScope(sem, armed=armed, logger=log)
    done: List[str] = []

    mode = "ARMED" if armed else "DRY-RUN (nothing will actually change)"
    log(f"=== Prep CryoFIBSEM [{mode}] ===")

    # Both waypoints must exist with full coordinates before we touch anything that
    # would later move the stage.
    for name in (CLEM_SAFE_NAME, CRYO_INSERTION_NAME):
        if name not in db.positions:
            return PrepResult(False, f"required position '{name}' not found in the "
                                     f"positions file.")
    clem = _target_from_record(db.positions[CLEM_SAFE_NAME])
    cryo = _target_from_record(db.positions[CRYO_INSERTION_NAME])

    # 1) EHT off -> wait -> close column valve
    log("1) SEM EHT off")
    scope.execute("CMD_EHT_OFF")
    if not _wait_eht_off(scope, log, clock_sleep, clock_now):
        kv = scope.get_float("AP_ACTUALKV")
        return PrepResult(False, f"EHT did not turn off (AP_ACTUALKV={kv}); aborting "
                                 f"before valves/stage.", steps_done=done)
    done.append("eht_off")
    log("   EHT confirmed off. Closing column chamber valve.")
    scope.execute("CMD_CLOSE_COL_CHAMBER_VALVE")
    cv = scope.get_float("DP_COLUMN_CHAMBER_VALVE")
    if cv is not None:
        cvi = int(round(cv))
        log(f"   DP_COLUMN_CHAMBER_VALVE = {cvi} "
            f"({'closed' if cvi == VALVE_CLOSED else 'open' if cvi == 1 else 'transit/unknown'}).")
    done.append("column_valve_closed")

    # 2) close FIB gun valve UNLESS it is confirmed closed (do not turn the FIB off).
    #    DP_FIB_GUN_VALVE: 0=Closed, 1=Open, transit/unknown otherwise. CLOSE via
    #    CMD_FIB_GUN_VALVE_CLOSE (CMD_FIB_GUN_VALVE_OPEN would OPEN it -- never used).
    log("2) FIB gun valve")
    vstate = scope.get_float("DP_FIB_GUN_VALVE")
    if vstate is None:
        log("   NOTE: DP_FIB_GUN_VALVE not readable; issuing close anyway "
            "(harmless if already closed).")
        scope.execute("CMD_FIB_GUN_VALVE_CLOSE")
        done.append("fib_valve_close_unverified")
    else:
        istate = int(round(vstate))
        if istate == VALVE_CLOSED:
            log(f"   DP_FIB_GUN_VALVE = {istate} (closed) -> nothing to do.")
            done.append("fib_valve_already_closed")
        else:
            label = "open" if istate == 1 else f"transit/unknown ({istate})"
            log(f"   DP_FIB_GUN_VALVE = {istate} ({label}) -> closing "
                f"(FIB gun left ON).")
            scope.execute("CMD_FIB_GUN_VALVE_CLOSE")
            done.append("fib_valve_closed")

    # 3) SEM 2.00 keV / 20 pA
    log("3) SEM accelerating voltage 2.00 keV, probe current 20 pA")
    scope.set_value("AP_MANUALKV", SEM_TARGET_KV_V)
    scope.set_value("AP_IPROBE", SEM_TARGET_CURRENT_A)
    done.append("sem_kv_current_set")

    # 4) stage: to CLEM SAFE (Z, xyrt, m) then to Cryo Insertion (m, xyrt, z),
    #    confirming arrival at each waypoint (CLEM SAFE is at T=0, which is the state
    #    prep_cryo needs -- confirming arrival guarantees it before we insert).
    log("4) Stage: to CLEM SAFE, then to Cryo Insertion")
    cur = _read_stage(scope)
    if cur is None:
        if armed:
            return PrepResult(False, "could not read current stage position; "
                                     "aborting before any stage move.", steps_done=done)
        cur = {"x": 0.0, "y": 0.0, "z": 0.0, "t": 0.0, "r": 0.0, "m": 0.0}
    cur = _phased_move(scope, cur, clem,
                       [("z",), ("x", "y", "r", "t"), ("m",)],
                       label=CLEM_SAFE_NAME, log=log,
                       sleep=clock_sleep, now=clock_now)
    if cur is None:
        return PrepResult(False, "stage move to CLEM SAFE failed.", steps_done=done)
    if not _confirm_arrival(scope, clem, label=CLEM_SAFE_NAME, log=log):
        return PrepResult(False, "CLEM SAFE arrival not confirmed (tilt/position not "
                                 "as expected); aborting before Cryo Insertion.",
                          steps_done=done)
    cur = clem
    done.append("at_clem_safe")
    cur = _phased_move(scope, cur, cryo,
                       [("m",), ("x", "y", "r", "t"), ("z",)],
                       label=CRYO_INSERTION_NAME, log=log,
                       sleep=clock_sleep, now=clock_now)
    if cur is None:
        return PrepResult(False, "stage move to Cryo Insertion failed.",
                          steps_done=done)
    if not _confirm_arrival(scope, cryo, label=CRYO_INSERTION_NAME, log=log):
        return PrepResult(False, "Cryo Insertion arrival not confirmed.",
                          steps_done=done)
    done.append("at_cryo_insertion")

    # 5) report FIB probe
    log("5) FIB probe")
    idx = scope.get_float("DP_FIB_PROBE")
    amps = scope.get_float("AP_FIB_PROBE_CURRENT")
    idx_i = int(round(idx)) if idx is not None else None
    log(f"   FIB probe: DP_FIB_PROBE={idx_i}"
        + (f", AP_FIB_PROBE_CURRENT={amps * 1e12:.1f} pA" if amps is not None else
           ", AP_FIB_PROBE_CURRENT=n/a"))
    done.append("fib_probe_reported")

    log("=== Prep CryoFIBSEM complete ===")
    return PrepResult(True, "", steps_done=done, fib_probe_idx=idx_i,
                      fib_probe_a=amps)
