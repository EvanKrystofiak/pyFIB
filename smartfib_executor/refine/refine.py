"""Position refinement orchestrator.

After a stage recall, cross-correlate the stored reference BMP with a fresh FIB
image at the SAME magnification, then pull the field back onto the original target:

  * drift <= threshold (10 um): correct with FIB beam shift.
  * drift  > threshold:         a stage move is required -- the code path exists but
                                is DISABLED by default (enable_stage=False) pending
                                on-instrument sign/rotation calibration.

Closed-loop safety: a mis-signed correction doubles the error, so (with verify=True,
the default) every correction is followed by a fresh image + re-correlation. If the
residual does not shrink, the last correction is UNDONE and the refinement reports
failure rather than leaving the sample worse off. This check measures the real
residual and does not depend on the assumed sign being right.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import hypot
from typing import Callable, List, Optional, Tuple

import numpy as np

from . import geometry as g
from .registration import phase_cross_correlation, RegistrationResult
from .sources import RefineScope


@dataclass
class RefineResult:
    success: bool
    method: str                       # 'beam_shift' | 'stage' | 'none'
    initial_drift_x_m: float
    initial_drift_y_m: float
    initial_drift_mag_m: float
    residual_drift_m: float
    applied_beam_shift_delta_m: Tuple[float, float] = (0.0, 0.0)
    applied_stage_delta_m: Tuple[float, float] = (0.0, 0.0)
    iterations: int = 0
    frames: int = 0
    peak: float = 0.0
    snr: float = 0.0
    low_confidence: bool = False
    stage_required_but_disabled: bool = False
    stage_refused: bool = False       # stage path reached but a guard refused the move
    message: str = ""
    history: List[dict] = field(default_factory=list)


def _measure(scope: RefineScope, reference: np.ndarray, magnification: float,
             crop_bar: bool) -> Tuple[RegistrationResult, float, float, np.ndarray]:
    new = scope.grab_fib_image(magnification)
    reg = phase_cross_correlation(reference, new, crop_bar=crop_bar)
    # Pixel size MUST be consistent with the grid we actually correlated on. Deriving
    # it from the correlated image width (as v0.8.1 did) is self-consistent no matter
    # what the store/display resolution is. AP_FIB_PIXEL_SIZE is tied to the STORE
    # grid, so using it directly mis-scales every correction whenever the store width
    # differs from the grabbed width -- this caused the v0.9.0 "very small movements"
    # regression (both beam-shift and stage corrections shrank by grab_w/store_w).
    # The live value is used only as a cross-check in refine_position(), never to
    # drive the correction.
    dx_m, dy_m = g.drift_px_to_m(reg.dy_px, reg.dx_px, magnification, reg.shape[1])
    return reg, dx_m, dy_m, new


def refine_position(
    scope: RefineScope,
    reference_image: np.ndarray,
    magnification: float,
    *,
    beam_shift_threshold_m: float = g.BEAM_SHIFT_THRESHOLD_M,
    residual_tolerance_m: float = 3.0e-7,   # ~0.3 um; ~5 px at 2000x/1024
    max_iterations: int = 2,
    verify: bool = True,
    enable_stage: bool = False,
    stage_max_m: float = g.REFINE_STAGE_MAX_M,
    plan_only: bool = False,
    min_snr: float = 4.0,
    beam_sign_x: float = g.BEAM_SIGN_X,
    beam_sign_y: float = g.BEAM_SIGN_Y,
    crop_bar: bool = True,
    max_scan_rotation_deg: float = 0.5,
    strict_scan_rotation: bool = False,
    max_drift_fov_fraction: float = 0.4,   # beyond this the correlation may be ALIASED
    logger: Optional[Callable[[str], None]] = None,
) -> RefineResult:
    """Refine the current field back onto the stored reference target."""
    log = logger or (lambda m: None)
    frames = 0
    history: List[dict] = []

    # Scan-rotation guard: the correction axes assume image == beam-shift axes.
    rot = None
    try:
        rot = scope.get_scan_rotation_deg()
    except Exception:
        rot = None
    if rot is not None and abs(rot) > max_scan_rotation_deg:
        msg = (f"scan rotation is {rot:.2f} deg (> {max_scan_rotation_deg:.2f}); the "
               f"refine correction axes assume ~0. Correction directions would be "
               f"wrong.")
        if strict_scan_rotation:
            return RefineResult(
                success=False, method="none",
                initial_drift_x_m=0.0, initial_drift_y_m=0.0, initial_drift_mag_m=0.0,
                residual_drift_m=0.0, low_confidence=True,
                message="Refused: " + msg + " (strict_scan_rotation=True)")
        log(f"[refine] *** WARNING: {msg} Proceeding, but verify the residual "
            f"actually shrinks. ***")

    reg, dx_m, dy_m, _ = _measure(scope, reference_image, magnification, crop_bar)
    frames += 1
    mag0 = hypot(dx_m, dy_m)

    # Cross-check: warn if the instrument's live (store-grid) pixel size disagrees
    # with the correlated-image pixel size. A mismatch means store resolution !=
    # grabbed width; we deliberately drive corrections from the image-consistent
    # value, but surfacing the discrepancy would have caught the v0.9.0 regression.
    try:
        live_px = scope.get_pixel_size_m()
    except Exception:
        live_px = None
    if live_px:
        img_px = g.pixel_size_m(magnification, reg.shape[1])
        if img_px > 0 and abs(live_px - img_px) / img_px > 0.10:
            log(f"[refine] note: live pixel size {live_px * 1e9:.1f} nm != correlated-"
                f"image pixel size {img_px * 1e9:.1f} nm (store resolution differs from "
                f"the grabbed width). Using the image-consistent value; corrections are "
                f"scaled correctly.")
    peak0, snr0 = reg.peak, reg.snr
    log(f"[refine] initial drift dx={dx_m * 1e6:+.2f} um dy={dy_m * 1e6:+.2f} um "
        f"|d|={mag0 * 1e6:.2f} um (snr={snr0:.1f})")
    history.append(dict(iter=0, dx_m=dx_m, dy_m=dy_m, mag_m=mag0,
                        peak=peak0, snr=snr0))

    # Confidence gate: a weak/ambiguous correlation must not drive a correction.
    if snr0 < min_snr:
        return RefineResult(
            success=False, method="none",
            initial_drift_x_m=dx_m, initial_drift_y_m=dy_m, initial_drift_mag_m=mag0,
            residual_drift_m=mag0, iterations=0, frames=frames,
            peak=peak0, snr=snr0, low_confidence=True, history=history,
            message=(f"Low confidence: correlation snr {snr0:.1f} < {min_snr:.1f}. "
                     "Not correcting (check overlap / magnification match)."),
        )

    # ALIASING GUARD. Phase correlation resolves shifts only within +/- half the field;
    # a larger true shift wraps to the WRONG side with a perfectly confident peak (a
    # 30 um offset at 2000x measured as -27 um at snr 87 in the simulator). Refuse to
    # act on a drift beyond this fraction of the field: it may be aliased, and a
    # coarse (lower-mag) pass is the way to measure it.
    fov_w_m = g.pixel_size_m(magnification, reg.shape[1]) * reg.shape[1]
    fov_h_m = g.pixel_size_m(magnification, reg.shape[1]) * reg.shape[0]
    if (abs(dx_m) > max_drift_fov_fraction * fov_w_m
            or abs(dy_m) > max_drift_fov_fraction * fov_h_m):
        return RefineResult(
            success=False, method="none",
            initial_drift_x_m=dx_m, initial_drift_y_m=dy_m, initial_drift_mag_m=mag0,
            residual_drift_m=mag0, iterations=0, frames=frames,
            peak=peak0, snr=snr0, low_confidence=True, history=history,
            message=(f"Unreliable: measured drift ({dx_m * 1e6:+.1f}, {dy_m * 1e6:+.1f}) um "
                     f"exceeds {max_drift_fov_fraction * 100:.0f}% of the field at "
                     f"{magnification:.0f}x ({fov_w_m * 1e6:.0f} x {fov_h_m * 1e6:.0f} um) "
                     f"and may be aliased. Not correcting; use a coarse (lower-mag) pass."),
        )

    if mag0 <= residual_tolerance_m:
        return RefineResult(
            success=True, method="none",
            initial_drift_x_m=dx_m, initial_drift_y_m=dy_m, initial_drift_mag_m=mag0,
            residual_drift_m=mag0, iterations=0, frames=frames,
            peak=peak0, snr=snr0, history=history,
            message=f"Already within tolerance ({mag0 * 1e6:.2f} um).",
        )

    if plan_only:
        return _plan(scope, dx_m, dy_m, mag0, reg, frames, history,
                     beam_shift_threshold_m, enable_stage, stage_max_m,
                     beam_sign_x, beam_sign_y, log)

    # Route: beam shift vs stage.
    if mag0 > beam_shift_threshold_m:
        if not enable_stage:
            return RefineResult(
                success=False, method="stage",
                initial_drift_x_m=dx_m, initial_drift_y_m=dy_m,
                initial_drift_mag_m=mag0, residual_drift_m=mag0,
                iterations=0, frames=frames, peak=peak0, snr=snr0,
                stage_required_but_disabled=True, history=history,
                message=(f"Drift {mag0 * 1e6:.2f} um exceeds the "
                         f"{beam_shift_threshold_m * 1e6:.0f} um beam-shift limit; a "
                         f"stage move is required, but stage correction is DISABLED "
                         f"(enable with --enable-stage). Not corrected."),
            )
        return _refine_via_stage(scope, reference_image, magnification, crop_bar,
                                 dx_m, dy_m, mag0, reg, frames, history,
                                 residual_tolerance_m, max_iterations, verify,
                                 beam_sign_x, beam_sign_y, log,
                                 beam_shift_threshold_m, stage_max_m)

    return _refine_via_beam(scope, reference_image, magnification, crop_bar,
                            dx_m, dy_m, mag0, reg, frames, history,
                            residual_tolerance_m, max_iterations, verify,
                            beam_sign_x, beam_sign_y, log)


class _WithScanRate:
    """Scope proxy that grabs at a given scan rate (coarse pass); everything else is
    delegated, so refine_position's internals need no changes."""
    def __init__(self, scope, scan_rate):
        self._scope, self._rate = scope, scan_rate
    def grab_fib_image(self, magnification: float):
        return self._scope.grab_fib_image(magnification, scan_rate=self._rate)
    def __getattr__(self, name):
        return getattr(self._scope, name)


