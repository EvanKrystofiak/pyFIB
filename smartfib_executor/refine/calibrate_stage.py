"""Measure the STAGE <-> image sign convention for refine's stage correction.

Why this exists: refine's stage-correction path was written with PLACEHOLDER signs
(geometry.STAGE_SIGN_X/Y = -1, rotation 0, all marked UNVERIFIED) and kept disabled.
A position correction is a feedback loop -- a wrong sign DOUBLES the error -- so the
sign must be MEASURED on the instrument before the stage is ever driven by refine,
exactly as the beam-shift sign was (calibrate.py).

Method (per axis, X then Y):
  1. grab a baseline FIB frame at the working magnification;
  2. command a small, known stage step on that axis ONLY (default 20 um) -- other
     axes are held at their current values by the adapter;
  3. wait for arrival, read the ACHIEVED step from the stage read-back (mechanical
     stages do not land exactly -- we measure what actually happened, not what was
     asked);
  4. grab a frame and cross-correlate against the baseline -> image drift (px);
  5. sign = direction of image motion per +stage step; scale = achieved step / px
     moved (cross-checks the pixel size); cross-axis motion flags rotation;
  6. move the stage back to where it started and confirm arrival.

The correction sign that NULLS a measured drift is  STAGE_SIGN = -R  (R = response),
mirroring BEAM_SIGN = -R in calibrate.py.

SCOPE / SAFETY: these limits are for REFINE ONLY. Recall, setup moves, prep_cryo and
coincidence have their own logic and are untouched. The probe commands X/Y only,
refuses any step above the refine stage cap, and always restores the start position.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from math import hypot
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from .geometry import pixel_size_m
from .registration import phase_cross_correlation
from .sources import RefineScope

# Refine-only stage cap lives in geometry (shared with the correction itself).
from .geometry import REFINE_STAGE_MAX_M, stage_scale

# Image offset (baseline vs return-to-start frame) above which the axis's scale is
# flagged as possibly contaminated by drift/backlash. 0.5 um ~ 9 px at 2000x, ~2.5%
# of a 20 um step.
RETURN_OFFSET_WARN_M = 0.5e-6
DEFAULT_STAGE_TEST_STEP_M = 20.0e-6  # operator: start at 20 um, may need more

DEFAULT_STAGE_CALIBRATION_FILENAME = "refine_stage_calibration.json"


@dataclass
class StageAxisCalibration:
    axis: str
    commanded_step_m: float
    achieved_step_m: float           # from the stage read-back (mechanical error!)
    dx_px: float
    dy_px: float
    response: float                  # +1 / -1: image moves with (+) or against (-) +stage
    response_raw: float              # along_px / expected_px (from the ACHIEVED step)
    measured_pixel_size_m: float     # achieved_step / |along_px|
    crosstalk_px: float              # motion on the other image axis (rotation hint)
    snr: float
    ok: bool
    note: str = ""
    # Image offset between this axis's baseline and a frame grabbed after returning
    # to the start position (drift, a settling jump, or backlash). The step is
    # measured against the baseline, so any such offset contaminates the scale.
    return_dx_px: float = 0.0
    return_dy_px: float = 0.0
    return_offset_m: float = 0.0


@dataclass
class StageCalibration:
    magnification: float
    image_width_px: int
    nominal_pixel_size_m: float
    axes: Dict[str, StageAxisCalibration] = field(default_factory=dict)
    # The stage tilt the calibration was taken at. The stage->image scale is a
    # projection effect that depends on tilt, so a calibration is only valid at
    # the tilt it was measured at.
    tilt_deg: Optional[float] = None
    rot_deg: Optional[float] = None

    @property
    def ok(self) -> bool:
        return bool(self.axes) and all(a.ok for a in self.axes.values())

    def correction_signs(self) -> Optional[Tuple[float, float]]:
        """(STAGE_SIGN_X, STAGE_SIGN_Y) = -response, or None if not both OK."""
        if not self.ok or "x" not in self.axes or "y" not in self.axes:
            return None
        return (-self.axes["x"].response, -self.axes["y"].response)

    def rotation_hint_deg(self) -> Optional[float]:
        """Apparent rotation of the image relative to stage axes, from cross-talk."""
        import math
        if "x" not in self.axes or not self.axes["x"].ok:
            return None
        ax = self.axes["x"]
        if abs(ax.dx_px) < 1e-9:
            return None
        return math.degrees(math.atan2(ax.dy_px, ax.dx_px))

    def to_dict(self) -> dict:
        d = {"magnification": self.magnification, "image_width_px": self.image_width_px,
             "nominal_pixel_size_m": self.nominal_pixel_size_m,
             "tilt_deg": self.tilt_deg, "rot_deg": self.rot_deg,
             "axes": {k: asdict(v) for k, v in self.axes.items()}}
        s = self.correction_signs()
        d["correction_signs"] = list(s) if s else None
        d["rotation_hint_deg"] = self.rotation_hint_deg()
        return d

    def save(self, path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")


def _measure_stage_axis(scope: RefineScope, axis: str, magnification: float,
                        baseline_img, start_xy: Tuple[float, float], step_m: float,
                        crop_bar: bool, min_snr: float,
                        log: Callable[[str], None],
                        expected_scale: Optional[float] = None) -> StageAxisCalibration:
    sx0, sy0 = start_xy
    if abs(step_m) > REFINE_STAGE_MAX_M:
        return StageAxisCalibration(axis, step_m, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                                    False, f"test step exceeds the refine stage cap "
                                           f"({REFINE_STAGE_MAX_M * 1e6:.0f} um)")
    target = (sx0 + step_m, sy0) if axis == "x" else (sx0, sy0 + step_m)
    log(f"[calibrate-stage] {axis.upper()}: stage step {step_m * 1e6:+.1f} um "
        f"(X/Y only; other axes held)")
    scope.move_stage_xy(*target)          # adapter holds Z/T/R/M and waits arrival
    ax, ay = scope.get_stage_xy()
    achieved = (ax - sx0) if axis == "x" else (ay - sy0)
    log(f"[calibrate-stage] {axis.upper()}: achieved {achieved * 1e6:+.2f} um "
        f"(commanded {step_m * 1e6:+.1f})")
    moved = scope.grab_fib_image(magnification)
    reg = phase_cross_correlation(baseline_img, moved, crop_bar=crop_bar)
    along = reg.dx_px if axis == "x" else reg.dy_px
    cross = reg.dy_px if axis == "x" else reg.dx_px
    nominal_px = pixel_size_m(magnification, reg.shape[1])
    expected_px = (achieved / nominal_px) if nominal_px else 0.0
    ok, note = True, ""
    if abs(achieved) < 0.25 * abs(step_m):
        ok, note = False, (f"stage barely moved ({achieved * 1e6:+.2f} um of "
                           f"{step_m * 1e6:+.1f}); check the move took effect")
    elif reg.snr < min_snr:
        ok, note = False, f"correlation snr {reg.snr:.1f} < {min_snr:.1f}"
    elif abs(along) < 2.0:
        ok, note = False, (f"image barely moved ({along:.1f} px); increase "
                           f"--stage-test-step or check the correlation")
    response_raw = (along / expected_px) if expected_px else 0.0
    response = 1.0 if response_raw >= 0 else -1.0
    measured_px = (abs(achieved) / abs(along)) if abs(along) > 1e-9 else 0.0
    if ok and abs(cross) > 0.35 * abs(along):
        note = (f"large cross-axis motion ({cross:+.1f} px vs {along:+.1f} px): the "
                f"image is rotated relative to the stage axes -- a rotation term is "
                f"needed before stage correction is safe")
    if ok and expected_scale:
        # Compare with the TILT MODEL (foreshortening is expected, not a fault).
        dev = abs(response_raw) / expected_scale - 1.0
        if abs(dev) > 0.10:
            note = (note + "; " if note else "") + (
                f"scale {abs(response_raw):.3f} differs from the tilt model "
                f"{expected_scale:.3f} by {dev * 100:+.0f}%: re-check before relying on "
                f"stage correction at this tilt")
    elif ok and measured_px and abs(measured_px / nominal_px - 1.0) > 0.25:
        note = (note + "; " if note else "") + (
            f"measured pixel size {measured_px * 1e9:.1f} nm vs nominal "
            f"{nominal_px * 1e9:.1f} nm (>25% off): stage scale or pixel-size "
            f"calibration is suspect")
    return StageAxisCalibration(axis, step_m, achieved, reg.dx_px, reg.dy_px, response,
                                response_raw, measured_px, cross, reg.snr, ok, note)


def calibrate_stage(scope: RefineScope, magnification: float, *,
                    test_step_m: float = DEFAULT_STAGE_TEST_STEP_M,
                    axes: Tuple[str, ...] = ("x", "y"), crop_bar: bool = True,
                    min_snr: float = 4.0,
                    logger: Optional[Callable[[str], None]] = None) -> StageCalibration:
    """Measure the stage sign/scale on each axis. ALWAYS restores the start position
    (and confirms arrival), including on error."""
    log = logger or (lambda m: None)
    sx0, sy0 = scope.get_stage_xy()
    log(f"[calibrate-stage] start stage XY = ({sx0 * 1e3:.4f}, {sy0 * 1e3:.4f}) mm; "
        f"step {test_step_m * 1e6:.1f} um; cap {REFINE_STAGE_MAX_M * 1e6:.0f} um")
    tilt = rot = None
    try:
        tilt, rot = scope.get_stage_tilt_rot()
        log(f"[calibrate-stage] stage tilt = {tilt:.3f} deg, rotation = {rot:.3f} deg "
            f"(the scale is tilt-dependent; R is fixed for cryo-milling)")
    except Exception:
        pass
    cal = StageCalibration(magnification=float(magnification), image_width_px=0,
                           nominal_pixel_size_m=0.0, tilt_deg=tilt, rot_deg=rot)
    try:
        baseline = scope.grab_fib_image(magnification)
        probe = phase_cross_correlation(baseline, baseline, crop_bar=crop_bar)
        cal.image_width_px = probe.shape[1]
        cal.nominal_pixel_size_m = pixel_size_m(magnification, probe.shape[1])
        for axis in axes:
            exp = None
            if tilt is not None:
                exp = stage_scale(tilt)[0 if axis == "x" else 1]
            cal.axes[axis] = _measure_stage_axis(scope, axis, magnification, baseline,
                                                 (sx0, sy0), test_step_m, crop_bar,
                                                 min_snr, log, expected_scale=exp)
            # Return to start and grab a RETURN frame (no extra stage motion -- the
            # probe returns here anyway). Its offset from the baseline measures drift /
            # jumps / backlash during this axis: the 2026-09-22 12-deg run carried a
            # ~1.2 um X offset that inflated X to 0.98 and put 25 px of X cross-talk on
            # the Y step. The return frame then becomes a FRESH baseline for the next
            # axis, so an offset on one axis cannot carry into the next.
            scope.move_stage_xy(sx0, sy0)
            a = cal.axes[axis]
            try:
                ret = scope.grab_fib_image(magnification)
                rr = phase_cross_correlation(baseline, ret, crop_bar=crop_bar)
                a.return_dx_px, a.return_dy_px = float(rr.dx_px), float(rr.dy_px)
                a.return_offset_m = float(hypot(rr.dx_px, rr.dy_px)) * cal.nominal_pixel_size_m
                log(f"[calibrate-stage] {axis.upper()}: return-to-start offset "
                    f"({rr.dx_px:+.1f}, {rr.dy_px:+.1f}) px = "
                    f"{a.return_offset_m * 1e6:.2f} um")
                if a.return_offset_m > RETURN_OFFSET_WARN_M:
                    a.note = (a.note + "; " if a.note else "") + (
                        f"image moved {a.return_offset_m * 1e6:.2f} um between the "
                        f"baseline and the return to start (drift/jump/backlash): this "
                        f"axis's SCALE may be contaminated -- repeat the calibration "
                        f"(the sign is still valid)")
                baseline = ret            # fresh baseline for the next axis
            except Exception as e:  # noqa: BLE001
                log(f"[calibrate-stage] {axis.upper()}: return frame failed ({e}); "
                    f"keeping the original baseline")
            log(f"[calibrate-stage] {axis.upper()}: image ({a.dx_px:+.1f}, "
                f"{a.dy_px:+.1f}) px, response {a.response:+.0f} "
                f"(raw {a.response_raw:+.2f}), px {a.measured_pixel_size_m * 1e9:.1f} nm, "
                f"snr {a.snr:.1f}, {'OK' if a.ok else 'FAIL'}{(' -- ' + a.note) if a.note else ''}")
    finally:
        # Restore, ALWAYS. If the restore itself fails, the operator must know
        # exactly where the stage should be -- a silent second exception here would
        # leave the stage displaced with no clear message.
        try:
            scope.move_stage_xy(sx0, sy0)
            log(f"[calibrate-stage] stage restored to start XY = "
                f"({sx0 * 1e3:.4f}, {sy0 * 1e3:.4f}) mm")
        except Exception as e:  # noqa: BLE001
            log(f"[calibrate-stage] *** RESTORE FAILED ({e}). The stage may NOT be at "
                f"its start position. Return it MANUALLY to X={sx0 * 1e3:.4f} mm, "
                f"Y={sy0 * 1e3:.4f} mm before continuing. ***")
    s = cal.correction_signs()
    rot = cal.rotation_hint_deg()
    if s:
        log(f"[calibrate-stage] RESULT: STAGE_SIGN_X = {s[0]:+.0f}, STAGE_SIGN_Y = "
            f"{s[1]:+.0f}" + (f", rotation hint {rot:+.1f} deg" if rot is not None else ""))
    else:
        log("[calibrate-stage] RESULT: calibration incomplete -- see per-axis notes.")
    drifted = [k.upper() for k, a in cal.axes.items()
               if a.return_offset_m > RETURN_OFFSET_WARN_M]
    if drifted:
        log(f"[calibrate-stage] QUALITY: image drift/jump detected on {', '.join(drifted)} "
            f"-- the sign(s) are valid but the scale on that axis is not reliable; repeat.")
    return cal


def load_calibrated_stage(path: Optional[Path] = None):
    """(sign_x, sign_y, rotation_hint_deg) from a saved calibration, or None."""
    p = Path(path) if path else Path(DEFAULT_STAGE_CALIBRATION_FILENAME)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        s = d.get("correction_signs")
        if not s:
            return None
        return float(s[0]), float(s[1]), d.get("rotation_hint_deg")
    except Exception:
        return None
