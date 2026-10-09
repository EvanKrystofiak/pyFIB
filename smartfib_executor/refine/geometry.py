"""Geometry for position refinement: pixels <-> metres, and correction signs.

Everything sign-related lives HERE, in one tested place, because a position
correction is a feedback loop and a wrong sign doubles the error. Even so, the
orchestrator does not TRUST these signs -- it closes the loop and verifies the
residual shrank (see refine.py). These defaults are the starting guess.
"""

from __future__ import annotations

from typing import Tuple

# Magnification -> field-of-view calibration. Duplicated from the autofocus fork
# on purpose: this module stays isolated until it is proven on the instrument.
# The magnification-reference geometry now lives in the shared smartfib_executor.fov
# module (single source of truth, imported by core capture too). Re-exported here so
# the refine fork's public API (geometry.MAG_REF_WIDTH_M / fov_width_m / pixel_size_m)
# is unchanged.
from ..fov import MAG_REF_WIDTH_M, fov_width_m, pixel_size_m  # noqa: F401


# --- correction thresholds -------------------------------------------------- #
# <= this drift magnitude -> correct with FIB beam shift; above it -> stage move.
BEAM_SHIFT_THRESHOLD_M = 1.00e-05     # 10 um (operator spec)
# Instrument hard limit on AP_FIB_BEAM_SHIFT_X/Y; we never command beyond this.
BEAM_SHIFT_MAX_M = 1.50e-05           # 15 um


# --- beam-shift sign convention --------------------------------------------- #
# Terminology used throughout:
#   R ("response") = image displacement per unit of applied beam shift, +/-1.
#                    R=+1 -> +shift moves the image RIGHT / DOWN
#                    R=-1 -> +shift moves the image LEFT  / UP
#   BEAM_SIGN = -R = the sign that turns a measured drift into the nulling delta:
#                    beam_delta = BEAM_SIGN * drift
#
# MEASURED ON THIS INSTRUMENT (Crossbeam 550, 2000x) by calibrate.py, v0.7.2.
# Applying +2 um of beam shift moved the IMAGE:
#     X: +36.8 px  -> RIGHT  -> R_x = +1   (snr 18.1, crosstalk 2.0%)
#     Y: +35.7 px  -> DOWN   -> R_y = +1   (snr 19.0, crosstalk 2.7%)
# With correction_sign = -R, both axes are -1.
#
# NOTE: this OVERRODE a derivation made from a verbal description of the beam
# direction, which had implied the opposite sign. The measurement wins; that is
# the entire reason calibrate.py exists. Re-run it after any change of column,
# scan rotation or magnification, and prefer a saved refine_calibration.json.
#
# Crosstalk was ~2-3% on both axes, so the image axes are effectively aligned
# with the beam-shift axes (no significant scan rotation to model).
BEAM_SIGN_X = -1.0
BEAM_SIGN_Y = -1.0

# Measured pixel size at 2000x: 54.28 nm (X) and 56.04 nm (Y), mean 55.16 nm vs
# the nominal 55.81 nm from MAG_REF_WIDTH_M -- agreement to ~1.2%. The X/Y spread
# (~3%) exceeds that, so the difference is measurement noise (snr ~18) rather than
# a calibration error; MAG_REF_WIDTH_M is left as-is.
MEASURED_PIXEL_SIZE_M_AT_2000X = 55.16e-9


def drift_px_to_m(dy_px: float, dx_px: float, magnification: float,
                  image_width_px: int) -> Tuple[float, float]:
    """Convert a (dy, dx) pixel drift to (drift_x_m, drift_y_m) in sample metres."""
    px = pixel_size_m(magnification, image_width_px)
    return dx_px * px, dy_px * px


def beam_shift_correction(drift_x_m: float, drift_y_m: float,
                          sign_x: float = BEAM_SIGN_X,
                          sign_y: float = BEAM_SIGN_Y) -> Tuple[float, float]:
    """Beam-shift DELTA (metres) that counters a measured drift."""
    return sign_x * drift_x_m, sign_y * drift_y_m


# --- stage sign convention: MEASURED on the instrument 2026-09-22 ------------ #
# refine --calibrate-stage at 2000x, 20 um steps, stage tilt 30 deg
# (refine_stage_calibration.json, locked by a regression test):
#   +20 um stage X -> image +323.3 px (right)  => response +1 => STAGE_SIGN_X = -1
#   +20 um stage Y -> image -309.9 px (up)     => response -1 => STAGE_SIGN_Y = +1
# NOTE: the old placeholder had Y = -1 -- WRONG. Had the stage correction been
# enabled on it, every Y correction would have moved the wrong way and doubled the
# error. Cross-talk ~1.2% (rotation hint +0.7 deg): no rotation term needed.
STAGE_SIGN_X = -1.0
STAGE_SIGN_Y = +1.0
STAGE_ROTATION_DEG = 0.0