@dataclass
class TwoStageResult:
    coarse: Optional[RefineResult]
    fine: Optional[RefineResult]
    success: bool
    message: str
    stage_moved: bool = False


def refine_two_stage(
    scope: RefineScope,
    coarse_reference: np.ndarray,
    coarse_magnification: float,
    fine_reference: np.ndarray,
    fine_magnification: float,
    *,
    coarse_scan_rate: Optional[int] = 5,
    coarse_tolerance_m: float = 1.0e-6,      # the fine pass finishes the job
    enable_stage: bool = False,
    stage_max_m: float = g.REFINE_STAGE_MAX_M,
    plan_only: bool = False,
    beam_sign_x: float = g.BEAM_SIGN_X,
    beam_sign_y: float = g.BEAM_SIGN_Y,
    max_iterations: int = 2,
    verify: bool = True,
    logger: Optional[Callable[[str], None]] = None,
) -> TwoStageResult:
    """COARSE pass at a lower magnification, then the unchanged FINE pass.

    Why coarse FIRST (not as a fallback): at the fine mag an out-of-reach drift does
    not fail cleanly -- it aliases to a confident, wrong measurement. The coarse pass
    (wider field) measures drifts of tens of um, uses the stage (if enabled, within the
    refine-only limits: X/Y, <= stage_max_m, one move, undone unless the drift halves)
    to bring the site back into beam-shift range, and converges to coarse_tolerance_m
    with beam shift. The fine pass then runs exactly as before, beam shift only. The
    instrument is left at the fine magnification either way.
    """
    log = logger or (lambda m: None)
    cs = _WithScanRate(scope, coarse_scan_rate) if coarse_scan_rate is not None else scope
    coarse = fine = None
    try:
        log(f"[refine] COARSE pass at {coarse_magnification:.0f}x"
            + (f" (scan rate {coarse_scan_rate})" if coarse_scan_rate is not None else ""))
        coarse = refine_position(cs, coarse_reference, coarse_magnification,
                                 residual_tolerance_m=coarse_tolerance_m,
                                 enable_stage=enable_stage, stage_max_m=stage_max_m,
                                 plan_only=plan_only, beam_sign_x=beam_sign_x,
                                 beam_sign_y=beam_sign_y, max_iterations=max_iterations,
                                 verify=verify, logger=log)
        moved = coarse.applied_stage_delta_m != (0.0, 0.0)
        if plan_only:
            return TwoStageResult(coarse, None, False, f"COARSE {coarse.message}", False)
        if coarse.low_confidence:
            return TwoStageResult(coarse, None, False,
                                  f"Coarse pass could not lock: {coarse.message}", moved)
        if not coarse.success:
            return TwoStageResult(coarse, None, False,
                                  f"Coarse pass failed: {coarse.message}", moved)
        log(f"[refine] coarse done (residual {coarse.residual_drift_m * 1e6:.2f} um); "
            f"FINE pass at {fine_magnification:.0f}x")
        fine = refine_position(scope, fine_reference, fine_magnification,
                               enable_stage=False, beam_sign_x=beam_sign_x,
                               beam_sign_y=beam_sign_y, max_iterations=max_iterations,
                               verify=verify, logger=log)
        return TwoStageResult(coarse, fine, fine.success,
                              f"coarse: {coarse.message} | fine: {fine.message}", moved)
    finally:
        try:
            scope.ensure_magnification(fine_magnification)   # never leave it at coarse
        except Exception:
            pass


