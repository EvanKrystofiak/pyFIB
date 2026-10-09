"""Image sources for FIB autofocus.

An *image source* is the thin boundary between the pure search logic and the
outside world. The search only needs three things from it:

    set_voltage(v)      # command the FIB objective voltage (volts)
    grab() -> ndarray   # acquire one image at the current voltage
    initial_voltage()   # last best-known focus to sweep around
    bounds()            # optional (vmin, vmax) safety clamp

Two implementations:
    * SimulatedImageSource -- deterministic, hardware-free. Sharpness peaks at a
      known voltage, so the whole search can be proven offline.
    * ScopeImageSource -- the real adapter over SmartSEM (smartsem_com.SmartSEM). It reuses the proven
      "ensure FIB imaging -> unfreeze -> grab BMP -> freeze (dose control)"
      sequence from positions.py. This class is the ONLY instrument-touching part
      and has NOT yet been validated on the microscope. pywin32/SmartSEM are
      imported lazily so this module (and the simulated path/tests) import fine on
      any machine.
"""

from __future__ import annotations

import os
import tempfile
import time
from typing import Optional, Tuple

import numpy as np

# Relative tolerance when verifying AP_FIB_MAGNIFICATION read-back (SmartFIB may
# report a marginally different value than was set).
MAG_VERIFY_REL_TOL = 0.01

try:  # Pillow is a project dependency; used to read grabbed BMPs and to blur sims.
    from PIL import Image, ImageFilter  # type: ignore
    _HAVE_PIL = True
except Exception:  # pragma: no cover
    _HAVE_PIL = False


class ImageSource:
    """Interface implemented by the simulator and the real scope adapter.

    ``prepare()`` / ``restore()`` bracket a focus run. The search calls them in a
    try/finally so any instrument state a source takes out (notably a beam shift
    off the area of interest) is ALWAYS put back -- on success, on failure, on
    exception, on Ctrl-C. Both default to no-ops.
    """

    def prepare(self) -> None:
        """Set up instrument state for a focus run. Must be idempotent."""

    def restore(self) -> None:
        """Undo whatever prepare() changed. Idempotent, and MUST NOT raise."""

    def set_voltage(self, voltage: float) -> None:
        raise NotImplementedError

    def grab(self) -> np.ndarray:
        raise NotImplementedError

    def initial_voltage(self) -> float:
        raise NotImplementedError

    def bounds(self) -> Optional[Tuple[float, float]]:
        return None


# --------------------------------------------------------------------------- #
# Simulated source (offline)
# --------------------------------------------------------------------------- #
class SimulatedImageSource(ImageSource):
    """A fake FIB whose image sharpness peaks at ``true_focus`` volts.

    On each grab, a fixed "feature" image is blurred by a Gaussian whose radius
    grows with the distance of the current voltage from ``true_focus``, then
    noise is added. This gives a single, well-behaved focus peak so the search
    algorithm can be tested without hardware.
    """

    def __init__(
        self,
        true_focus: float,
        *,
        start_voltage: Optional[float] = None,
        blur_per_volt: float = 0.02,  # Gaussian radius (px) added per volt of defocus.
                                      # Calibrated to real FIB data: the sharpness peak
                                      # spans roughly +/-150-200 V at 2000x, so ~150 V of
                                      # defocus corresponds to a few px of blur.
        base_blur: float = 0.4,       # residual blur at perfect focus
        noise_sigma: float = 10.4,    # additive Gaussian noise (grey levels); 10.4 is the
                                      # value measured on real slow-dwell FIB frames.
        size: Tuple[int, int] = (256, 256),
        bounds: Optional[Tuple[float, float]] = None,
        seed: int = 0,
    ) -> None:
        self.true_focus = float(true_focus)
        self._voltage = float(start_voltage if start_voltage is not None else true_focus)
        self._start_voltage = self._voltage
        self.blur_per_volt = float(blur_per_volt)
        self.base_blur = float(base_blur)
        self.noise_sigma = float(noise_sigma)
        self._bounds = bounds
        self._rng = np.random.default_rng(seed)
        self._feature = self._make_feature(size, seed)

    @staticmethod
    def _make_feature(size: Tuple[int, int], seed: int) -> np.ndarray:
        rng = np.random.default_rng(seed + 12345)
        h, w = size
        img = np.full((h, w), 40.0)
        # Scatter bright, high-contrast blobs (feature-like) to give the metrics
        # real edges to grade.
        for _ in range(60):
            cy = rng.integers(6, h - 6)
            cx = rng.integers(6, w - 6)
            r = int(rng.integers(2, 6))
            val = float(rng.integers(150, 256))
            img[cy - r : cy + r, cx - r : cx + r] = val
        return img

    def set_voltage(self, voltage: float) -> None:
        self._voltage = float(voltage)

    def initial_voltage(self) -> float:
        return self._start_voltage

    def bounds(self) -> Optional[Tuple[float, float]]:
        return self._bounds

    def grab(self) -> np.ndarray:
        radius = self.base_blur + self.blur_per_volt * abs(self._voltage - self.true_focus)
        base = self._feature
        if radius > 1e-6 and _HAVE_PIL:
            pil = Image.fromarray(base.astype(np.uint8))
            pil = pil.filter(ImageFilter.GaussianBlur(radius=float(radius)))
            blurred = np.asarray(pil, dtype=np.float64)
        else:
            blurred = base.copy()
        noise = self._rng.normal(0.0, self.noise_sigma, size=blurred.shape)
        out = np.clip(blurred + noise, 0.0, 255.0)
        return out


