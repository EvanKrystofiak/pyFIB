"""Hard limits, sanity checks and the no-surprises SmartSEM wrapper for SEM autofocus.

Everything here is either a constant, a pure function, or ``GuardedSem`` -- an
allow-list wrapper that makes the fork's safety properties structural:

  * the ONLY parameters it can set are AP_WD (SEM focus), AP_MAG (SEM mag, only when
    the operator asks), DP_FREEZE_ON and the reduced-raster box (AP_RED_RASTER_*);
  * the ONLY commands it can execute are the SEM imaging switch, freeze/unfreeze,
    reduced/normal raster and CMD_SCANRATE<n>;
  * AP_WD is refused outside the 4-8 mm window AT THE MOMENT OF THE SET, whatever
    the caller computed.

So this fork cannot move the stage, cannot touch contrast/brightness, and cannot run
SmartSEM's own CMD_AUTO_FOCUS_* (the routine that sometimes leaves contrast at 0,
bug list #3). Any attempt raises SemAutofocusSafetyError.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

import numpy as np

# --------------------------------------------------------------------------- #
# Limits (operator-stated or conservative; see the notes)
# --------------------------------------------------------------------------- #
# Operator (2026-09-30): "WD is likely to be between 4-8 mm only." A reading or a
# target outside this window is treated as nonsense: refuse / never set.
WD_MIN_M = 4.0e-3
WD_MAX_M = 8.0e-3

# Sweep geometry -- MEASURED 2026-10-01 (2 kV, 2000x, cryo sample): the focus peak is a
# sharp cusp; the sharpness falls to ~half within ~20-30 um. 25 um steps (v0.37) were
# too coarse; +/-60 um in 13 steps (10 um) resolved it (curve contrast 0.71, focus
# within 0.8 um of the full-frame result). The hard cap stops a typo (1000 for 100).
HALF_RANGE_DEFAULT_M = 60e-6
HALF_RANGE_MAX_M = 1.0e-3
COARSE_POINTS_DEFAULT = 13
FINE_POINTS_DEFAULT = 5
# Fine sweep: by default +/- ONE coarse step around the best coarse point, so the new
# samples fall halfway between coarse samples (the v0.37 fraction of the half-range
# landed ~0.5 um from coarse points: repeats, not new information).
FINE_FRACTION_DEFAULT = None
MAX_FRAMES_DEFAULT = 25
# Scan speed for focus frames (restored afterwards). MEASURED: at speed 5 the frames were
# noise-dominated and the curve too flat (0.24); at 8 the curve contrast was 0.71.
# AP_FRAME_TIME doubles per speed step (1301 ms at 5, 5268 ms at 7, full frame).
SCAN_RATE_DEFAULT = 8

# AP_WD read-back must match what was set (SmartSEM reports WD to ~1 um). A larger
# mismatch means the set did not take (or went somewhere else): abort + restore.
# NOT YET INSTRUMENT-CONFIRMED: the first armed run logs every set/read-back pair.
WD_READBACK_TOL_M = 2.0e-6
# Before the sweep, one tiny WD step (no image) proves the set/read-back and that the
# stage does not follow WD, on a few-um move instead of the first 100-um sweep step.
PROBE_STEP_M = 10.0e-6        # > 5x the stage-Z tolerance, so a Z/WD link cannot hide in it

EHT_MIN_ON_V = 100.0          # same threshold coincidence uses for "SEM beam on"
CONTRAST_MIN_PCT = 1.0        # AP_CONTRAST below this = the "contrast 0" failure: refuse
BC_CHANGE_TOL_PCT = 0.05      # contrast/brightness may not move during the run (we never set them)
MAG_REL_TOL = 0.01            # AP_MAG read-back / "unchanged" tolerance
MAG_MIN, MAG_MAX = 20.0, 1.0e6
STAGE_Z_TOL_M = 2.0e-6        # a WD change must never move the stage (e.g. a Z-WD link)

# Frame sanity (grey levels, after the databar is cropped).
FRAME_MIN_STD = 2.0           # flatter than this = blank (contrast 0, beam blanked, no signal)
FRAME_DARK_LEVEL = 2.0
FRAME_BRIGHT_LEVEL = 253.0
FRAME_CLIP_FRAC_MAX = 0.5     # > half the pixels black or white = not a usable image

# Imaging states the run may START from (normalised names; anything else -- milling,
# drift, external, "None", an empty read -- is refused). The run then switches to SEM.
START_IMAGING_OK = frozenset({"sem", "fib", "sem+fib"})
# Noise reduction that keeps each frame independent of the previous WD step. Frame /
# continuous averaging blends frames across WD steps; frame/line integration freezes on
# its own. Those are refused (switch to Line Avg or Pixel Avg for the focus run).
NOISE_REDUCTION_OK = frozenset({"pixel avg", "line avg"})
UNFREEZE_LIVE_TIMEOUT_S = 10.0   # after CMD_UNFREEZE_ZONE, DP_FROZEN must read Live within this
FINE_FRACTION_MAX = 1.0          # fine sweep never wider than the coarse half-range
PROBE_STEP_MAX_M = 50.0e-6

# Curve / result confidence.
# A featureless image still gives a noisy "curve": on the simulator its contrast reached
# 0.07 (full frame), 0.12 (half box) and 0.17 (third box). 0.3 keeps >2x margin over the
# half box. MEASURED 2026-10-01 at scan speed 8: real curves 0.71 (box) and 0.60 (full
# frame); at scan speed 5 a real but noisy curve was 0.24 and was (correctly) rejected.
MIN_CURVE_CONTRAST = 0.3
VERIFY_MIN_RATIO = 0.6        # a frame at the chosen WD must score >= 60% of the sweep peak

# Reduced raster (v0.37.1): only a centred box is scanned -> much shorter frames. Same
# mechanism FIB autofocus uses on the instrument: AP_RED_RASTER_* in SCREEN pixels on the
# 1024x768 screen grid, then CMD_MODE_REDUCED; CMD_MODE_NORMAL on every exit path.
SCREEN_SIZE = (1024, 768)
RASTER_MNS = ("AP_RED_RASTER_POSN_X", "AP_RED_RASTER_POSN_Y",
              "AP_RED_RASTER_W", "AP_RED_RASTER_H")
RASTER_FRAC_DEFAULT = 0.5         # box = half the width and height: 1/4 of the area, ~4x
                                  # shorter frames. Smaller boxes gain little (the WD settle
                                  # dominates) and raise the metric's noise floor (below).
RASTER_FRAC_MIN = 0.1             # smaller boxes hold too few features to score

ALLOWED_SETS = frozenset({"AP_WD", "AP_MAG", "DP_FREEZE_ON"} | set(RASTER_MNS))
ALLOWED_CMDS = frozenset(
    {"CMD_FIB_MODE_SEM", "CMD_UNFREEZE_ZONE", "CMD_FREEZE_ZONE", "CMD_FREEZE_ALL",
     "CMD_MODE_REDUCED", "CMD_MODE_NORMAL"}
    | {f"CMD_SCANRATE{n}" for n in range(1, 16)}
)


def centred_box(frac: float, screen: Tuple[int, int] = SCREEN_SIZE) -> Tuple[int, int, int, int]:
    """(x, y, w, h) in screen pixels: a box of ``frac`` x the screen, centred. The centre
    of a 1024x768 screen sits well above the databar (bottom ~9%)."""
    if not (RASTER_FRAC_MIN <= float(frac) < 1.0):
        raise ValueError(f"raster fraction {frac} must be in [{RASTER_FRAC_MIN}, 1)")
    sw, sh = screen
    w = int(round(sw * frac)) // 2 * 2
    h = int(round(sh * frac)) // 2 * 2
    return ((sw - w) // 2, (sh - h) // 2, w, h)


class SemAutofocusSafetyError(RuntimeError):
    """The fork was asked to do something it is not allowed to do."""


def wd_in_window(wd_m: Optional[float], window: Tuple[float, float] = (WD_MIN_M, WD_MAX_M)
                 ) -> bool:
    if wd_m is None:
        return False
    try:
        wd = float(wd_m)
    except (TypeError, ValueError):
        return False
    return math.isfinite(wd) and window[0] <= wd <= window[1]


def norm(s: Optional[str]) -> Optional[str]:
    """'Pixel Avg.' -> 'pixel avg'; 'SEM + FIB' -> 'sem+fib'. None stays None."""
    if s is None:
        return None
    t = " ".join(str(s).replace(".", " ").split()).lower()
    return t.replace(" + ", "+").replace(" +", "+").replace("+ ", "+")


def mm(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{float(v) * 1e3:.4f} mm"


def frame_sanity(img: np.ndarray, crop_bar: bool = True) -> Optional[str]:
    """None if the frame looks like a real image, else a reason string.

    Catches the frames that would make a focus score meaningless: flat (contrast 0,
    blanked beam, detector off), mostly black, or mostly saturated. The databar is
    cropped first so its static text cannot make a blank frame look textured.
    """
    from ..autofocus import metrics as _metrics
    a = np.asarray(img, dtype=np.float64)
    if a.ndim != 2 or a.shape[0] < 8 or a.shape[1] < 8:
        return f"frame has an unusable shape {a.shape}"
    if crop_bar:
        try:
            a = _metrics.crop_databar(a)
        except Exception:
            pass
    if not np.all(np.isfinite(a)):
        return "frame contains non-finite pixels"
    std = float(a.std())
    mean = float(a.mean())
    dark = float(np.mean(a <= FRAME_DARK_LEVEL))
    bright = float(np.mean(a >= FRAME_BRIGHT_LEVEL))
    if std < FRAME_MIN_STD:
        return (f"blank frame (std {std:.2f} grey levels, mean {mean:.1f}): contrast 0, "
                f"beam blanked or no detector signal")
    if dark > FRAME_CLIP_FRAC_MAX:
        return f"frame is {dark:.0%} black (mean {mean:.1f}): brightness/contrast far too low"
    if bright > FRAME_CLIP_FRAC_MAX:
        return f"frame is {bright:.0%} saturated (mean {mean:.1f}): brightness/contrast too high"
    return None


# --------------------------------------------------------------------------- #
# Allow-list wrapper around the real SmartSEM object
# --------------------------------------------------------------------------- #
class GuardedSem:
    """Read anything; set only AP_WD / AP_MAG / DP_FREEZE_ON; run only imaging commands.

    AP_WD and AP_MAG are range-checked here, at the moment of the set.
    """

    def __init__(self, sem, *, wd_window: Tuple[float, float] = (WD_MIN_M, WD_MAX_M)):
        # A caller may NARROW the window, never widen it past 4-8 mm.
        lo = max(WD_MIN_M, float(wd_window[0]))
        hi = min(WD_MAX_M, float(wd_window[1]))
        if not (math.isfinite(lo) and math.isfinite(hi) and lo < hi):
            raise SemAutofocusSafetyError(f"invalid WD window {wd_window!r}")
        object.__setattr__(self, "_sem", sem)
        object.__setattr__(self, "_wd_window", (lo, hi))
        object.__setattr__(self, "calls", [])

    @property
    def wd_window(self) -> Tuple[float, float]:
        return object.__getattribute__(self, "_wd_window")

    def _raw(self):
        return object.__getattribute__(self, "_sem")

    def _log(self, *entry) -> None:
        object.__getattribute__(self, "calls").append(entry)

    # --- reads: always allowed ---
    def get_float(self, mnemonic: str) -> float:
        return self._raw().get_float(mnemonic)

    def get_str(self, mnemonic: str) -> str:
        return self._raw().get_str(mnemonic)

    # --- sets: allow-listed and range-checked ---
    def set_value(self, mnemonic: str, value) -> None:
        if mnemonic not in ALLOWED_SETS:
            raise SemAutofocusSafetyError(
                f"SEM autofocus may not set {mnemonic!r} (allowed: {sorted(ALLOWED_SETS)}). "
                f"It never changes contrast, brightness, the stage or the FIB.")
        if mnemonic == "AP_WD":
            lo, hi = object.__getattribute__(self, "_wd_window")
            if not wd_in_window(value, (lo, hi)):
                raise SemAutofocusSafetyError(
                    f"refusing to set AP_WD = {value!r} m: outside the "
                    f"{lo * 1e3:.1f}-{hi * 1e3:.1f} mm window.")
            value = float(value)                # forward exactly what was checked
        if mnemonic == "AP_MAG":
            v = float(value)
            if not (math.isfinite(v) and MAG_MIN <= v <= MAG_MAX):
                raise SemAutofocusSafetyError(f"refusing to set AP_MAG = {value!r}.")
            value = v
        if mnemonic in RASTER_MNS:
            v = float(value)
            lim = SCREEN_SIZE[0] if mnemonic.endswith(("_X", "_W")) else SCREEN_SIZE[1]
            if not (math.isfinite(v) and 0 <= v <= lim):
                raise SemAutofocusSafetyError(f"refusing to set {mnemonic} = {value!r}.")
            value = v
        self._log("set_value", mnemonic, value)
        self._raw().set_value(mnemonic, value)

    # --- commands: allow-listed ---
    def execute(self, command: str) -> None:
        if command not in ALLOWED_CMDS:
            raise SemAutofocusSafetyError(
                f"SEM autofocus may not execute {command!r}. It only switches to SEM "
                f"imaging, freezes/unfreezes and sets the scan rate -- it never runs "
                f"SmartSEM's CMD_AUTO_FOCUS_* or moves anything.")
        self._log("execute", command)
        self._raw().execute(command)

    # --- image grab: allowed (no motion) ---
    def grab_bmp(self, path, **kwargs):
        self._log("grab_bmp", str(path))
        return self._raw().grab_bmp(path, **kwargs)

    # --- everything else is blocked ---
    def __getattr__(self, name):
        raise SemAutofocusSafetyError(
            f"'{name}' is not available to SEM autofocus (read, set WD/mag, image only).")

    def __setattr__(self, name, value):
        raise SemAutofocusSafetyError(f"setting attribute '{name}' on GuardedSem is not allowed.")