def _plan(scope, dx_m, dy_m, mag0, reg, frames, history, window_m, enable_stage,
          stage_max_m, beam_sign_x, beam_sign_y, log) -> RefineResult:
    """PLAN ONLY: report what refine WOULD do from a real measurement, and move
    NOTHING (no beam shift, no stage). The safe first step on the instrument."""
    if mag0 <= window_m:
        ddx, ddy = g.beam_shift_correction(dx_m, dy_m, beam_sign_x, beam_sign_y)
        bx, by = scope.get_beam_shift()
        nx = min(max(bx + ddx, -g.BEAM_SHIFT_MAX_M), g.BEAM_SHIFT_MAX_M)
        ny = min(max(by + ddy, -g.BEAM_SHIFT_MAX_M), g.BEAM_SHIFT_MAX_M)
        msg = (f"PLAN: beam shift += ({ddx * 1e6:+.2f}, {ddy * 1e6:+.2f}) um -> "
               f"({nx * 1e6:+.2f}, {ny * 1e6:+.2f}) um. Nothing moved.")
    elif not enable_stage:
        msg = (f"PLAN: drift {mag0 * 1e6:.2f} um exceeds the "
               f"{window_m * 1e6:.0f} um beam-shift window; a stage move would be "
               f"needed but --enable-stage is off. Nothing moved.")
    else:
        try:
            tilt, rot = scope.get_stage_tilt_rot()
            ok, why = g.stage_model_applies(tilt, rot)
        except Exception as e:  # noqa: BLE001
            ok, why, tilt = False, f"could not read stage tilt/rotation ({e})", None
        if not ok:
            msg = f"PLAN: stage correction would be REFUSED: {why}. Nothing moved."
        else:
            sdx, sdy = g.stage_correction(dx_m, dy_m, tilt_deg=tilt)
            sx, sy = g.stage_scale(tilt)
            if abs(sdx) > stage_max_m or abs(sdy) > stage_max_m:
                msg = (f"PLAN: stage correction would be REFUSED: required move "
                       f"({sdx * 1e6:+.1f}, {sdy * 1e6:+.1f}) um exceeds "
                       f"{stage_max_m * 1e6:.0f} um. Nothing moved.")
            else:
                msg = (f"PLAN: stage X/Y += ({sdx * 1e6:+.2f}, {sdy * 1e6:+.2f}) um at "
                       f"tilt {tilt:.2f} deg (scale X {sx:.3f}, Y {sy:.3f}), then beam "
                       f"shift for the residual; undone unless drift halves. Nothing "
                       f"moved.")
    log(f"[refine] {msg}")
    return RefineResult(
        success=False, method="plan",
        initial_drift_x_m=dx_m, initial_drift_y_m=dy_m, initial_drift_mag_m=mag0,
        residual_drift_m=mag0, iterations=0, frames=frames, peak=reg.peak,
        snr=reg.snr, history=history, message=msg)