# --- stage -> image SCALE: a TILT MODEL fitted to two calibrations ---------- #
# Measured 2026-09-22 at 2000x, R = 12 deg, 20 um steps:
#   tilt 30.0 deg: scale X 0.902, Y 0.865
#   tilt  1.97 deg: scale X 0.922, Y 0.584
# Y is the axis foreshortened by the FIB viewing angle. Solving the ratio of the two
# Y scales for the column angle (NOT assumed) gives 53.9 deg -- the Crossbeam FIB
# column is at 54 deg -- so:
#     scale_Y(tilt) = K_Y * cos(FIB_ANGLE - tilt),   scale_X ~ constant
# A single fixed calibration fails across tilts (the 30 deg values used at 2 deg
# leave 32% of the drift; the 2 deg values used at 30 deg overshoot 48%), so the
# correction reads the LIVE tilt and uses this model. Milling tilts: 0-12 and 30 deg.
# The 0-12 range is interpolated/extrapolated between the measured points; a
# calibration at ~12 deg would validate it (model predicts Y 0.70, X 0.91).
STAGE_FIB_ANGLE_DEG = 53.9       # fitted from the two runs (Crossbeam nominal 54)
STAGE_K_Y = 0.946                # fitted
STAGE_SCALE_X = 0.912            # mean of 0.902 / 0.922 (+/-1.1%), tilt-independent
STAGE_MODEL_TILT_RANGE_DEG = (-1.0, 31.0)   # refuse stage correction outside this

# Rotation is FIXED for cryo-milling and is not modelled -- but both calibrations
# were taken at R = 12 deg, so stage correction is REFUSED if the live R differs.
STAGE_CALIBRATION_ROT_DEG = 12.0
STAGE_ROT_TOL_DEG = 1.0

# Refine-only stage limits. NOT applied to recall / setup / prep / coincidence.
REFINE_STAGE_MAX_M = 100.0e-6    # per axis; above this refine REFUSES (no partial move)
STAGE_MIN_IMPROVEMENT = 0.5      # after the move, drift must be < 50% of before, else undo


def stage_scale(tilt_deg: float) -> Tuple[float, float]:
    """(scale_x, scale_y): image px moved per px expected, for a stage step, at tilt."""
    import math
    return (STAGE_SCALE_X,
            STAGE_K_Y * math.cos(math.radians(STAGE_FIB_ANGLE_DEG - float(tilt_deg))))


def stage_model_applies(tilt_deg: float, rot_deg: float) -> Tuple[bool, str]:
    """Is the calibrated stage model valid at this tilt/rotation? (ok, reason)."""
    lo, hi = STAGE_MODEL_TILT_RANGE_DEG
    if not (lo <= float(tilt_deg) <= hi):
        return False, (f"stage tilt {float(tilt_deg):.2f} deg is outside the calibrated "
                       f"model range [{lo:.0f}, {hi:.0f}] deg")
    d = (float(rot_deg) - STAGE_CALIBRATION_ROT_DEG + 180.0) % 360.0 - 180.0
    if abs(d) > STAGE_ROT_TOL_DEG:
        return False, (f"stage rotation {float(rot_deg):.2f} deg differs from the "
                       f"calibration rotation {STAGE_CALIBRATION_ROT_DEG:.1f} deg "
                       f"(re-run refine --calibrate-stage at this R)")
    return True, ""


def stage_correction(drift_x_m: float, drift_y_m: float, *, tilt_deg: float,
                     sign_x: float = STAGE_SIGN_X, sign_y: float = STAGE_SIGN_Y,
                     rotation_deg: float = STAGE_ROTATION_DEG) -> Tuple[float, float]:
    """Stage DELTA (dx_m, dy_m) that counters a measured image drift at `tilt_deg`.

    tilt_deg is REQUIRED (keyword-only) so a caller can never silently use the wrong
    tilt. The drift is image-plane metres (px * nominal pixel size); stage motion
    projects onto the image foreshortened (scale < 1), so the delta is divided by the
    tilt-model scale -- the stage must move MORE than the image drift.
    """
    import math

    sx, sy = stage_scale(tilt_deg)
    th = math.radians(rotation_deg)
    rx = drift_x_m * math.cos(th) - drift_y_m * math.sin(th)
    ry = drift_x_m * math.sin(th) + drift_y_m * math.cos(th)
    return sign_x * rx / sx, sign_y * ry / sy
