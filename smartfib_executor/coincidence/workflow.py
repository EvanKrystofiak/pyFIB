"""SEM working-distance measurement + coincidence recommendation.

Flow (nothing here moves the stage):
    1. verify SEM beam is ON (AP_ACTUALKV) and stage tilt is ~0 -- refuse otherwise
    2. read current Z
    3. CMD_AUTO_FOCUS_COARSE, wait for DP_AUTO_FUNCTION to return to Idle
    4. CMD_AUTO_FOCUS_FINE,   wait for DP_AUTO_FUNCTION to return to Idle
    5. read AP_WD
    6. compute the RECOMMENDED Z move (limits.recommend_z_move) and report it

The operator makes the recommended move by hand, then re-runs to verify. At WD =
5 mm the FIB and SEM are coincident.

INSTRUMENT-UNTESTED points (flagged for the first real run):
    * that DP_AUTO_FUNCTION actually reads 1/Focus during CMD_AUTO_FOCUS_* and
      returns to 0/Idle when done, and that the commands settle before Idle;
    * generally, that CMD_AUTO_FOCUS_COARSE/FINE behave as assumed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

from . import limits as L
from . import autofocus as af
from .clock import Clock, RealClock
from .sem import CoincidenceSafetyError

# SEM autofocus is delegated to the ported gisdep routine (two-phase confirmed
# sustained-Idle, injected clock, abort support) in coincidence.autofocus.
DEFAULT_STABLE_IDLE_S = af.AutoFocusConfig().sustained_idle_s


@dataclass
class CoincidenceResult:
    ok: bool
    reason: str = ""
    recommendation: Optional[L.ZRecommendation] = None
    sem_image_path: Optional[str] = None
    fib_image_path: Optional[str] = None


def measure_and_recommend(sem, *, target_wd_m: float = L.TARGET_WD_M,
                          stable_idle_s: float = DEFAULT_STABLE_IDLE_S,
                          clock: Optional[Clock] = None,
                          abort: Optional[object] = None,
                          logger: Optional[Callable[[str], None]] = None
                          ) -> CoincidenceResult:
    """Focus the SEM, read WD, and report a recommended (not executed) Z move."""
    log = logger or (lambda m: None)
    clock = clock or RealClock()

    # 1) SEM beam on?
    try:
        kv = float(sem.get_float("AP_ACTUALKV"))
    except Exception as e:
        return CoincidenceResult(False, f"could not read AP_ACTUALKV: {e}")
    if kv < L.EHT_MIN_ON_V:
        return CoincidenceResult(
            False, f"SEM beam appears OFF (AP_ACTUALKV={kv:.1f} V). Turn the SEM "
                   f"beam on before measuring working distance.")

    # 2) read tilt (informational only -- coincidence at 5 mm WD is tilt-independent).
    try:
        tilt = float(sem.get_float("AP_STAGE_AT_T"))
    except Exception:
        tilt = float("nan")

    # 3) current Z
    try:
        z_now = float(sem.get_float("AP_STAGE_AT_Z"))
    except Exception as e:
        return CoincidenceResult(False, f"could not read AP_STAGE_AT_Z: {e}")
    log(f"[coincidence] beam on ({kv:.0f} V), tilt {tilt:.3f} deg, "
        f"Z = {z_now * 1e3:.4f} mm")

    # 4-5) coarse then fine autofocus (ported gisdep routine), which reads AP_WD.
    cfg = af.AutoFocusConfig(sustained_idle_s=float(stable_idle_s))
    try:
        result = af.autofocus(sem, do_coarse=True, do_fine=True, clock=clock,
                              cfg=cfg, abort=abort, logger=log)
    except af.AutoFocusError as e:
        return CoincidenceResult(False, str(e))
    except CoincidenceSafetyError as e:
        return CoincidenceResult(False, str(e))
    wd = float(result.wd_after_m)
    log(f"[coincidence] focused; AP_WD = {wd * 1e3:.4f} mm")

    # 6) recommend (does not move anything)
    rec = L.recommend_z_move(wd, z_now, target_wd_m=target_wd_m)
    return CoincidenceResult(True, "", rec)


def _wait_stage_idle(sem, *, target_z_m=None, tol_m: float = 0.3e-3,
                     timeout_s: float = 120.0, poll_s: float = 0.25,
                     logger: Callable[[str], None]) -> None:
    """Wait until the stage is idle AND (if given) actually at target_z_m.

    MoveStage is non-blocking and DP_STAGE_IS can read Idle *before* the stage starts
    moving, so requiring the read-back Z to reach the target avoids proceeding
    mid-travel (the premature-Idle trap).
    """
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            idle = int(round(float(sem.get_float("DP_STAGE_IS")))) == 0
        except Exception:
            return
        arrived = True
        if target_z_m is not None:
            try:
                arrived = abs(float(sem.get_float("AP_STAGE_AT_Z")) - target_z_m) <= tol_m
            except Exception:
                arrived = True
        if idle and arrived:
            return
        time.sleep(poll_s)
    logger(f"[coincidence] WARNING: stage did not reach target within {timeout_s:.0f}s.")


def run_coincidence(sem, *, target_wd_m: float = L.TARGET_WD_M,
                    allow_move: bool = True,
                    confirm_fn: Optional[Callable[[L.ZRecommendation], bool]] = None,
                    stable_idle_s: float = DEFAULT_STABLE_IDLE_S,
                    clock: Optional[Clock] = None,
                    abort: Optional[object] = None,
                    image_out_dir: Optional[object] = None,
                    _capture_images_fn=None,
                    logger: Optional[Callable[[str], None]] = None
                    ) -> CoincidenceResult:
    """Measure WD and, when the correction is within the per-move cap, MOVE Z.

    Decision (per the operator's spec):
      * WD already at target      -> nothing to do; report coincidence.
      * WD outside [3, 8] mm       -> focus suspect; refuse, do not move.
      * target Z outside [20, 40]  -> refuse; DO NOT MOVE.
      * |move| > 0.5 mm            -> pyFIB will NOT move; RECOMMEND the move to the
                                      operator to make by hand, then re-run.
      * |move| <= 0.5 mm and safe  -> pyFIB moves Z (after confirm_fn), waits for the
                                      stage, then re-autofocuses to verify.

    ``allow_move=False`` makes it report-only (never moves). ``confirm_fn`` is asked
    before any move; if it is None or returns False, nothing moves.
    """
    log = logger or (lambda m: None)
    res = measure_and_recommend(sem, target_wd_m=target_wd_m,
                                stable_idle_s=stable_idle_s, clock=clock,
                                abort=abort, logger=log)
    if not res.ok:
        return res
    rec = res.recommendation
    log("")
    log(rec.format())
    log("")

    if rec.already_at_target:
        log(f"  Already at {rec.target_wd_m * 1e3:.4f} mm -> FIB and SEM are coincident. "
            f"No move needed.")
        _maybe_capture(sem, res, allow_move=allow_move, image_out_dir=image_out_dir,
                       clock=clock, capture_fn=_capture_images_fn, logger=log)
        return res

    if not rec.wd_in_window:
        log("  *** NOT MOVING *** measured WD is outside the trusted window; focus "
            "is suspect. Re-check focus before any Z move.")
        return res

    if not rec.within_hard_bounds:
        log("  *** DO NOT MOVE *** target Z is outside the hard limits [20, 40] mm.")
        return res

    if not rec.within_step_cap:
        # Larger than the cap: pyFIB will not move it -- recommend to the operator.
        log(f"  Correction {abs(rec.delta_z_m) * 1e3:.4f} mm exceeds the "
            f"{L.MAX_Z_STEP_M * 1e3:.2f} mm cap -- pyFIB will NOT move Z.")
        log(f"  RECOMMENDED (operator, by hand): move Z by "
            f"{rec.delta_z_m * 1e3:+.4f} mm to {rec.z_target_m * 1e3:.4f} mm, "
            f"then re-run to verify.")
        return res

    # Within the cap and safe: pyFIB can move it, with confirmation.
    if not allow_move:
        log(f"  (report-only) pyFIB WOULD move Z by {rec.delta_z_m * 1e3:+.4f} mm "
            f"to {rec.z_target_m * 1e3:.4f} mm; not moving (allow_move=False).")
        return res
    if confirm_fn is None or not confirm_fn(rec):
        log("  Move not confirmed; nothing moved.")
        return res

    log(f"  Moving Z by {rec.delta_z_m * 1e3:+.4f} mm to {rec.z_target_m * 1e3:.4f} mm ...")
    try:
        sem.move_z(rec.z_target_m)
    except Exception as e:  # includes CoincidenceSafetyError from the guard
        log(f"  Move refused/failed: {e}")
        return CoincidenceResult(False, str(e), rec)
    _wait_stage_idle(sem, target_z_m=rec.z_target_m, logger=log)
    log("  Z move complete. Re-focusing to verify ...")

    verify = measure_and_recommend(sem, target_wd_m=target_wd_m,
                                   stable_idle_s=stable_idle_s, clock=clock,
                                   abort=abort, logger=log)
    if verify.ok:
        vr = verify.recommendation
        log("")
        log(vr.format())
        log("")
        if vr.already_at_target:
            log(f"  VERIFIED: WD = {vr.wd_meas_m * 1e3:.4f} mm -> at coincidence.")
            _maybe_capture(sem, verify, allow_move=allow_move,
                           image_out_dir=image_out_dir, clock=clock,
                           capture_fn=_capture_images_fn, logger=log)
        else:
            log(f"  After the move, WD = {vr.wd_meas_m * 1e3:.4f} mm "
                f"(residual {vr.delta_z_m * 1e3:+.4f} mm). No further move performed "
                f"(no convergence loop yet) -- re-run if you want another step.")
    return verify


def _maybe_capture(sem, result: CoincidenceResult, *, allow_move: bool,
                   image_out_dir, clock, capture_fn, logger) -> None:
    """After reaching the coincidence WD, grab an SEM then a FIB overview image.

    Only runs when a real (armed) adjust flow reached the correct WD and an output
    directory was given. Never runs for report-only/dry-run. Best-effort: an imaging
    failure is logged but does not fail the coincidence result.
    """
    if not allow_move or image_out_dir is None:
        return
    fn = capture_fn
    if fn is None:
        from .imaging import capture_sem_then_fib as fn  # default real capture
    try:
        logger("[coincidence] capturing SEM then FIB overview images ...")
        pair = fn(sem, image_out_dir, clock=clock, logger=logger)
        result.sem_image_path = pair.sem_path
        result.fib_image_path = pair.fib_path
    except Exception as e:  # noqa: BLE001
        logger(f"[coincidence] WARNING: image capture failed: {e}")