def _refine_via_beam(scope, reference, mag, crop_bar, dx_m, dy_m, mag0, reg,
                     frames, history, tol, max_iter, verify,
                     sign_x, sign_y, log) -> RefineResult:
    beam_x0, beam_y0 = scope.get_beam_shift()
    total_dx = total_dy = 0.0
    prev_mag = mag0
    cur_dx_m, cur_dy_m = dx_m, dy_m
    cur_reg = reg
    it = 0

    while it < max_iter:
        it += 1
        ddx, ddy = g.beam_shift_correction(cur_dx_m, cur_dy_m, sign_x, sign_y)
        bx, by = scope.get_beam_shift()
        new_bx = float(np.clip(bx + ddx, -g.BEAM_SHIFT_MAX_M, g.BEAM_SHIFT_MAX_M))
        new_by = float(np.clip(by + ddy, -g.BEAM_SHIFT_MAX_M, g.BEAM_SHIFT_MAX_M))
        log(f"[refine] iter {it}: beam shift += ({ddx * 1e6:+.2f}, {ddy * 1e6:+.2f}) um "
            f"-> ({new_bx * 1e6:+.2f}, {new_by * 1e6:+.2f}) um")
        scope.set_beam_shift(new_bx, new_by)
        total_dx += (new_bx - bx)
        total_dy += (new_by - by)

        if not verify:
            return RefineResult(
                success=True, method="beam_shift",
                initial_drift_x_m=dx_m, initial_drift_y_m=dy_m,
                initial_drift_mag_m=mag0, residual_drift_m=float("nan"),
                applied_beam_shift_delta_m=(total_dx, total_dy),
                iterations=it, frames=frames, peak=reg.peak, snr=reg.snr,
                history=history,
                message="Beam-shift correction applied (verify disabled; residual not "
                        "measured).",
            )

        cur_reg, cur_dx_m, cur_dy_m, _ = _measure(scope, reference, mag, crop_bar)
        frames += 1
        cur_mag = hypot(cur_dx_m, cur_dy_m)
        history.append(dict(iter=it, dx_m=cur_dx_m, dy_m=cur_dy_m, mag_m=cur_mag,
                            peak=cur_reg.peak, snr=cur_reg.snr))
        log(f"[refine] iter {it}: residual |d|={cur_mag * 1e6:.2f} um")

        if cur_mag <= tol:
            return RefineResult(
                success=True, method="beam_shift",
                initial_drift_x_m=dx_m, initial_drift_y_m=dy_m,
                initial_drift_mag_m=mag0, residual_drift_m=cur_mag,
                applied_beam_shift_delta_m=(total_dx, total_dy),
                iterations=it, frames=frames, peak=cur_reg.peak, snr=cur_reg.snr,
                history=history,
                message=f"Refined to {cur_mag * 1e6:.2f} um with beam shift.",
            )

        # Decide from HOW the residual changed. A wrong sign roughly DOUBLES the
        # error, so it shows up as a clear increase -- that is the only case we treat
        # as a sign error and undo. "Did not shrink much" at low correlation SNR is
        # the noise floor, NOT a sign error: keep the (neutral) correction and stop
        # crying wolf.
        WORSE_FACTOR = 1.25
        if cur_mag > prev_mag * WORSE_FACTOR:
            scope.set_beam_shift(bx, by)
            total_dx -= (new_bx - bx)
            total_dy -= (new_by - by)
            log(f"[refine] residual INCREASED ({prev_mag * 1e6:.2f} -> "
                f"{cur_mag * 1e6:.2f} um) -- wrong-sign signature; UNDID last beam shift.")
            return RefineResult(
                success=False, method="beam_shift",
                initial_drift_x_m=dx_m, initial_drift_y_m=dy_m,
                initial_drift_mag_m=mag0, residual_drift_m=prev_mag,
                applied_beam_shift_delta_m=(total_dx, total_dy),
                iterations=it, frames=frames, peak=cur_reg.peak, snr=cur_reg.snr,
                history=history,
                message=("Correction INCREASED the drift (wrong-sign signature); undid "
                         "last step. Re-run calibrate.py to check the beam-shift sign."),
            )

        if cur_mag < prev_mag - 1e-9 and it < max_iter:
            # Meaningful improvement and iterations remain -> refine further.
            prev_mag = cur_mag
            continue

        # Did not reach tolerance, but did not clearly worsen: we are at the
        # correlation noise floor. KEEP the correction (it is not hurting) and stop.
        log(f"[refine] residual {cur_mag * 1e6:.2f} um at the correlation noise floor "
            f"(snr {cur_reg.snr:.1f}); keeping correction (not a sign error).")
        return RefineResult(
            success=(cur_mag <= tol), method="beam_shift",
            initial_drift_x_m=dx_m, initial_drift_y_m=dy_m,
            initial_drift_mag_m=mag0, residual_drift_m=cur_mag,
            applied_beam_shift_delta_m=(total_dx, total_dy),
            iterations=it, frames=frames, peak=cur_reg.peak, snr=cur_reg.snr,
            history=history,
            message=(f"Residual {cur_mag * 1e6:.2f} um (tolerance {tol * 1e6:.2f} um); "
                     f"limited by correlation SNR {cur_reg.snr:.1f}. Correction kept."),
        )

    return RefineResult(
        success=cur_mag <= tol, method="beam_shift",
        initial_drift_x_m=dx_m, initial_drift_y_m=dy_m,
        initial_drift_mag_m=mag0, residual_drift_m=cur_mag,
        applied_beam_shift_delta_m=(total_dx, total_dy),
        iterations=it, frames=frames, peak=cur_reg.peak, snr=cur_reg.snr,
        history=history,
        message=(f"Stopped after {it} iterations; residual {cur_mag * 1e6:.2f} um "
                 f"(tolerance {tol * 1e6:.2f} um)."),
    )


