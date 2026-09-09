"""Move to a SETUP position, always via CLEM SAFE (collision-safe middle point).

These setup fixtures (CLEM SAFE, Cryo Insertion, Cryo Removal, Grid* ...) can be far
apart and involve tilt/Z changes, so every move goes VIA CLEM SAFE first:

    to CLEM SAFE   -> Z, then X/Y/T/R, then M
    to destination -> M, then X/Y/T/R, then Z

If the stage is already very close to CLEM SAFE (within 0.3 mm on X/Y/Z and EXACT on
T/R/M), the CLEM SAFE pass is skipped. Each waypoint is CONFIRMED on arrival (axes
read back within tolerance) before proceeding -- not just "stage idle".

This is deliberately SEPARATE from milling recall (goto-position), which does NOT
detour via CLEM SAFE because those recalls don't change tilt/Z dangerously.

Dry-run unless armed. The stage is the only thing this touches.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

# CLEM-SAFE proximity to SKIP the safe-middle pass.
CLEM_NEAR_LINEAR_M = 0.3e-3     # 0.3 mm on X, Y, Z
CLEM_EXACT_LINEAR_M = 1.0e-6    # "exact" M: 1 um float/readback epsilon
CLEM_EXACT_ANGULAR_DEG = 1.0e-3  # "exact" T, R: 0.001 deg epsilon

# Arrival confirmation tolerance (after a phased move, per axis).
ARRIVE_LINEAR_M = 0.3e-3        # 0.3 mm on X, Y, Z, M
ARRIVE_ANGULAR_DEG = 0.05      # 0.05 deg on T, R

STAGE_IDLE_TIMEOUT_S = 180.0
STAGE_POLL_S = 0.25

CLEM_SAFE_NAME = "CLEM SAFE"

# to CLEM SAFE: Z, then X/Y/T/R, then M
PHASES_TO_CLEM = [("z",), ("x", "y", "t", "r"), ("m",)]
# to destination: M, then X/Y/T/R, then Z
PHASES_TO_DEST = [("m",), ("x", "y", "t", "r"), ("z",)]

_LINEAR = {"x", "y", "z", "m"}


@dataclass
class MoveResult:
    ok: bool
    reason: str = ""
    steps: List[str] = field(default_factory=list)
    skipped_clem: bool = False


def _target_from_record(rec):
    st = rec.stage_at
    return {"x": st.x_m, "y": st.y_m, "z": st.z_m,
            "t": st.t_deg, "r": st.r_deg, "m": st.m_m}


def _read_stage(scope) -> Optional[dict]:
    keys = {"x": "AP_STAGE_AT_X", "y": "AP_STAGE_AT_Y", "z": "AP_STAGE_AT_Z",
            "t": "AP_STAGE_AT_T", "r": "AP_STAGE_AT_R", "m": "AP_STAGE_AT_M"}
    out = {}
    for ax, mn in keys.items():
        v = scope.get_float(mn)
        if v is None:
            return None
        out[ax] = v
    return out


def _near_clem(cur, clem) -> bool:
    """True if within 0.3 mm on X/Y/Z and EXACT (epsilon) on T/R/M."""
    for ax in ("x", "y", "z"):
        if abs(cur[ax] - clem[ax]) > CLEM_NEAR_LINEAR_M:
            return False
    if abs(cur["m"] - clem["m"]) > CLEM_EXACT_LINEAR_M:
        return False
    for ax in ("t", "r"):
        cv, tv = cur.get(ax), clem.get(ax)
        if cv is None or tv is None:
            return False
        if abs(cv - tv) > CLEM_EXACT_ANGULAR_DEG:
            return False
    return True


def _arrived(cur, target) -> Optional[str]:
    """None if every axis is within arrival tolerance, else a description of the miss."""
    for ax, tv in target.items():
        if tv is None:
            continue
        tol = ARRIVE_LINEAR_M if ax in _LINEAR else ARRIVE_ANGULAR_DEG
        if abs(cur[ax] - tv) > tol:
            unit = "mm" if ax in _LINEAR else "deg"
            scale = 1e3 if ax in _LINEAR else 1.0
            return (f"{ax.upper()} off by {abs(cur[ax] - tv) * scale:.4f} {unit} "
                    f"(tol {tol * scale:g})")
    return None


def _wait_idle(scope, log, sleep, now) -> bool:
    if not scope.armed:
        return True
    t0 = now()
    while now() - t0 < STAGE_IDLE_TIMEOUT_S:
        v = scope.get_float("DP_STAGE_IS")
        if v is not None and int(round(v)) == 0:
            return True
        sleep(STAGE_POLL_S)
    return False


def _wait_move_complete(scope, dest, log, sleep, now) -> bool:
    """Block until the stage is idle AND actually at ``dest`` (within arrival tol).

    MoveStage is non-blocking and DP_STAGE_IS can read Idle *before* the stage starts
    moving, so idle alone is not enough -- also require the read-back position to
    reach the target, or we proceed mid-travel (the bug prep_cryo hit).
    """
    if not scope.armed:
        return True
    t0 = now()
    while now() - t0 < STAGE_IDLE_TIMEOUT_S:
        v = scope.get_float("DP_STAGE_IS")
        idle = v is not None and int(round(v)) == 0
        pos = _read_stage(scope)
        if idle and pos is not None and _arrived(pos, dest) is None:
            return True
        sleep(STAGE_POLL_S)
    return False


def _move_axes(scope, current, target, axes, *, note, log, sleep, now):
    """Move only ``axes`` to target, holding the rest at ``current``. Returns new
    current (the commanded destination) or None on failure."""
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


def _phased_move(scope, current, target, phases, *, label, log, sleep, now):
    log(f"  Move to {label} (axis order: {' -> '.join('/'.join(p) for p in phases)})")
    for phase in phases:
        current = _move_axes(scope, current, target, phase,
                             note=f"{label}: {'/'.join(phase)}", log=log,
                             sleep=sleep, now=now)
        if current is None:
            return None
    return current


def _confirm_arrival(scope, target, *, label, log) -> bool:
    """Read the stage back and confirm arrival within tolerance (armed only)."""
    if not scope.armed:
        log(f"    [dry-run] would confirm arrival at {label}")
        return True
    cur = _read_stage(scope)
    if cur is None:
        log(f"    ERROR: could not read stage to confirm {label}.")
        return False
    miss = _arrived(cur, target)
    if miss is not None:
        log(f"    ERROR: not at {label}: {miss}.")
        return False
    log(f"    confirmed at {label}.")
    return True


def move_to_setup_position(scope, db, dest_name, *,
                           logger: Optional[Callable[[str], None]] = None,
                           clock_sleep=time.sleep, clock_now=time.monotonic
                           ) -> MoveResult:
    """Move to ``dest_name`` via CLEM SAFE, with per-waypoint arrival confirmation."""
    log = logger or (lambda m: None)
    steps: List[str] = []

    if CLEM_SAFE_NAME not in db.positions:
        return MoveResult(False, f"'{CLEM_SAFE_NAME}' not found in setup positions.")
    if dest_name not in db.positions:
        return MoveResult(False, f"'{dest_name}' not found in setup positions.")
    clem = _target_from_record(db.positions[CLEM_SAFE_NAME])
    dest = _target_from_record(db.positions[dest_name])

    mode = "ARMED" if scope.armed else "DRY-RUN (nothing will move)"
    log(f"=== Move to '{dest_name}' via CLEM SAFE [{mode}] ===")

    cur = _read_stage(scope)
    if cur is None:
        if scope.armed:
            return MoveResult(False, "could not read current stage position.")
        cur = dict(clem)  # dry-run: assume at CLEM for a clean plan

    # 1) CLEM SAFE pass (unless already very close)
    if _near_clem(cur, clem):
        log("  Already close to CLEM SAFE (within 0.3 mm X/Y/Z, exact T/R/M) -- "
            "skipping the safe-middle pass.")
        skipped = True
    else:
        cur = _phased_move(scope, cur, clem, PHASES_TO_CLEM,
                           label=CLEM_SAFE_NAME, log=log,
                           sleep=clock_sleep, now=clock_now)
        if cur is None:
            return MoveResult(False, "move to CLEM SAFE failed.", steps=steps)
        if not _confirm_arrival(scope, clem, label=CLEM_SAFE_NAME, log=log):
            return MoveResult(False, "CLEM SAFE arrival not confirmed.", steps=steps)
        cur = clem
        steps.append("at_clem_safe")
        skipped = False

    # 2) destination
    if dest_name == CLEM_SAFE_NAME:
        # Destination IS CLEM SAFE: the pass above already put us there.
        if skipped:
            log("  Destination is CLEM SAFE and we are already there.")
        steps.append("at_destination")
        log("=== Move complete ===")
        return MoveResult(True, "", steps=steps, skipped_clem=skipped)

    cur = _phased_move(scope, cur, dest, PHASES_TO_DEST,
                       label=dest_name, log=log, sleep=clock_sleep, now=clock_now)
    if cur is None:
        return MoveResult(False, f"move to {dest_name} failed.", steps=steps)
    if not _confirm_arrival(scope, dest, label=dest_name, log=log):
        return MoveResult(False, f"{dest_name} arrival not confirmed.", steps=steps)
    steps.append("at_destination")
    log("=== Move complete ===")
    return MoveResult(True, "", steps=steps, skipped_clem=skipped)
