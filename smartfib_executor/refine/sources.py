"""Image/actuator sources for position refinement.

A RefineScope is the boundary between the pure refinement logic and the outside
world. The orchestrator only needs to:
    grab_fib_image(magnification) -> ndarray   # fresh FIB frame at that mag
    get_beam_shift() / set_beam_shift(x, y)    # AP_FIB_BEAM_SHIFT_X/Y (metres)
    get_stage_xy() / move_stage_xy(x, y)       # X/Y only (stage path is DISABLED
                                               #   by default in refine.py)

Implementations:
  * SimulatedRefineScope -- deterministic, hardware-free. Models a feature that is
    displaced from the reference framing (stage-recall error) and moves under beam
    shift per the MEASURED convention, so the closed-loop refinement can be proven
    offline -- including that a WRONG sign diverges and is rejected.
  * ScopeRefineAdapter -- the real adapter over smartsem_com.SmartSEM. Only file
    here that touches the instrument; NOT yet validated on the microscope. pywin32
    is imported lazily so this module imports fine anywhere.
"""

from __future__ import annotations

import os
import tempfile
import time
from typing import Optional, Tuple

import numpy as np

try:
    from PIL import Image  # project dependency; used to read grabbed BMPs
    _HAVE_PIL = True
except Exception:  # pragma: no cover
    _HAVE_PIL = False

from .geometry import BEAM_SHIFT_MAX_M, pixel_size_m


class RefineScope:
    """Interface implemented by the simulator and the real adapter."""

    def grab_fib_image(self, magnification: float) -> np.ndarray:
        raise NotImplementedError

    def get_pixel_size_m(self) -> Optional[float]:
        """Live sample-plane pixel size (metres), or None if not available.

        When present this is used in preference to the magnification-derived
        constant, since it is exact and self-corrects for store resolution.
        """
        return None

    def get_scan_rotation_deg(self) -> Optional[float]:
        """FIB scan rotation in degrees, or None if not readable.

        The refinement assumes image axes are aligned with the beam-shift axes; a
        non-zero scan rotation silently breaks that. The orchestrator checks this.
        """
        return None

    def get_beam_shift(self) -> Tuple[float, float]:
        raise NotImplementedError

    def set_beam_shift(self, x_m: float, y_m: float) -> None:
        raise NotImplementedError

    def get_stage_xy(self) -> Tuple[float, float]:
        raise NotImplementedError

    def move_stage_xy(self, x_m: float, y_m: float) -> None:
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Fourier sub-pixel shift (used by the simulator)
# --------------------------------------------------------------------------- #
def fourier_shift(img: np.ndarray, dy: float, dx: float) -> np.ndarray:
    """Translate an image by (dy, dx) pixels with sub-pixel accuracy via FFT."""
    h, w = img.shape
    ky = np.fft.fftfreq(h)[:, None]
    kx = np.fft.fftfreq(w)[None, :]
    phase = np.exp(-2j * np.pi * (ky * dy + kx * dx))
    return np.real(np.fft.ifft2(np.fft.fft2(img) * phase))


