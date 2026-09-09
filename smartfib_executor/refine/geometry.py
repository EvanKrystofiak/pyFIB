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


# --- stage sign convention (UNVERIFIED -- correction is written, not enabled) --
# Stage axes may be flipped and/or rotated relative to the image (scan rotation!),
# so these are placeholders. Do NOT enable stage correction until calibrated.
STAGE_SIGN_X = -1.0
STAGE_SIGN_Y = -1.0
STAGE_ROTATION_DEG = 0.0


def stage_correction(drift_x_m: float, drift_y_m: float,
                     sign_x: float = STAGE_SIGN_X, sign_y: float = STAGE_SIGN_Y,
                     rotation_deg: float = STAGE_ROTATION_DEG) -> Tuple[float, float]:
    """Stage DELTA (dx_m, dy_m) that would counter a measured drift.

    WARNING: signs and rotation are UNCALIBRATED. This function exists so the code
    path is written and testable; refine.py keeps it disabled by default.
    """
    import math

    th = math.radians(rotation_deg)
    # Rotate the (image-frame) drift into stage axes, then counter it.
    rx = drift_x_m * math.cos(th) - drift_y_m * math.sin(th)
    ry = drift_x_m * math.sin(th) + drift_y_m * math.cos(th)
    return sign_x * rx, sign_y * ry
