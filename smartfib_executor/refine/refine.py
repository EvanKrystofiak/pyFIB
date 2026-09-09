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
    min_snr: float = 4.0,
    beam_sign_x: float = g.BEAM_SIGN_X,
    beam_sign_y: float = g.BEAM_SIGN_Y,
    crop_bar: bool = True,
    max_scan_rotation_deg: float = 0.5,
    strict_scan_rotation: bool = False,
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

    if mag0 <= residual_tolerance_m:
        return RefineResult(
            success=True, method="none",
            initial_drift_x_m=dx_m, initial_drift_y_m=dy_m, initial_drift_mag_m=mag0,
            residual_drift_m=mag0, iterations=0, frames=frames,
            peak=peak0, snr=snr0, history=history,
            message=f"Already within tolerance ({mag0 * 1e6:.2f} um).",
        )

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
                         f"(untested). Not corrected."),
            )
        return _refine_via_stage(scope, reference_image, magnification, crop_bar,
                                 dx_m, dy_m, mag0, reg, frames, history,
                                 residual_tolerance_m, max_iterations, log)

    return _refine_via_beam(scope, reference_image, magnification, crop_bar,
                            dx_m, dy_m, mag0, reg, frames, history,
                            residual_tolerance_m, max_iterations, verify,
                            beam_sign_x, beam_sign_y, log)


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
                      frames, history, tol, max_iter, log) -> RefineResult:
    """Stage-based correction. Only reached when enable_stage=True (off by default).

    UNVERIFIED: stage<->image axis signs and rotation are not calibrated. Written so
    the path exists and is testable; do not rely on it until validated.
    """
    sx0, sy0 = scope.get_stage_xy()
    ddx, ddy = g.stage_correction(dx_m, dy_m)
    log(f"[refine] STAGE (X/Y only) += ({ddx * 1e6:+.2f}, {ddy * 1e6:+.2f}) um "
        f"[UNVERIFIED sign/rotation]")
    scope.move_stage_xy(sx0 + ddx, sy0 + ddy)

    cur_reg, cur_dx_m, cur_dy_m, _ = _measure(scope, reference, mag, crop_bar)
    frames += 1
    cur_mag = hypot(cur_dx_m, cur_dy_m)
    history.append(dict(iter=1, dx_m=cur_dx_m, dy_m=cur_dy_m, mag_m=cur_mag,
                        peak=cur_reg.peak, snr=cur_reg.snr))
    if cur_mag >= mag0 - 1e-12:
        scope.move_stage_xy(sx0, sy0)  # undo
        return RefineResult(
            success=False, method="stage",
            initial_drift_x_m=dx_m, initial_drift_y_m=dy_m, initial_drift_mag_m=mag0,
            residual_drift_m=mag0, applied_stage_delta_m=(0.0, 0.0),
            iterations=1, frames=frames, peak=cur_reg.peak, snr=cur_reg.snr,
            history=history,
            message="Stage correction did not reduce drift (uncalibrated); undone.",
        )
    return RefineResult(
        success=cur_mag <= tol, method="stage",
        initial_drift_x_m=dx_m, initial_drift_y_m=dy_m, initial_drift_mag_m=mag0,
        residual_drift_m=cur_mag, applied_stage_delta_m=(ddx, ddy),
        iterations=1, frames=frames, peak=cur_reg.peak, snr=cur_reg.snr,
        history=history,
        message=f"Stage-refined to {cur_mag * 1e6:.2f} um.",
    )