# --------------------------------------------------------------------------- #
# Simulated source (offline)
# --------------------------------------------------------------------------- #
class SimulatedRefineScope(RefineScope):
    """A fake FIB where a feature is displaced from the reference framing.

    The reference image is the scene at zero displacement / zero beam shift. After
    a "stage recall" the feature sits at ``true_offset_px`` = (oy, ox). A beam
    shift of (bx, by) metres moves the imaged feature by
    (R_x*bx, R_y*by)/pixel_size pixels, where R defaults to +1 on both axes to
    match the measured instrument (+X beam shift -> image moves RIGHT).
    A stage move changes the underlying displacement.
    """

    def __init__(
        self,
        *,
        magnification: float = 2000.0,
        size: Tuple[int, int] = (768, 1024),
        true_offset_px: Tuple[float, float] = (0.0, 0.0),  # (oy, ox)
        # Response R: image displacement per unit beam shift. Default +1 on both
        # axes to match the MEASURED instrument (+shift -> image moves RIGHT/DOWN;
        # see geometry.BEAM_SIGN_*). Flip these to simulate the other handedness.
        beam_sign_x: float = +1.0,
        beam_sign_y: float = +1.0,
        noise_sigma: float = 8.0,
        add_databar: bool = True,
        scan_rotation_deg: float = 0.0,
        report_live_pixel_size: bool = True,
        seed: int = 0,
    ) -> None:
        self.magnification = float(magnification)
        self.size = size
        self.true_offset = list(true_offset_px)     # [oy, ox], mutated by stage moves
        self.beam_sign_x = float(beam_sign_x)
        self.beam_sign_y = float(beam_sign_y)
        self.noise_sigma = float(noise_sigma)
        self.add_databar = add_databar
        self.scan_rotation_deg = float(scan_rotation_deg)
        self.report_live_pixel_size = bool(report_live_pixel_size)
        self._rng = np.random.default_rng(seed)
        self._beam = [0.0, 0.0]     # (x_m, y_m)
        self._stage = [0.0, 0.0]    # (x_m, y_m)
        self._scene = self._make_scene(size, seed)
        self.px = pixel_size_m(self.magnification, size[1])
        self.grabs = 0

    @staticmethod
    def _make_scene(size, seed) -> np.ndarray:
        rng = np.random.default_rng(seed + 99)
        h, w = size
        img = np.full((h, w), 60.0)
        for _ in range(80):
            cy, cx = rng.integers(8, h - 8), rng.integers(8, w - 8)
            r = int(rng.integers(3, 9))
            img[cy - r:cy + r, cx - r:cx + r] = float(rng.integers(120, 240))
        return img

    def reference_image(self) -> np.ndarray:
        """The 'stored' reference: scene at zero offset / zero beam shift."""
        img = self._scene.copy()
        return self._finish(img)

    def _finish(self, img: np.ndarray) -> np.ndarray:
        img = img + self._rng.normal(0, self.noise_sigma, img.shape)
        img = np.clip(img, 0, 255)
        if self.add_databar:
            h, w = img.shape
            bar = int(round(h * 0.094 / (1 - 0.094)))
            sep = np.full((1, w), 255.0)
            barimg = np.zeros((bar, w))
            barimg[2:8, 5:w // 3] = 255.0  # static high-contrast "text"
            img = np.vstack([img, sep, barimg])
        return img

    # -- RefineScope API --------------------------------------------------
    def grab_fib_image(self, magnification: float) -> np.ndarray:
        self.grabs += 1
        oy, ox = self.true_offset
        # feature position = recall offset + beam-shift contribution
        fx = ox + self.beam_sign_x * self._beam[0] / self.px
        fy = oy + self.beam_sign_y * self._beam[1] / self.px
        img = fourier_shift(self._scene, fy, fx)
        return self._finish(img)

    def get_pixel_size_m(self) -> Optional[float]:
        return self.px if self.report_live_pixel_size else None

    def get_scan_rotation_deg(self) -> Optional[float]:
        return self.scan_rotation_deg

    def get_beam_shift(self) -> Tuple[float, float]:
        return tuple(self._beam)

    def set_beam_shift(self, x_m: float, y_m: float) -> None:
        self._beam = [
            float(np.clip(x_m, -BEAM_SHIFT_MAX_M, BEAM_SHIFT_MAX_M)),
            float(np.clip(y_m, -BEAM_SHIFT_MAX_M, BEAM_SHIFT_MAX_M)),
        ]

    def get_stage_xy(self) -> Tuple[float, float]:
        return tuple(self._stage)

    def move_stage_xy(self, x_m: float, y_m: float) -> None:
        # Moving the stage changes the feature displacement (in pixels).
        dxm = x_m - self._stage[0]
        dym = y_m - self._stage[1]
        self._stage = [float(x_m), float(y_m)]
        # Harness convention chosen to be CONSISTENT WITH geometry.stage_correction
        # (STAGE_SIGN_* = -1), so the enabled stage path converges in tests. This is
        # a test assumption only: the REAL stage<->image sign/rotation is uncalibrated
        # and stage correction stays disabled by default in refine.py.
        self.true_offset[1] += dxm / self.px
        self.true_offset[0] += dym / self.px


# --------------------------------------------------------------------------- #
# Real instrument adapter (NOT yet validated on the microscope)
# --------------------------------------------------------------------------- #
def _ensure_fib_imaging(sem, timeout_s: float = 60.0, poll_s: float = 0.25) -> None:
    def _state():
        try:
            return sem.get_str("DP_FIB_IMAGING").strip()
        except Exception:
            return None

    if (_state() or "").upper() != "FIB":
        sem.execute("CMD_FIB_MODE_FIB")
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if (_state() or "").upper() == "FIB":
                return
            try:
                if int(round(float(sem.get_float("DP_FIB_IMAGING")))) == 1:
                    return
            except Exception:
                pass
            time.sleep(poll_s)
        raise TimeoutError("timed out waiting for DP_FIB_IMAGING=FIB")


def _read_scan_rate(sem) -> Optional[int]:
    try:
        return int(round(float(sem.get_float("DP_SCANRATE"))))
    except Exception:
        return None


def _apply_scan_rate(sem, rate: Optional[int], method: str, log) -> Optional[int]:
    """Set DP_SCANRATE to `rate`; return the ORIGINAL rate (to restore later).

    method: 'set'  -> set_value only (what the operator asked to try first)
            'cmd'  -> CMD_SCANRATE<n> only
            'set_then_cmd' -> set_value, and if the read-back disagrees, CMD_SCANRATE<n>
    Always verifies by read-back and logs what actually happened, so it is obvious on
    the instrument whether DP_SCANRATE is directly settable.
    """
    orig = _read_scan_rate(sem)
    if rate is None:
        return orig
    rate = int(rate)
    if method in ("set", "set_then_cmd"):
        try:
            sem.set_value("DP_SCANRATE", float(rate))
        except Exception as e:  # noqa: BLE001
            log(f"[scanrate] set_value DP_SCANRATE={rate} raised: {e}")
    got = _read_scan_rate(sem)
    if got != rate and method in ("cmd", "set_then_cmd"):
        try:
            sem.execute(f"CMD_SCANRATE{rate}")
            if method == "set_then_cmd":
                log(f"[scanrate] direct set did not take; used CMD_SCANRATE{rate}.")
            else:
                log(f"[scanrate] used CMD_SCANRATE{rate}.")
        except Exception as e:  # noqa: BLE001
            log(f"[scanrate] CMD_SCANRATE{rate} raised: {e}")
        got = _read_scan_rate(sem)
    if got == rate:
        log(f"[scanrate] DP_SCANRATE now {rate} (was {orig}).")
    elif got in (None, 0):
        # DP_SCANRATE is read-only (driven by CMD_SCANRATE#) and can read 0 / be
        # unreadable mid-mode. Issuing the command is the correct action; we just
        # cannot positively confirm it here, so note it rather than alarm.
        log(f"[scanrate] issued scan-rate change to {rate}; DP_SCANRATE reads "
            f"{got} and could not be confirmed (it is read-only and can read oddly "
            f"mid-mode). Proceeding.")
    else:
        log(f"[scanrate] WARNING: requested DP_SCANRATE={rate} but it reads {got}. "
            f"The scan rate may not have changed.")
    return orig


def _is_frozen(sem) -> Optional[bool]:
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
    """Block until DP_FROZEN reports Frozen, i.e. the (possibly slow) frame finished."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        st = _is_frozen(sem)
        if st is None:
            return  # cannot read; rely on the time padding
        if st:
            return
        time.sleep(poll_s)
    if log:
        log(f"[refine] WARNING: DP_FROZEN did not report Frozen within {timeout_s:.0f}s; "
            f"the grabbed frame may be incomplete.")


class ScopeRefineAdapter(RefineScope):
    """Real adapter over smartsem_com.SmartSEM. Instrument-untested.

    Uses the frame-safe grab sequence proven in the autofocus fork (unfreeze ->
    scan a full frame -> freeze at end of frame -> grab from the frozen buffer).
    Stage motion is X/Y only and is only ever called when refine.py is explicitly
    allowed to use the stage (disabled by default).
    """

    def __init__(self, sem, *, settle_s: float = 1.0, frame_wait_s: float = 3.0,
                 grab_size: Tuple[int, int] = (1024, 768), grab_reduction: int = 0,
                 imaging_scan_rate: Optional[int] = None, scan_rate_method: str = "cmd",
                 freeze_on_end_frame: bool = True, frozen_timeout_s: float = 30.0,
                 dry_run: bool = False, logger=None) -> None:
        self._sem = sem
        self.settle_s = float(settle_s)
        self.frame_wait_s = float(frame_wait_s)
        self.grab_size = grab_size
        self.grab_reduction = int(grab_reduction)
        self.imaging_scan_rate = imaging_scan_rate
        self.scan_rate_method = scan_rate_method
        self.freeze_on_end_frame = bool(freeze_on_end_frame)
        self.frozen_timeout_s = float(frozen_timeout_s)
        self.dry_run = bool(dry_run)
        self._log = logger or (lambda m: None)

    def grab_fib_image(self, magnification: float) -> np.ndarray:
        self._log(f"[refine] set AP_FIB_MAGNIFICATION = {float(magnification):.6g}")
        if not self.dry_run:
            self._sem.set_value("AP_FIB_MAGNIFICATION", float(magnification))
            time.sleep(self.settle_s)
        if self.dry_run:
            self._log("[refine] (dry-run) would grab a FIB frame")
            return np.zeros((8, 8))
        if not _HAVE_PIL:
            raise RuntimeError("Pillow is required to read grabbed BMP images.")

        _ensure_fib_imaging(self._sem)

        # Raise the imaging dwell (scan rate) for this correlation frame to lift SNR,
        # remembering the original to restore afterwards.
        orig_rate = None
        if self.imaging_scan_rate is not None:
            orig_rate = _apply_scan_rate(self._sem, int(self.imaging_scan_rate),
                                         self.scan_rate_method, self._log)
        # Freeze at the END of a frame so a slower (higher-dwell) frame is captured
        # complete, not half-scanned.
        if self.freeze_on_end_frame:
            try:
                self._sem.set_value("DP_FREEZE_ON", "End Frame")
            except Exception:
                try:
                    self._sem.set_value("DP_FREEZE_ON", 0.0)
                except Exception:
                    pass
        try:
            try:
                self._sem.execute("CMD_UNFREEZE_ZONE")
            except Exception:
                pass
            time.sleep(self.frame_wait_s)          # minimum; poll below guarantees completion
            try:
                self._sem.execute("CMD_FREEZE_ZONE")
            except Exception:
                try:
                    self._sem.execute("CMD_FREEZE_ALL")
                except Exception:
                    pass
            _wait_frozen(self._sem, self.frozen_timeout_s, log=self._log)

            fd, path = tempfile.mkstemp(suffix=".bmp", prefix="pyfib_refine_")
            os.close(fd)
            try:
                w, h = self.grab_size
                self._sem.grab_bmp(path, xoff=0, yoff=0, width=w, height=h,
                                   reduction=self.grab_reduction)
                with Image.open(path) as im:
                    return np.asarray(im.convert("L"), dtype=np.float64)
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
        finally:
            # Always restore the original scan rate, even if the grab threw.
            if orig_rate is not None:
                _apply_scan_rate(self._sem, orig_rate, self.scan_rate_method,
                                 self._log)

    def get_beam_shift(self) -> Tuple[float, float]:
        return (float(self._sem.get_float("AP_FIB_BEAM_SHIFT_X")),
                float(self._sem.get_float("AP_FIB_BEAM_SHIFT_Y")))

    def get_pixel_size_m(self) -> Optional[float]:
        """Live FIB pixel size (confirmed present on the Crossbeam 550 probe)."""
        for mn in ("AP_FIB_PIXEL_SIZE", "AP_PIXEL_SIZE"):
            try:
                return float(self._sem.get_float(mn))
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

    def set_beam_shift(self, x_m: float, y_m: float) -> None:
        xm = float(np.clip(x_m, -BEAM_SHIFT_MAX_M, BEAM_SHIFT_MAX_M))
        ym = float(np.clip(y_m, -BEAM_SHIFT_MAX_M, BEAM_SHIFT_MAX_M))
        self._log(f"[refine] set AP_FIB_BEAM_SHIFT_X/Y = {xm:.3e}, {ym:.3e} m")
        if self.dry_run:
            return
        self._sem.set_value("AP_FIB_BEAM_SHIFT_X", xm)
        self._sem.set_value("AP_FIB_BEAM_SHIFT_Y", ym)
        time.sleep(self.settle_s)

    def get_stage_xy(self) -> Tuple[float, float]:
        p = self._sem.get_stage_position()
        return (float(p.x_m), float(p.y_m))

    def move_stage_xy(self, x_m: float, y_m: float) -> None:
        # X/Y ONLY: read the current pose and change nothing else.
        p = self._sem.get_stage_position()
        t = getattr(p, "t_deg", 0.0) or 0.0
        self._log(f"[refine] move_stage X/Y -> ({x_m:.6e}, {y_m:.6e}) "
                  f"(z/r/m/t held)")
        if self.dry_run:
            return
        self._sem.move_stage(float(x_m), float(y_m), float(p.z_m), float(t),
                             float(p.r_deg), float(p.m_m))
        try:
            self._sem.wait_stage_idle()
        except Exception:
            pass
