"""Measure the FIB beam-shift convention with cross-correlation.

WHY THIS EXISTS
---------------
Beam-shift signs have now been wrong three times, each time discovered on the
instrument (once by burning the sample). Signs cannot be derived reliably from
first principles: they depend on the column, the scan rotation and the image
orientation. So stop asserting them and MEASURE them.

The procedure is simple and self-verifying:

    1. grab a baseline FIB image
    2. apply a known beam shift on one axis (default 2 um)
    3. grab again, cross-correlate against the baseline
    4. the measured pixel displacement gives BOTH:
         * the SIGN   -- which way the image moves for a positive shift
         * the SCALE  -- pixels per metre, i.e. an independent check of the
                         magnification -> pixel-size calibration
    5. restore the original beam shift

Definitions (used consistently everywhere):

    R  ("response")  = feature displacement on screen per unit of applied beam
                       shift, normalised to +/-1.
                       R = +1  ->  +beam shift moves features RIGHT / DOWN
                       R = -1  ->  +beam shift moves features LEFT  / UP

    BEAM_SIGN = -R   is the correction sign: the beam-shift delta that NULLS a
                     measured drift is  delta = BEAM_SIGN * drift.

The same R also determines the safe raster side for the autofocus fork:
    R = +1 -> a +X park puts the target RIGHT -> raster the LEFT
    R = -1 -> a +X park puts the target LEFT  -> raster the RIGHT
so one calibration run resolves the convention for BOTH modules.

DOSE: this grabs 3+ FIB frames. Run it on a sacrificial area.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from .geometry import BEAM_SHIFT_MAX_M, pixel_size_m
from .registration import phase_cross_correlation
from .sources import RefineScope

DEFAULT_CALIBRATION_FILENAME = "refine_calibration.json"


@dataclass
class AxisCalibration:
    """Measured response of one beam-shift axis."""

    axis: str                 # 'x' | 'y'
    applied_shift_m: float
    dx_px: float              # measured image displacement, +x = right
    dy_px: float              # measured image displacement, +y = down
    response: float           # +1 or -1 (normalised, along the driven axis)
    response_raw: float       # unrounded: (displacement along axis) / expected
    pixel_size_m: float       # measured from |displacement| and applied shift
    crosstalk_px: float       # displacement on the OTHER axis (should be ~0)
    snr: float
    ok: bool
    note: str = ""

    @property
    def correction_sign(self) -> float:
        """Sign to multiply a measured drift by to get the nulling beam delta."""
        return -self.response


@dataclass
class BeamShiftCalibration:
    magnification: float
    image_width_px: int
    nominal_pixel_size_m: float
    axes: Dict[str, AxisCalibration] = field(default_factory=dict)
    created_unix_s: float = field(default_factory=time.time)
    note: str = ""

    # -- derived ---------------------------------------------------------
    @property
    def beam_sign_x(self) -> Optional[float]:
        a = self.axes.get("x")
        return a.correction_sign if a and a.ok else None

    @property
    def beam_sign_y(self) -> Optional[float]:
        a = self.axes.get("y")
        return a.correction_sign if a and a.ok else None

    @property
    def safe_raster_side_for_positive_x_park(self) -> Optional[str]:
        """Which side the autofocus fork should raster for a +X beam park."""
        a = self.axes.get("x")
        if not a or not a.ok:
            return None
        # R=+1 -> target appears RIGHT -> raster LEFT; R=-1 -> raster RIGHT.
        return "left" if a.response > 0 else "right"

    @property
    def scale_error_pct(self) -> Optional[float]:
        """How far the measured pixel size is from the nominal calibration."""
        vals = [a.pixel_size_m for a in self.axes.values() if a.ok]
        if not vals:
            return None
        measured = sum(vals) / len(vals)
        return (measured - self.nominal_pixel_size_m) / self.nominal_pixel_size_m * 100.0

    # -- persistence -----------------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        d["derived"] = {
            "beam_sign_x": self.beam_sign_x,
            "beam_sign_y": self.beam_sign_y,
            "safe_raster_side_for_positive_x_park":
                self.safe_raster_side_for_positive_x_park,
            "scale_error_pct": self.scale_error_pct,
        }
        return d

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @staticmethod
    def load(path: Path) -> "BeamShiftCalibration":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        d.pop("derived", None)
        axes = {k: AxisCalibration(**v) for k, v in d.pop("axes", {}).items()}
        return BeamShiftCalibration(axes=axes, **d)

    def summary(self) -> str:
        lines = ["Beam-shift calibration",
                 f"  magnification    : {self.magnification:.6g}x",
                 f"  nominal px size  : {self.nominal_pixel_size_m * 1e9:.2f} nm"]
        for ax in ("x", "y"):
            a = self.axes.get(ax)
            if a is None:
                continue
            if not a.ok:
                lines.append(f"  {ax.upper()}: FAILED -- {a.note}")
                continue
            direction = {("x", 1.0): "RIGHT", ("x", -1.0): "LEFT",
                         ("y", 1.0): "DOWN", ("y", -1.0): "UP"}[(ax, a.response)]
            lines += [
                f"  {ax.upper()} axis:",
                f"     applied        : {a.applied_shift_m * 1e6:+.2f} um",
                f"     image moved    : dx={a.dx_px:+.1f} px  dy={a.dy_px:+.1f} px",
                f"     => +shift moves the image {direction}   (response {a.response:+.0f})",
                f"     => correction sign to use : {a.correction_sign:+.0f}",
                f"     measured px size: {a.pixel_size_m * 1e9:.2f} nm "
                f"({(a.pixel_size_m / self.nominal_pixel_size_m - 1) * 100:+.1f}% vs nominal)",
                f"     crosstalk       : {a.crosstalk_px:+.1f} px   snr={a.snr:.1f}",
            ]
        side = self.safe_raster_side_for_positive_x_park
        if side:
            lines += ["",
                      f"  Autofocus implication: for a POSITIVE X beam park, the area of",
                      f"  interest appears on the {'RIGHT' if side == 'left' else 'LEFT'}, "
                      f"so the safe raster side is the {side.upper()}."]
        return "\n".join(lines)


def _measure_axis(
    scope: RefineScope,
    axis: str,
    magnification: float,
    baseline_img,
    base_shift: Tuple[float, float],
    test_shift_m: float,
    crop_bar: bool,
    min_snr: float,
    log: Callable[[str], None],
) -> AxisCalibration:
    bx0, by0 = base_shift
    if axis == "x":
        target = (bx0 + test_shift_m, by0)
    else:
        target = (bx0, by0 + test_shift_m)

    # Refuse to drive past the instrument limit.
    if max(abs(target[0]), abs(target[1])) > BEAM_SHIFT_MAX_M:
        return AxisCalibration(axis=axis, applied_shift_m=test_shift_m, dx_px=0.0,
                               dy_px=0.0, response=0.0, response_raw=0.0,
                               pixel_size_m=0.0, crosstalk_px=0.0, snr=0.0, ok=False,
                               note=(f"test shift would exceed the "
                                     f"{BEAM_SHIFT_MAX_M * 1e6:.0f} um beam-shift limit"))

    log(f"[calibrate] {axis.upper()}: applying {test_shift_m * 1e6:+.2f} um "
        f"-> beam ({target[0] * 1e6:+.2f}, {target[1] * 1e6:+.2f}) um")
    scope.set_beam_shift(*target)
    moved = scope.grab_fib_image(magnification)
    reg = phase_cross_correlation(baseline_img, moved, crop_bar=crop_bar)

    along = reg.dx_px if axis == "x" else reg.dy_px
    cross = reg.dy_px if axis == "x" else reg.dx_px
    nominal_px = pixel_size_m(magnification, reg.shape[1])
    expected_px = test_shift_m / nominal_px

    ok = True
    note = ""
    if reg.snr < min_snr:
        ok, note = False, f"correlation snr {reg.snr:.1f} < {min_snr:.1f}"
    elif abs(along) < 2.0:
        ok, note = False, (f"image barely moved ({along:.1f} px); increase "
                           f"--test-shift or check the beam shift took effect")

    response_raw = (along / expected_px) if expected_px else 0.0
    response = 1.0 if response_raw >= 0 else -1.0
    measured_px_size = (abs(test_shift_m) / abs(along)) if abs(along) > 1e-9 else 0.0

    # A large cross-axis term means the image is rotated relative to the beam-shift
    # axes (scan rotation), which the simple per-axis model does not capture.
    if ok and abs(cross) > 0.35 * abs(along):
        note = (f"large cross-axis motion ({cross:+.1f} px vs {along:+.1f} px): the "
                f"image may be rotated relative to the beam-shift axes")

    return AxisCalibration(axis=axis, applied_shift_m=test_shift_m,
                           dx_px=reg.dx_px, dy_px=reg.dy_px, response=response,
                           response_raw=response_raw, pixel_size_m=measured_px_size,
                           crosstalk_px=cross, snr=reg.snr, ok=ok, note=note)


def calibrate_beam_shift(
    scope: RefineScope,
    magnification: float,
    *,
    test_shift_m: float = 2.0e-6,
    axes: Tuple[str, ...] = ("x", "y"),
    crop_bar: bool = True,
    min_snr: float = 4.0,
    logger: Optional[Callable[[str], None]] = None,
) -> BeamShiftCalibration:
    """Measure the beam-shift sign and scale on each axis.

    Always restores the original beam shift, including on error.
    """
    log = logger or (lambda m: None)
    bx0, by0 = scope.get_beam_shift()
    log(f"[calibrate] baseline beam shift = ({bx0 * 1e6:+.2f}, {by0 * 1e6:+.2f}) um")

    cal = BeamShiftCalibration(
        magnification=float(magnification),
        image_width_px=0,
        nominal_pixel_size_m=0.0,
    )
    try:
        baseline = scope.grab_fib_image(magnification)
        # Width after databar crop is what registration actually works on.
        probe = phase_cross_correlation(baseline, baseline, crop_bar=crop_bar)
        cal.image_width_px = probe.shape[1]
        cal.nominal_pixel_size_m = pixel_size_m(magnification, probe.shape[1])

        for axis in axes:
            cal.axes[axis] = _measure_axis(scope, axis, magnification, baseline,
                                           (bx0, by0), test_shift_m, crop_bar,
                                           min_snr, log)
            scope.set_beam_shift(bx0, by0)   # back to baseline between axes
    finally:
        scope.set_beam_shift(bx0, by0)
        log(f"[calibrate] restored beam shift to "
            f"({bx0 * 1e6:+.2f}, {by0 * 1e6:+.2f}) um")
    return cal


def load_calibrated_signs(path: Optional[Path] = None) -> Optional[Tuple[float, float]]:
    """Return (beam_sign_x, beam_sign_y) from a saved calibration, or None."""
    p = Path(path) if path else Path(DEFAULT_CALIBRATION_FILENAME)
    if not p.exists():
        return None
    try:
        cal = BeamShiftCalibration.load(p)
    except Exception:
        return None
    sx, sy = cal.beam_sign_x, cal.beam_sign_y
    if sx is None or sy is None:
        return None
    return float(sx), float(sy)