# --------------------------------------------------------------------------- #
# Real instrument source (NOT yet validated on the microscope)
# --------------------------------------------------------------------------- #
def _ensure_fib_imaging(sem, timeout_s: float = 60.0, poll_s: float = 0.25) -> None:
    """Put the column into FIB imaging, mirroring positions.py.

    NOTE: kept in sync deliberately with the capture flow in positions.py. At
    reintegration these should be unified into one shared helper.
    """

    def _img_state():
        try:
            return sem.get_str("DP_FIB_IMAGING").strip()
        except Exception:
            return None

    cur = _img_state()
    if cur is None or cur.upper() != "FIB":
        sem.execute("CMD_FIB_MODE_FIB")
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            s = _img_state()
            if s is not None and s.upper() == "FIB":
                break
            try:
                if int(round(float(sem.get_float("DP_FIB_IMAGING")))) == 1:
                    break
            except Exception:
                pass
            time.sleep(poll_s)
        else:
            raise TimeoutError(
                "Timed out waiting for DP_FIB_IMAGING=FIB after CMD_FIB_MODE_FIB"
            )


# Real-instrument timing, measured on the Crossbeam 550 at 2000x.
# The frame cycle time was ~1.3 s; these defaults add generous padding because a
# too-short wait silently returns the PREVIOUS frame instead of erroring.
DEFAULT_SETTLE_S = 1.0        # after changing objective voltage, before imaging
DEFAULT_FRAME_WAIT_S = 3.0    # scanning time before freezing -> guarantees a fresh frame.
                              # 5.0 was proven on the instrument; 3.0 confirmed OK there
                              # and is still >2x the measured ~1.3 s frame cycle time.

# Autofocus rasters the beam for tens of seconds and FIB imaging sputters, so we
# focus on a throwaway patch: shift the beam off target, focus, shift back.
# Magnification -> field-of-view calibration.
# Measured from the operator's own 2000x databar ("Image Pixel Size = 55.82 nm"
# over 1024 px => FOV width 57.2 um) and independently confirmed from the 10 um
# scale bar in BOTH store resolutions (184 px @1024 -> 54.4 nm/px; 91 px @512 ->
# 109.9 nm/px; same ~57 um field, as expected for one magnification).
# Hence: FOV_width_m ~= MAG_REF_WIDTH_M / magnification.
MAG_REF_WIDTH_M = 0.1143   # ~114 mm


# DP_IMAGE_STORE state -> (width, height) in pixels.
STORE_RESOLUTIONS = {0: (1024, 768), 1: (512, 384), 2: (2048, 1536), 3: (3072, 2304)}
DEFAULT_SCREEN_SIZE = (1024, 768)

_UNSET = object()   # sentinel: raster not yet resolved (None is a valid answer)


def target_screen_x(beam_shift_x_m: float, magnification: float,
                    screen_w: int = DEFAULT_SCREEN_SIZE[0],
                    pixel_size_m: Optional[float] = None) -> float:
    """Screen x (px) where the ORIGINAL target lands after a beam shift.

    SIGN CONVENTION -- MEASURED ON THE INSTRUMENT, NOT ASSUMED:
    a POSITIVE AP_FIB_BEAM_SHIFT_X makes the area of interest appear to the RIGHT
    of centre, so the safe side to raster is the LEFT. This was established
    empirically: pyFIB <= 0.6.0 assumed the opposite sign, chose the right side for
    a +12 um park, and BURNED THE SAMPLE. Do not "correct" this back without a
    fresh instrument observation.
        target_x = screen_w/2 + shift_px

    If ``pixel_size_m`` is given (the instrument's live value) it is used directly;
    otherwise pixels-per-metre is derived from the magnification constant.
    """
    if pixel_size_m and pixel_size_m > 0:
        px_per_m = 1.0 / pixel_size_m
    else:
        px_per_m = screen_w / fov_width_m(magnification)
    return screen_w / 2.0 + float(beam_shift_x_m) * px_per_m


# Shrink the rastered rectangle below the naive half-field: less dose, and more
# clearance between the raster edge and the area of interest. Operator-specified.
DEFAULT_RASTER_W_SCALE = 0.90   # reduce width by 10%
DEFAULT_RASTER_H_SCALE = 0.60   # reduce height by 40%