def _refine_via_stage(scope, reference, mag, crop_bar, dx_m, dy_m, mag0, reg,
                      frames, history, tol, max_iter, verify, beam_sign_x,
                      beam_sign_y, log, beam_window_m, stage_max_m) -> RefineResult:
    """Stage correction for drift beyond the beam-shift window (enable_stage only).

    REFINE-ONLY limits (recall/setup/prep/coincidence are untouched):
      * guards: live tilt inside the calibrated model range, live R = calibration R;
      * X/Y ONLY (the adapter holds Z/T/R/M from the live read-back);
      * a delta above stage_max_m (100 um) on either axis is REFUSED -- no partial move;
      * ONE stage move per refine; afterwards the drift must be < 50% of before, else
        the move is UNDONE;
      * the residual is handed to beam shift for the fine correction.
    Signs (X -1, Y +1) and the tilt-dependent scale are MEASURED (2026-09-22).
    """
    def refused(why: str) -> RefineResult:
        log(f"[refine] STAGE correction REFUSED: {why}. Not moved.")
        return RefineResult(
            success=False, method="stage",
            initial_drift_x_m=dx_m, initial_drift_y_m=dy_m, initial_drift_mag_m=mag0,
            residual_drift_m=mag0, iterations=0, frames=frames, peak=reg.peak,
            snr=reg.snr, stage_refused=True, history=history,
            message=f"Stage correction refused: {why}. Not corrected.")

    try:
        tilt, rot = scope.get_stage_tilt_rot()
    except Exception as e:  # noqa: BLE001
        return refused(f"could not read stage tilt/rotation ({e})")
    ok, why = g.stage_model_applies(tilt, rot)
    if not ok:
        return refused(why)

    ddx, ddy = g.stage_correction(dx_m, dy_m, tilt_deg=tilt)
    sx, sy = g.stage_scale(tilt)
    log(f"[refine] STAGE decision: drift ({dx_m * 1e6:+.2f}, {dy_m * 1e6:+.2f}) um at "
        f"tilt {tilt:.2f} deg (scale X {sx:.3f}, Y {sy:.3f}) -> stage "
        f"({ddx * 1e6:+.2f}, {ddy * 1e6:+.2f}) um, X/Y only")
    if abs(ddx) > stage_max_m or abs(ddy) > stage_max_m:
        return refused(f"required stage move ({ddx * 1e6:+.1f}, {ddy * 1e6:+.1f}) um "
                       f"exceeds the refine limit of {stage_max_m * 1e6:.0f} um per axis")

    sx0, sy0 = scope.get_stage_xy()
    scope.move_stage_xy(sx0 + ddx, sy0 + ddy)

    cur_reg, cur_dx_m, cur_dy_m, _ = _measure(scope, reference, mag, crop_bar)
    frames += 1
    cur_mag = hypot(cur_dx_m, cur_dy_m)
    history.append(dict(iter=1, dx_m=cur_dx_m, dy_m=cur_dy_m, mag_m=cur_mag,
                        peak=cur_reg.peak, snr=cur_reg.snr, stage=True))
    if cur_mag > g.STAGE_MIN_IMPROVEMENT * mag0:
        scope.move_stage_xy(sx0, sy0)  # undo
        log(f"[refine] STAGE move did not reduce drift enough ({mag0 * 1e6:.2f} -> "
            f"{cur_mag * 1e6:.2f} um); UNDONE.")
        return RefineResult(
            success=False, method="stage",
            initial_drift_x_m=dx_m, initial_drift_y_m=dy_m, initial_drift_mag_m=mag0,
            residual_drift_m=mag0, applied_stage_delta_m=(0.0, 0.0),
            iterations=1, frames=frames, peak=cur_reg.peak, snr=cur_reg.snr,
            history=history,
            message=(f"Stage move reduced drift only {mag0 * 1e6:.2f} -> "
                     f"{cur_mag * 1e6:.2f} um (needs < "
                     f"{g.STAGE_MIN_IMPROVEMENT * 100:.0f}%); undone."))
    log(f"[refine] STAGE moved: drift {mag0 * 1e6:.2f} -> {cur_mag * 1e6:.2f} um")

    def with_stage(res: RefineResult, msg: str) -> RefineResult:
        import dataclasses as _dc
        return _dc.replace(res, initial_drift_x_m=dx_m, initial_drift_y_m=dy_m,
                           initial_drift_mag_m=mag0, applied_stage_delta_m=(ddx, ddy),
                           message=msg)

    if cur_mag <= tol:
        return RefineResult(
            success=True, method="stage",
            initial_drift_x_m=dx_m, initial_drift_y_m=dy_m, initial_drift_mag_m=mag0,
            residual_drift_m=cur_mag, applied_stage_delta_m=(ddx, ddy),
            iterations=1, frames=frames, peak=cur_reg.peak, snr=cur_reg.snr,
            history=history, message=f"Stage-refined to {cur_mag * 1e6:.2f} um.")
    if cur_mag > beam_window_m:
        # One stage move per refine: do not chain mechanical moves automatically.
        return RefineResult(
            success=False, method="stage",
            initial_drift_x_m=dx_m, initial_drift_y_m=dy_m, initial_drift_mag_m=mag0,
            residual_drift_m=cur_mag, applied_stage_delta_m=(ddx, ddy),
            iterations=1, frames=frames, peak=cur_reg.peak, snr=cur_reg.snr,
            history=history,
            message=(f"Stage move improved drift {mag0 * 1e6:.2f} -> "
                     f"{cur_mag * 1e6:.2f} um but it is still beyond the beam-shift "
                     f"window; not iterated (one stage move per refine)."))
    # Hand the residual to beam shift for the fine correction.
    res = _refine_via_beam(scope, reference, mag, crop_bar, cur_dx_m, cur_dy_m, cur_mag,
                           cur_reg, frames, history, tol, max_iter, verify,
                           beam_sign_x, beam_sign_y, log)
    res = with_stage(res, f"Stage moved ({ddx * 1e6:+.1f}, {ddy * 1e6:+.1f}) um, then "
                          f"beam shift: {res.message}")
    import dataclasses as _dc
    return _dc.replace(res, method="stage+beam_shift")