def raster_for_side(side: str, screen_size=DEFAULT_SCREEN_SIZE,
                    fraction: float = 0.5,
                    w_scale: float = DEFAULT_RASTER_W_SCALE,
                    h_scale: float = DEFAULT_RASTER_H_SCALE):
    """Reduced-raster (x, y, w, h) on one side of the field.

    The band is ``fraction`` of the field width, then scaled by ``w_scale`` /
    ``h_scale`` to cut dose. The width shrinks AWAY from the area of interest
    (the band stays anchored to the outer edge of the field), which widens the
    margin rather than narrowing it. The height is centred vertically -- the beam
    shift is in X only, so Y does not affect the target clearance.
    """
    w, h = screen_size
    band = max(1, int(round(w * float(fraction) * float(w_scale))))
    band = min(band, w)
    rh = max(1, int(round(h * float(h_scale))))
    rh = min(rh, h)
    y = max(0, (h - rh) // 2)
    if side == "left":
        return (0, y, band, rh)            # anchored to the LEFT edge
    if side == "right":
        return (w - band, y, band, rh)     # anchored to the RIGHT edge
    raise ValueError(f"side must be 'left' or 'right', got {side!r}")


def safe_raster_side(beam_shift_x_m: float, magnification: float,
                     screen_w: int = DEFAULT_SCREEN_SIZE[0],
                     pixel_size_m: Optional[float] = None) -> str:
    """Which side to scan so the raster does NOT cover the original target.

    With the measured convention, a POSITIVE shift -> target appears right of
    centre -> the LEFT side is safe. (Confirmed on the instrument.)
    """
    tx = target_screen_x(beam_shift_x_m, magnification, screen_w, pixel_size_m)
    # Target right of centre -> scan the left; and vice versa.
    return "left" if tx > screen_w / 2.0 else "right"


def fov_width_m(magnification: float) -> float:
    """Approximate FIB field-of-view width (metres) at a given magnification."""
    mag = float(magnification)
    if mag <= 0:
        raise ValueError("magnification must be > 0")
    return MAG_REF_WIDTH_M / mag


DEFAULT_FIB_MAGNIFICATION = 2.00e03   # same value the main workflow uses
DEFAULT_BEAM_SHIFT_X_M = 1.20e-05     # park: 12 um off the area of interest (metres).
                                      # Instrument maximum is 15 um; we stay off the rail.
BEAM_SHIFT_X_MAX_M = 1.50e-05         # hard instrument limit (operator-supplied)
DEFAULT_BEAM_SHIFT_X_RETURN_M = 0.00  # return here when autofocus finishes


def _is_frozen(sem) -> Optional[bool]:
    """True/False if DP_FROZEN is readable, else None."""
    try:
        v = sem.get_str("DP_FROZEN").strip().lower()
        if v in ("frozen", "1", "yes"):
            return True
        if v in ("live", "0", "no"):
            return False
    except Exception:
        pass
    try:
        return int(round(float(sem.get_float("DP_FROZEN")))) == 1
    except Exception:
        return None


def _wait_frozen(sem, timeout_s: float, poll_s: float = 0.2, log=None) -> None:
    """Block until DP_FROZEN reports Frozen (i.e. the frame finished).

    With DP_FREEZE_ON = End Frame, CMD_FREEZE_ZONE only takes effect at the end of
    the current frame, so the framestore holds a COMPLETE frame once frozen. If
    DP_FROZEN is unreadable this returns immediately -- the frame_wait padding is
    then the only guarantee.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        st = _is_frozen(sem)
        if st is None:
            return  # cannot read; rely on the time-based padding
        if st:
            return
        time.sleep(poll_s)
    if log:
        log(f"[autofocus] WARNING: DP_FROZEN did not report Frozen within "
            f"{timeout_s:.1f}s; the grabbed frame may be mid-scan.")


class ScopeImageSource(ImageSource):
    """Real FIB adapter over SmartSEM. Instrument-untested; handle with care.

    Sweeps AP_FIB_OBJECTIVE_POTENTIAL. Each grab ensures FIB imaging, unfreezes,
    grabs a BMP to a temp file, reloads it, then freezes again to limit ion dose.
    Voltage bounds must be supplied explicitly for safety (the SmartSEM wrapper
    does not yet expose GetLimits; adding that is a good reintegration follow-up).
    """

    def __init__(
        self,
        sem,
        *,
        start_voltage: Optional[float] = None,
        bounds: Tuple[float, float],
        settle_s: float = DEFAULT_SETTLE_S,
        frame_wait_s: float = DEFAULT_FRAME_WAIT_S,
        freeze_on_end_frame: bool = True,
        frozen_timeout_s: float = 15.0,
        fib_magnification: Optional[float] = DEFAULT_FIB_MAGNIFICATION,
        beam_shift_x_m: Optional[float] = DEFAULT_BEAM_SHIFT_X_M,
        beam_shift_x_return_m: float = DEFAULT_BEAM_SHIFT_X_RETURN_M,
        reduced_raster: Optional[Tuple[int, int, int, int]] = None,
        raster_side: Optional[str] = None,      # 'left' | 'right' | 'auto'
        raster_fraction: float = 0.5,
        raster_h_scale: float = DEFAULT_RASTER_H_SCALE,
        screen_size: Tuple[int, int] = DEFAULT_SCREEN_SIZE,
        roi: Optional[Tuple[int, int, int, int]] = None,  # (xoff, yoff, w, h)
        grab_reduction: int = 0,
        max_scan_rotation_deg: float = 0.5,
        strict_scan_rotation: bool = False,
        imaging_scan_rate: Optional[int] = None,
        scan_rate_method: str = "cmd",
        dry_run: bool = False,
        logger=None,
    ) -> None:
        self._sem = sem
        self._bounds = bounds
        self.settle_s = float(settle_s)
        self.frame_wait_s = float(frame_wait_s)
        self.freeze_on_end_frame = bool(freeze_on_end_frame)
        self.frozen_timeout_s = float(frozen_timeout_s)
        self.fib_magnification = fib_magnification
        self.reduced_raster = reduced_raster
        self.raster_side = raster_side
        self.raster_fraction = float(raster_fraction)
        self.raster_h_scale = float(raster_h_scale)
        self.screen_size = screen_size
        self._reduced_applied = False
        self._raster_cache = _UNSET
        self.beam_shift_x_m = beam_shift_x_m
        self.beam_shift_x_return_m = float(beam_shift_x_return_m)
        self._prepared = False
        self._beam_shift_applied = False
        self._pixel_size_m: Optional[float] = None   # filled live in prepare()
        self.roi = roi
        self.grab_reduction = int(grab_reduction)
        self.max_scan_rotation_deg = float(max_scan_rotation_deg)
        self.strict_scan_rotation = bool(strict_scan_rotation)
        self.imaging_scan_rate = imaging_scan_rate
        self.scan_rate_method = scan_rate_method
        self._orig_scan_rate: Optional[int] = None
        self.dry_run = bool(dry_run)
        self._log = logger or (lambda msg: None)
        # Freeze at the END OF A FRAME, not mid-scan: a mid-scan freeze leaves the
        # framestore holding a half-old/half-new frame, which corrupts the score.
        if self.freeze_on_end_frame and not self.dry_run:
            try:
                self._sem.set_value("DP_FREEZE_ON", "End Frame")
            except Exception:
                try:
                    self._sem.set_value("DP_FREEZE_ON", 0.0)  # 0 = End Frame
                except Exception:
                    self._log("[autofocus] WARNING: could not set DP_FREEZE_ON=End Frame; "
                              "a mid-scan freeze may yield a partially-scanned frame.")
        if start_voltage is None:
            start_voltage = self._read_voltage()
        self._start_voltage = float(start_voltage)

    def _read_scan_rate(self) -> Optional[int]:
        try:
            return int(round(float(self._sem.get_float("DP_SCANRATE"))))
        except Exception:
            return None

    def _apply_scan_rate(self, rate: Optional[int]) -> Optional[int]:
        """Set DP_SCANRATE; return the ORIGINAL to restore. Verifies by read-back."""
        orig = self._read_scan_rate()
        if rate is None:
            return orig
        rate = int(rate)
        if self.scan_rate_method in ("set", "set_then_cmd"):
            try:
                self._sem.set_value("DP_SCANRATE", float(rate))
            except Exception as e:  # noqa: BLE001
                self._log(f"[scanrate] set_value DP_SCANRATE={rate} raised: {e}")
        got = self._read_scan_rate()
        if got != rate and self.scan_rate_method in ("cmd", "set_then_cmd"):
            try:
                self._sem.execute(f"CMD_SCANRATE{rate}")
                if self.scan_rate_method == "set_then_cmd":
                    self._log(f"[scanrate] direct set did not take; used "
                              f"CMD_SCANRATE{rate}.")
                else:
                    self._log(f"[scanrate] used CMD_SCANRATE{rate}.")
            except Exception as e:  # noqa: BLE001
                self._log(f"[scanrate] CMD_SCANRATE{rate} raised: {e}")
            got = self._read_scan_rate()
        if got == rate:
            self._log(f"[autofocus] DP_SCANRATE now {rate} (was {orig}).")
        elif got in (None, 0):
            self._log(f"[autofocus] issued scan-rate change to {rate}; DP_SCANRATE "
                      f"reads {got} and could not be confirmed (it is read-only and can "
                      f"read oddly mid-mode). Proceeding.")
        else:
            self._log(f"[autofocus] WARNING: requested DP_SCANRATE={rate} but it reads "
                      f"{got}. The scan rate may not have changed.")
        return orig

    def _read_voltage(self) -> float:
        try:
            return float(self._sem.get_float("AP_FIB_OBJECTIVE_POTENTIAL"))
        except Exception:
            return float(sum(self._bounds) / 2.0)

    def _read_pixel_size(self) -> Optional[float]:
        """Live FIB pixel size (confirmed present on the Crossbeam 550 probe)."""
        for mn in ("AP_FIB_PIXEL_SIZE", "AP_PIXEL_SIZE"):
            try:
                v = float(self._sem.get_float(mn))
                if v > 0:
                    return v
            except Exception:
                continue
        return None

    def get_scan_rotation_deg(self) -> Optional[float]:
        for mn in ("AP_FIB_SCAN_ROTATION", "AP_SCANROTATION"):
            try:
                return float(self._sem.get_float(mn))
            except Exception:
                continue
        return None

    def get_working_distance_m(self) -> Optional[float]:
        """FIB working distance -- recorded for reference; focus stays on voltage."""
        try:
            return float(self._sem.get_float("AP_FIB_WORKING_DISTANCE"))
        except Exception:
            return None

    def _guard_scan_rotation(self) -> None:
        rot = self.get_scan_rotation_deg()
        if rot is None or abs(rot) <= self.max_scan_rotation_deg:
            return
        msg = (f"scan rotation is {rot:.2f} deg (> {self.max_scan_rotation_deg:.2f}); "
               f"the reduced-raster side geometry that keeps the beam OFF the lamella "
               f"assumes ~0. A wrong side would dose the target.")
        if self.strict_scan_rotation:
            raise RuntimeError("autofocus refused: " + msg)
        self._log(f"[autofocus] *** WARNING: {msg} ***")

    def initial_voltage(self) -> float:
        return self._start_voltage

    def bounds(self) -> Optional[Tuple[float, float]]:
        return self._bounds

    # ---------------- session setup / teardown ----------------
    def prepare(self) -> None:
        """Set FIB magnification, then shift the beam OFF the area of interest.

        Focusing rasters the beam for tens of seconds and FIB imaging sputters, so
        the sweep must run on a throwaway patch. Magnification is set to match the
        main workflow (and the conditions the focus metric was calibrated at).

        Idempotent: calling twice will not double-apply the shift.
        """
        if self._prepared:
            return

        if self.dry_run:
            if self.fib_magnification is not None:
                self._log(f"[autofocus] (dry-run) would set AP_FIB_MAGNIFICATION = "
                          f"{float(self.fib_magnification):.6g}")
            if self.beam_shift_x_m is not None:
                self._log(f"[autofocus] (dry-run) would set AP_FIB_BEAM_SHIFT_X = "
                          f"{float(self.beam_shift_x_m):.3e} m (off the area of interest)")
            self._prepared = True
            return

        # 1) Magnification -- the same mnemonic the main workflow writes. ORDER
        #    MATTERS: get into FIB imaging mode FIRST (SmartFIB keeps separate
        #    imaging/milling mags, so a set issued while still in post-milling mode
        #    does not stick), then set and READ BACK to verify. The pixel size read
        #    below depends on this mag being real.
        if self.fib_magnification is not None:
            _ensure_fib_imaging(self._sem)
            self._assert_magnification("at prepare")

        # Live pixel size (exact; falls back to the constant if unavailable) and a
        # scan-rotation guard (the raster-side geometry assumes image==beam axes).
        self._pixel_size_m = self._read_pixel_size()
        if self._pixel_size_m:
            # Cross-check only: raster geometry uses the SCREEN grid (screen_size),
            # so a store-tied live pixel size must NOT drive it (that mis-scaled the
            # geometry in v0.9.0). Warn if the two grids disagree.
            screen_px = fov_width_m(float(self.fib_magnification)) / self.screen_size[0] \
                if self.fib_magnification else None
            if screen_px and abs(self._pixel_size_m - screen_px) / screen_px > 0.10:
                self._log(f"[autofocus] note: live pixel size "
                          f"{self._pixel_size_m * 1e9:.2f} nm != screen-grid pixel size "
                          f"{screen_px * 1e9:.2f} nm (store resolution != screen_size); "
                          f"raster geometry uses the screen grid.")
        self._guard_scan_rotation()

        # Raise imaging dwell (scan rate) for the whole sweep to lift SNR; restored
        # in restore(). Saved so it is put back even if the sweep raises.
        if self.imaging_scan_rate is not None:
            self._orig_scan_rate = self._apply_scan_rate(int(self.imaging_scan_rate))

        # 2) Beam shift -- move off the region we care about BEFORE any imaging.
        if self.beam_shift_x_m is not None:
            shift = float(self.beam_shift_x_m)
            self._log(f"[autofocus] set AP_FIB_BEAM_SHIFT_X = {shift:.3e} m "
                      f"(focusing off the area of interest)")
            self._sem.set_value("AP_FIB_BEAM_SHIFT_X", shift)
            # Mark applied BEFORE any further work so restore() still runs if the
            # very next call throws.
            self._beam_shift_applied = True
            time.sleep(self.settle_s)

        # 3) Reduced raster -- the ONLY thing that actually shrinks the burned area.
        #    (Grab()'s xoff/yoff/width/height only CROP the framestore; the beam
        #    still rasters the whole field, so cropping saves no dose at all.)
        self._apply_reduced_raster()

        # 4) Only complain about the shift being too small for the FULL field if a
        #    reduced raster is not already keeping the beam off the target.
        if self.beam_shift_x_m is not None:
            self._warn_if_shift_does_not_clear_fov()

        self._prepared = True

    def _resolve_raster(self) -> Optional[Tuple[int, int, int, int]]:
        """Work out the reduced-raster rectangle, if any. Cached (and logs once)."""
        if self._raster_cache is not _UNSET:
            return self._raster_cache
        rect = self._compute_raster()
        self._raster_cache = rect
        return rect

    def _compute_raster(self) -> Optional[Tuple[int, int, int, int]]:
        if self.reduced_raster is not None:
            return tuple(int(v) for v in self.reduced_raster)
        if not self.raster_side:
            return None
        side = self.raster_side
        if side == "auto":
            if self.beam_shift_x_m is None or self.fib_magnification is None:
                self._log("[autofocus] raster-side=auto needs both a beam shift and a "
                          "magnification; leaving the raster at full field.")
                return None
            side = safe_raster_side(self.beam_shift_x_m, self.fib_magnification,
                                    self.screen_size[0])
            self._log(f"[autofocus] raster-side=auto -> scanning the {side} side "
                      f"(away from the target).")
        return raster_for_side(side, self.screen_size, self.raster_fraction,
                               h_scale=self.raster_h_scale)

    def _apply_reduced_raster(self) -> None:
        rect = self._resolve_raster()
        if rect is None:
            return
        x, y, w, h = rect
        if self.dry_run:
            self._log(f"[autofocus] (dry-run) would set reduced raster "
                      f"x={x} y={y} w={w} h={h} and CMD_MODE_REDUCED")
            self._check_raster_vs_target(rect)
            return
        self._log(f"[autofocus] reduced raster: x={x} y={y} w={w} h={h} "
                  f"(beam scans ONLY this region)")
        self._sem.set_value("AP_RED_RASTER_POSN_X", float(x))
        self._sem.set_value("AP_RED_RASTER_POSN_Y", float(y))
        self._sem.set_value("AP_RED_RASTER_W", float(w))
        self._sem.set_value("AP_RED_RASTER_H", float(h))
        self._sem.execute("CMD_MODE_REDUCED")
        self._reduced_applied = True
        time.sleep(self.settle_s)
        # Confirm the column really went to reduced mode -- if it silently stayed in
        # full raster we would be burning the target while believing we were not.
        try:
            mode = self._sem.get_str("DP_IMAGE_MODE").strip().lower()
        except Exception:
            mode = None
        if mode is not None and "reduc" not in mode:
            self._log(f"[autofocus] *** WARNING: DP_IMAGE_MODE reads {mode!r}, not "
                      f"Reduced. The beam may still be scanning the FULL field and "
                      f"dosing the area of interest. ***")
        self._check_raster_vs_target(rect)

    def _check_raster_vs_target(self, rect) -> None:
        """Shout if the scanned rectangle covers where the target actually is."""
        if self.beam_shift_x_m is None or self.fib_magnification is None:
            return
        try:
            tx = target_screen_x(self.beam_shift_x_m, self.fib_magnification,
                                 self.screen_size[0])
            fov = fov_width_m(self.fib_magnification)
        except Exception:
            return
        x, _y, w, _h = rect
        px_um = fov / self.screen_size[0] * 1e6
        if x <= tx <= x + w:
            self._log(
                f"[autofocus] *** WARNING: the area of interest is expected at screen "
                f"x~{tx:.0f} px, which is INSIDE the scanned raster x[{x}..{x + w}]. "
                f"This raster will dose it -- scan the other side (--raster-side). "
                f"Convention: +shift moves the target RIGHT, so LEFT is the safe side. ***"
            )
        else:
            margin = min(abs(tx - x), abs(tx - (x + w))) * px_um
            self._log(
                f"[autofocus] area of interest expected at screen x~{tx:.0f} px, "
                f"outside the scanned raster x[{x}..{x + w}] by ~{margin:.1f} um. "
                f"(+shift -> target right of centre; measured on the instrument.)"
            )

    def _warn_if_shift_does_not_clear_fov(self) -> None:
        """Warn if the parked beam still rasters over the area of interest.

        FIB imaging scans the WHOLE field, so shifting by less than half the field
        width leaves the original target inside the scanned (and sputtered) area --
        it just moves it off-centre. This is geometry, not opinion, so we report the
        numbers and let the operator decide rather than blocking the run.
        """
        if self.beam_shift_x_m is None or self.fib_magnification is None:
            return
        try:
            fov = fov_width_m(self.fib_magnification)
        except Exception:
            return
        # If a reduced raster already excludes the target, the shift alone does not
        # need to clear the whole field -- _check_raster_vs_target reported the margin.
        rect = self._resolve_raster()
        if rect is not None:
            try:
                tx = target_screen_x(self.beam_shift_x_m, self.fib_magnification,
                                     self.screen_size[0])
                x, _y, w, _h = rect
                if not (x <= tx <= x + w):
                    return
            except Exception:
                pass
        shift = abs(float(self.beam_shift_x_m))
        half = fov / 2.0
        if shift >= half:
            self._log(
                f"[autofocus] beam parked {shift*1e6:.1f} um; field of view is "
                f"{fov*1e6:.1f} um wide, so the original target is outside the "
                f"scanned area."
            )
            return
        self._log(
            f"[autofocus] *** NOTE: beam shift {shift*1e6:.1f} um does NOT clear the "
            f"field of view at {float(self.fib_magnification):.6g}x "
            f"(FOV {fov*1e6:.1f} um wide, half-width {half*1e6:.1f} um). "
            f"FIB imaging rasters the WHOLE field, so the area of interest is still "
            f"being scanned and dosed -- just {shift*1e6:.1f} um off-centre. "
            f"To put it outside the scanned field you need a shift > {half*1e6:.1f} um "
            f"(or a stage move, or higher magnification for a smaller field). ***"
        )

    def restore(self) -> None:
        """Return AP_FIB_BEAM_SHIFT_X to its home value. Never raises.

        SAFETY-CRITICAL: a beam shift left applied would displace subsequent
        imaging AND milling by the shift distance. This must run on every exit
        path, so it swallows errors -- but it shouts about them, and verifies by
        reading the value back.
        """
        if self.dry_run:
            if self._prepared and self._resolve_raster() is not None:
                self._log("[autofocus] (dry-run) would restore full raster "
                          "(CMD_MODE_NORMAL)")
            if self._beam_shift_applied or self._prepared:
                self._log(f"[autofocus] (dry-run) would restore AP_FIB_BEAM_SHIFT_X = "
                          f"{self.beam_shift_x_return_m:.3e} m")
            self._prepared = False
            return
        # Return to full raster first. Like the beam shift, a reduced raster left
        # applied would silently constrain later imaging.
        self._restore_full_raster()

        # Restore the original scan rate (if we raised it for the sweep).
        if self._orig_scan_rate is not None:
            self._apply_scan_rate(self._orig_scan_rate)
            self._orig_scan_rate = None

        if not self._beam_shift_applied:
            self._prepared = False
            return

        target = float(self.beam_shift_x_return_m)
        try:
            self._log(f"[autofocus] restore AP_FIB_BEAM_SHIFT_X = {target:.3e} m")
            self._sem.set_value("AP_FIB_BEAM_SHIFT_X", target)
            self._beam_shift_applied = False
        except Exception as e:
            self._log(
                f"[autofocus] *** WARNING: FAILED to restore AP_FIB_BEAM_SHIFT_X to "
                f"{target:.3e} m ({e}). The beam may still be shifted off target -- "
                f"CHECK AND RESET IT BEFORE MILLING. ***"
            )
            self._prepared = False
            return

        # Verify: a silent failure here would misplace every subsequent pattern.
        try:
            actual = float(self._sem.get_float("AP_FIB_BEAM_SHIFT_X"))
        except Exception:
            self._log("[autofocus] NOTE: could not read AP_FIB_BEAM_SHIFT_X back to "
                      "confirm the restore.")
        else:
            if abs(actual - target) > 1e-9:
                self._log(
                    f"[autofocus] *** WARNING: AP_FIB_BEAM_SHIFT_X reads "
                    f"{actual:.3e} m after restoring to {target:.3e} m. "
                    f"CHECK IT BEFORE MILLING. ***"
                )
        self._prepared = False

    def _restore_full_raster(self) -> None:
        """Return the column to full raster. Never raises."""
        if not self._reduced_applied:
            return
        try:
            self._log("[autofocus] restore full raster (CMD_MODE_NORMAL)")
            self._sem.execute("CMD_MODE_NORMAL")
            self._reduced_applied = False
        except Exception as e:
            self._log(f"[autofocus] *** WARNING: FAILED to leave reduced raster mode "
                      f"({e}). CHECK THE COLUMN BEFORE MILLING. ***")

    def __enter__(self) -> "ScopeImageSource":
        self.prepare()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.restore()

    def set_voltage(self, voltage: float) -> None:
        vmin, vmax = self._bounds
        v = min(max(float(voltage), vmin), vmax)  # hard safety clamp
        self._log(f"[autofocus] set AP_FIB_OBJECTIVE_POTENTIAL = {v:.3f} V")
        if self.dry_run:
            return
        self._sem.set_value("AP_FIB_OBJECTIVE_POTENTIAL", float(v))
        # Let the objective lens actually reach the new value before imaging.
        time.sleep(self.settle_s)

    def _assert_magnification(self, where: str = "") -> None:
        """Set AP_FIB_MAGNIFICATION to the position's mag and VERIFY it took.

        Milling and FIB mode switches change the FIB magnification (SmartFIB keeps
        separate imaging/milling mags), and a bare set issued before the column is in
        FIB imaging mode can fail to stick. So: ensure imaging mode first (caller),
        set the mag, then READ IT BACK and retry once if it does not match. Runs
        before every grab, not just once at prepare -- a frame at the wrong mag would
        silently corrupt the focus metric and the pixel-size geometry.
        """
        if self.fib_magnification is None or self.dry_run:
            return
        target = float(self.fib_magnification)
        for attempt in range(2):
            self._sem.set_value("AP_FIB_MAGNIFICATION", target)
            time.sleep(0.2)
            try:
                actual = float(self._sem.get_float("AP_FIB_MAGNIFICATION"))
            except Exception:
                actual = None
            if actual is None:
                # Readback unavailable on this instrument/mock: we set it, but cannot
                # verify. Proceed (the pre-fix behaviour) rather than refuse.
                self._log(f"[autofocus] set AP_FIB_MAGNIFICATION = {target:.6g} "
                          f"(readback unavailable; not verified)")
                return
            if abs(actual - target) <= MAG_VERIFY_REL_TOL * target:
                if attempt:
                    self._log(f"[autofocus] magnification re-asserted to {target:.6g}"
                              f"{(' ' + where) if where else ''}")
                return
            self._log(f"[autofocus] WARNING: AP_FIB_MAGNIFICATION read back "
                      f"{actual} but wanted {target:.6g}{(' ' + where) if where else ''}"
                      f" -- re-setting.")
        raise RuntimeError(
            f"could not establish FIB magnification {target:.6g} (read back {actual}); "
            f"refusing to grab at the wrong mag.")

    def grab(self) -> np.ndarray:
        if self.dry_run:
            self._log("[autofocus] (dry-run) would grab a FIB frame")
            return np.zeros((8, 8), dtype=np.float64)
        if not _HAVE_PIL:
            raise RuntimeError("Pillow is required to read grabbed BMP images.")

        _ensure_fib_imaging(self._sem)
        # Check the mag before EVERY image: milling / mode switches can change it.
        self._assert_magnification("before grab")

        # ORDER MATTERS. Grabbing straight after unfreeze returns the PREVIOUS
        # frame: the framestore needs a full cycle (~1.3 s here) to refresh, and
        # Grab() never errors -- it just hands back stale pixels. That made every
        # sweep point identical and the focus curve flat. So:
        #   unfreeze -> scan for frame_wait_s -> freeze at end of frame -> grab.
        # Grabbing from a FROZEN framestore also avoids catching a half-scanned frame.
        try:
            self._sem.execute("CMD_UNFREEZE_ZONE")
        except Exception:
            pass

        # Let the beam paint at least one complete, fresh frame.
        time.sleep(self.frame_wait_s)

        # Freeze (at end of frame) BEFORE grabbing: stops dose and stabilises the
        # framestore. Do this outside `finally` so the grab reads a frozen buffer.
        frozen_ok = True
        try:
            self._sem.execute("CMD_FREEZE_ZONE")
        except Exception:
            frozen_ok = False
            try:
                self._sem.execute("CMD_FREEZE_ALL")
                frozen_ok = True
            except Exception:
                self._log("[autofocus] WARNING: freeze failed; grabbing live frame.")
        if frozen_ok:
            _wait_frozen(self._sem, self.frozen_timeout_s, log=self._log)

        fd, path = tempfile.mkstemp(suffix=".bmp", prefix="pyfib_af_")
        os.close(fd)
        try:
            if self.roi is not None:
                xoff, yoff, w, h = self.roi
            else:
                rect = self._resolve_raster()
                if rect is not None:
                    # Only the rastered region is refreshed; the rest of the
                    # framestore still holds stale pixels, which would drag the
                    # sharpness score toward a constant.
                    xoff, yoff, w, h = rect
                else:
                    xoff, yoff, w, h = self.screen_size[0], self.screen_size[1], 0, 0
                    xoff, yoff, w, h = 0, 0, self.screen_size[0], self.screen_size[1]
            self._sem.grab_bmp(
                path,
                xoff=xoff,
                yoff=yoff,
                width=w,
                height=h,
                reduction=self.grab_reduction,
            )
            with Image.open(path) as im:
                arr = np.asarray(im.convert("L"), dtype=np.float64)
        finally:
            # Belt and braces: ensure scanning is stopped even if the grab threw.
            try:
                self._sem.execute("CMD_FREEZE_ZONE")
            except Exception:
                try:
                    self._sem.execute("CMD_FREEZE_ALL")
                except Exception:
                    pass
            try:
                os.remove(path)
            except OSError:
                pass
        return arr
