"""SEM side of the SEM autofocus fork: every SmartSEM call lives here.

``SemFocusScope`` wraps the SmartSEM connection in :class:`~.guards.GuardedSem` (so it
cannot move the stage, touch contrast/brightness, or run SmartSEM's own autofocus)
and offers what the search needs:

    read_state()      read-only snapshot (the dry run uses only this)
    prepare()         SEM imaging on, End-Frame freeze, optional mag / scan rate,
                      contrast/brightness/mag/stage-Z baselines
    set_wd(wd)        set AP_WD, VERIFY the read-back, check the stage did not move
    grab(path)        frame-safe SEM grab (unfreeze -> wait -> freeze at end of frame
                      -> DP_FROZEN -> Grab), left frozen
    check_unchanged() contrast/brightness/mag/stage Z/imaging mode vs the baselines
    restore()         put back mag and scan rate; never raises

WD itself is restored by the search (it decides between the new focus and the start).

INSTRUMENT-UNTESTED (first armed run): the DP_FIB_IMAGING string "SEM", the AP_WD
read-back tolerance, AP_FRAME_TIME units (ms per the manual), and DP_AUTO_VIDEO names.
"""

from __future__ import annotations

import math
import os
import shutil
import tempfile
import time
from typing import Callable, Dict, Optional, Tuple

import numpy as np

from . import guards as G

try:
    from PIL import Image  # type: ignore
    _HAVE_PIL = True
except Exception:  # pragma: no cover
    _HAVE_PIL = False


class RealClock:
    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class FakeClock:
    """Virtual time for tests: sleep() advances now() instantly."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += max(0.0, float(seconds))


class SemAutofocusAbort(RuntimeError):
    """A guard tripped mid-run. The search restores the start WD and reports it."""


DEFAULT_SETTLE_S = 0.5          # after an AP_WD set, before imaging
DEFAULT_FRAME_WAIT_S = 0.25     # floor on live-scan time before the freeze request; the
                                # real wait is 1.2 x AP_FRAME_TIME + 0.3 s (2 s if unreadable).
                                # v0.48.0: 0.5 -> 0.25 s (the floor dominated at scan speed 4
                                # in a box; measured 2026-10-05, frame 0.05-0.2 s)
PAUSE_CONFIRM_MAX_S = 3.0       # v0.49.0: wait at most this long to confirm the final pause
FROZEN_POLL_S = 0.05            # v0.48.0: DP_FROZEN polling (was 0.2 s Frozen / 0.1 s Live;
                                # ~0.15 s of every 1.45 s grab was polling granularity)
FRAME_WAIT_UNKNOWN_S = 2.0
DEFAULT_FROZEN_TIMEOUT_S = 30.0
DEFAULT_MODE_TIMEOUT_S = 30.0
DEFAULT_GRAB_SIZE = (1024, 768)


def _parse_store(s: Optional[str]) -> Optional[Tuple[int, int]]:
    """'1024 * 768' -> (1024, 768)."""
    if not s:
        return None
    parts = [p for p in s.replace("x", "*").replace("X", "*").split("*")]
    try:
        w, h = int(parts[0].strip()), int(parts[1].strip())
    except Exception:
        return None
    return (w, h) if w > 0 and h > 0 else None


class SemFocusScope:
    """Real-instrument adapter (also driven by the simulator in the tests)."""

    def __init__(self, sem, *, magnification: Optional[float] = None,
                 scan_rate: Optional[int] = G.SCAN_RATE_DEFAULT,
                 settle_s: float = DEFAULT_SETTLE_S,
                 frame_wait_s: float = DEFAULT_FRAME_WAIT_S,
                 frozen_timeout_s: float = DEFAULT_FROZEN_TIMEOUT_S,
                 mode_timeout_s: float = DEFAULT_MODE_TIMEOUT_S,
                 wd_tol_m: float = G.WD_READBACK_TOL_M,
                 window: Tuple[float, float] = (G.WD_MIN_M, G.WD_MAX_M),
                 raster_frac: Optional[float] = G.RASTER_FRAC_DEFAULT,
                 clock=None, logger: Optional[Callable[[str], None]] = None,
                 pause_at_end: bool = True) -> None:
        if raster_frac is not None and raster_frac < 1.0:
            G.centred_box(raster_frac)             # validates the fraction (ValueError)
        self.sem = G.GuardedSem(sem, wd_window=window)
        self.window = self.sem.wd_window          # narrowed to 4-8 mm, never widened
        self.magnification = magnification
        self.scan_rate = scan_rate
        self.settle_s = float(settle_s)
        self.frame_wait_s = float(frame_wait_s)
        self.frozen_timeout_s = float(frozen_timeout_s)
        self.mode_timeout_s = float(mode_timeout_s)
        self.wd_tol_m = float(wd_tol_m)
        self.clock = clock or RealClock()
        self.log = logger or (lambda m: None)
        self.pause_at_end = bool(pause_at_end)     # v0.49.0: restore() leaves the scan frozen
        # filled by prepare()
        self._prepared = False
        self._orig_mag: Optional[float] = None
        self._mag_changed = False
        self._orig_scan_rate: Optional[int] = None
        self._scan_rate_changed = False
        self.baseline: Dict[str, Optional[float]] = {}
        self.grab_size: Tuple[int, int] = DEFAULT_GRAB_SIZE
        self.eff_frame_wait_s = self.frame_wait_s
        self.eff_frozen_timeout_s = self.frozen_timeout_s
        self.wd_log = []          # (set, read-back) pairs -- measured, for the report
        self.wd_set_attempts = 0  # counted BEFORE each set: a set that raised may still have landed
        self.frame_log = []       # measured per frame: (live->frozen s, total grab s)
        # reduced raster
        self.raster_frac = raster_frac if (raster_frac is not None and raster_frac < 1.0) else None
        self.box: Optional[Tuple[int, int, int, int]] = None      # active box (screen px)
        self.grab_rect: Tuple[int, int, int, int] = (0, 0) + DEFAULT_GRAB_SIZE
        self.presmooth_scale = 1.0    # keep the metric's smoothing at the full-frame scale
        self.frame_has_databar = True
        self.notes = []               # things the run could not do as asked (reported)
        self._raster_touched = False
        self._reduced_applied = False
        self._orig_raster: Dict[str, Optional[float]] = {}

    # ------------------------------------------------------------------ reads
    def _f(self, mn: str) -> Optional[float]:
        try:
            v = float(self.sem.get_float(mn))
        except Exception:
            return None
        return v if math.isfinite(v) else None

    def _s(self, mn: str) -> Optional[str]:
        try:
            v = self.sem.get_str(mn)
        except Exception:
            return None
        if v is None:
            return None
        v = str(v).strip()
        return v or None          # an empty read is an unreadable read

    def read_wd(self) -> Optional[float]:
        return self._f("AP_WD")

    def imaging_mode(self) -> Optional[str]:
        """DP_FIB_IMAGING as its state NAME. Numeric 0 means SEM but is also what a
        failed/stale read returns, so the number is never trusted here."""
        return self._s("DP_FIB_IMAGING")

    def read_state(self) -> Dict[str, object]:
        ft = self._f("AP_FRAME_TIME")
        return {
            "frame_time_raw": ft,                                  # manual: milliseconds
            "wd_m": self.read_wd(),
            "mag": self._f("AP_MAG"),
            "contrast_pct": self._f("AP_CONTRAST"),
            "brightness_pct": self._f("AP_BRIGHTNESS"),
            "eht_v": self._f("AP_ACTUALKV"),
            "imaging": self.imaging_mode(),
            "auto_video": self._s("DP_AUTO_VIDEO"),
            "stage_z_m": self._f("AP_STAGE_AT_Z"),
            "scan_rate": self._f("DP_SCANRATE"),
            "frame_time_s": (ft / 1000.0) if (ft is not None and 0 < ft < 6.0e5) else None,
            "store": self._s("DP_IMAGE_STORE"),
            "noise_reduction": self._s("DP_NOISE_REDUCTION"),
            "fib_mode": self._s("DP_FIB_MODE"),
            "operating_mode": self._s("DP_OPERATING_MODE"),
            "image_mode": self._s("DP_IMAGE_MODE"),
            "spot": self._s("DP_SPOT"),
            "line_scan": self._s("DP_LINE_SCAN"),
        }

    # ------------------------------------------------------------------ setup
    def _ensure_sem_imaging(self) -> None:
        mode = self.imaging_mode()
        if G.norm(mode) == "sem":
            return
        if G.norm(mode) not in G.START_IMAGING_OK:
            raise SemAutofocusAbort(f"DP_FIB_IMAGING reads {mode!r}; SEM autofocus only starts "
                                    f"from SEM, FIB or SEM + FIB imaging. Not switching.")
        self.log(f"[sem-af] imaging mode {mode!r} -> CMD_FIB_MODE_SEM")
        self.sem.execute("CMD_FIB_MODE_SEM")
        t0 = self.clock.now()
        while self.clock.now() - t0 < self.mode_timeout_s:
            m = self.imaging_mode()
            if m is not None and m.upper() == "SEM":
                return
            self.clock.sleep(0.25)
        raise SemAutofocusAbort(
            f"DP_FIB_IMAGING did not read 'SEM' within {self.mode_timeout_s:.0f} s after "
            f"CMD_FIB_MODE_SEM (reads {self.imaging_mode()!r}).")

    def _set_mag_verified(self, target: float) -> None:
        for attempt in range(2):
            self.sem.set_value("AP_MAG", float(target))
            self.clock.sleep(0.2)
            got = self._f("AP_MAG")
            if got is not None and abs(got - target) <= G.MAG_REL_TOL * target:
                return
            self.log(f"[sem-af] AP_MAG read back {got} after setting {target:.6g}"
                     + (" -- retrying" if attempt == 0 else ""))
        raise SemAutofocusAbort(f"could not establish SEM magnification {target:.6g} "
                                f"(read back {self._f('AP_MAG')}).")

    def _read_scan_rate(self) -> Optional[int]:
        v = self._f("DP_SCANRATE")
        return None if v is None else int(round(v))

    def prepare(self) -> None:
        if self._prepared:
            return
        self._ensure_sem_imaging()
        # Freeze requests complete the frame in progress -> the store holds a whole frame.
        try:
            self.sem.set_value("DP_FREEZE_ON", "End Frame")
        except Exception:
            try:
                self.sem.set_value("DP_FREEZE_ON", 0.0)
            except Exception:
                self.log("[sem-af] WARNING: could not set DP_FREEZE_ON = End Frame.")
        self._prepared = True     # from here restore() has work to do

        if self.magnification is not None:
            self._orig_mag = self._f("AP_MAG")
            if self._orig_mag is None:
                raise SemAutofocusAbort("cannot read the current SEM mag (AP_MAG), so it could "
                                        "not be restored; not changing it.")
            if abs(self._orig_mag - self.magnification) > \
                    G.MAG_REL_TOL * self.magnification:
                self._mag_changed = True
                self._set_mag_verified(float(self.magnification))
                self.log(f"[sem-af] SEM mag {self._orig_mag} -> {self.magnification:.6g}")

        if self.scan_rate is not None:
            if not 1 <= int(self.scan_rate) <= 15:
                raise SemAutofocusAbort(f"scan rate {self.scan_rate} is outside 1-15.")
            self._orig_scan_rate = self._read_scan_rate()
            if self._orig_scan_rate is None or not 1 <= self._orig_scan_rate <= 15:
                raise SemAutofocusAbort(
                    f"current scan rate reads {self._orig_scan_rate}, which could not be "
                    f"restored with CMD_SCANRATE1-15; not changing it.")
            if self._orig_scan_rate != int(self.scan_rate):
                self.sem.execute(f"CMD_SCANRATE{int(self.scan_rate)}")
                self._scan_rate_changed = True
                self.clock.sleep(0.3)
                got = self._read_scan_rate()
                self.log(f"[sem-af] scan rate {self._orig_scan_rate} -> {int(self.scan_rate)} "
                         f"(reads {got})")

        # Reduced raster: only a centred box is scanned (much shorter frames). Only used
        # when the store is the 1024x768 screen grid, where raster and grab coordinates
        # coincide (the geometry FIB autofocus runs with); otherwise scan the full frame.
        store = _parse_store(self._s("DP_IMAGE_STORE")) or DEFAULT_GRAB_SIZE
        self.grab_size = store
        self.grab_rect = (0, 0, store[0], store[1])
        if self.raster_frac is not None:
            if tuple(store) != G.SCREEN_SIZE:
                self.notes.append(
                    f"store is {store[0]}x{store[1]}, not 1024x768: reduced raster not used "
                    f"(its grab geometry is only established at 1024x768); full frame scanned")
            else:
                self._apply_reduced(G.centred_box(self.raster_frac))

        st = self.read_state()
        self.baseline = {
            "contrast_pct": st["contrast_pct"], "brightness_pct": st["brightness_pct"],
            "mag": st["mag"], "stage_z_m": st["stage_z_m"],
        }
        for k in ("contrast_pct", "brightness_pct", "mag", "stage_z_m"):
            if self.baseline[k] is None:
                raise SemAutofocusAbort(f"cannot read {k} in SEM mode; it is needed for "
                                        f"the run's safety checks.")
        if self.baseline["contrast_pct"] < G.CONTRAST_MIN_PCT:
            raise SemAutofocusAbort(
                f"SEM contrast reads {self.baseline['contrast_pct']:.2f} % in SEM mode "
                f"(the 'contrast 0' failure). Set contrast by hand first.")
        ft = st["frame_time_s"]          # read AFTER the box is on, so it is the box's time
        if ft:
            self.eff_frame_wait_s = max(self.frame_wait_s, 1.2 * ft + 0.3)
            self.eff_frozen_timeout_s = max(self.frozen_timeout_s, 3.0 * ft + 5.0)
        else:
            self.eff_frame_wait_s = max(self.frame_wait_s, FRAME_WAIT_UNKNOWN_S)
        x, y, w, h = self.grab_rect
        self.log(f"[sem-af] SEM mode ready: mag {self.baseline['mag']:.6g}, contrast "
                 f"{self.baseline['contrast_pct']:.2f} %, brightness "
                 f"{self.baseline['brightness_pct']:.2f} %, "
                 + (f"reduced raster {w}x{h} at ({x},{y})" if self.box else
                    f"full frame {w}x{h}")
                 + f", frame {'%.2f s' % ft if ft else 'n/a'} -> wait "
                   f"{self.eff_frame_wait_s:.2f} s per frame")

    def retarget(self, *, magnification: Optional[float] = None,
                 scan_rate: Optional[int] = None) -> None:
        """Change mag / scan speed in the middle of a run (two-stage autofocus, v0.49.0)
        WITHOUT going back to the operator's values in between. The operator's originals
        (read at prepare(), or now if prepare() did not change them) are what restore()
        puts back. The mag baseline for check_unchanged() follows the new mag."""
        if not self._prepared:
            raise SemAutofocusAbort("retarget() before prepare()")
        if magnification is not None:
            cur = self._f("AP_MAG")
            if cur is None:
                raise SemAutofocusAbort("cannot read AP_MAG before changing the mag")
            if abs(cur - magnification) > G.MAG_REL_TOL * magnification:
                if not self._mag_changed:
                    self._orig_mag = cur
                    self._mag_changed = True
                self._set_mag_verified(float(magnification))
                self.log(f"[sem-af] SEM mag {cur:.6g} -> {magnification:.6g}")
            self.magnification = magnification
        if scan_rate is not None:
            if not 1 <= int(scan_rate) <= 15:
                raise SemAutofocusAbort(f"scan rate {scan_rate} is outside 1-15.")
            cur_r = self._read_scan_rate()
            if cur_r != int(scan_rate):
                if not self._scan_rate_changed:
                    if cur_r is None or not 1 <= cur_r <= 15:
                        raise SemAutofocusAbort(f"current scan rate reads {cur_r}; not changing it")
                    self._orig_scan_rate = cur_r
                    self._scan_rate_changed = True
                self.sem.execute(f"CMD_SCANRATE{int(scan_rate)}")
                self.clock.sleep(0.3)
                self.log(f"[sem-af] scan rate {cur_r} -> {int(scan_rate)}")
            self.scan_rate = scan_rate
        m = self._f("AP_MAG")
        if m is None:
            raise SemAutofocusAbort("cannot read AP_MAG after changing it")
        self.baseline["mag"] = m
        st = self.read_state()
        ft = st["frame_time_s"]
        if ft:
            self.eff_frame_wait_s = max(self.frame_wait_s, 1.2 * ft + 0.3)
            self.eff_frozen_timeout_s = max(self.frozen_timeout_s, 3.0 * ft + 5.0)

    def _apply_reduced(self, box: Tuple[int, int, int, int]) -> None:
        self._orig_raster = {mn: self._f(mn) for mn in G.RASTER_MNS}
        self._raster_touched = True
        for mn, v in zip(G.RASTER_MNS, box):
            self.sem.set_value(mn, float(v))
        self._reduced_applied = True            # before the command: restore must send NORMAL
        self.sem.execute("CMD_MODE_REDUCED")
        self.clock.sleep(0.3)
        mode = self._s("DP_IMAGE_MODE")
        if G.norm(mode) != "reduced":
            # MEASURED 2026-10-01: in SEM imaging DP_IMAGE_MODE keeps reading 'Full' even
            # though only the box is scanned (5.5 s vs 20.9 s per frame at scan speed 8).
            # So this is a log note, not a warning; the per-frame times in the report
            # show whether the box is really on.
            self.log(f"[sem-af] note: DP_IMAGE_MODE reads {mode!r} after CMD_MODE_REDUCED "
                     f"(normal in SEM mode; check the per-frame time in the summary)")
        x, y, w, h = box
        self.box = box
        self.grab_rect = box                    # only the box is refreshed: grab only it
        self.presmooth_scale = G.SCREEN_SIZE[0] / float(w)
        self.frame_has_databar = False          # the centred box is above the databar

    def restore(self) -> None:
        """Put back the full-frame raster, mag and scan rate, then PAUSE the scan (frozen,
        v0.49.0) and leave SEM imaging on.
        Never raises (called from finally)."""
        if not self._prepared:
            return
        try:
            self.sem.execute("CMD_FREEZE_ZONE")
        except Exception:
            pass
        self._restore_raster()
        if self._scan_rate_changed and self._orig_scan_rate:
            try:
                self.sem.execute(f"CMD_SCANRATE{int(self._orig_scan_rate)}")
                self.log(f"[sem-af] scan rate restored to {self._orig_scan_rate}")
            except Exception as e:  # noqa: BLE001
                self.log(f"[sem-af] WARNING: could not restore scan rate "
                         f"{self._orig_scan_rate}: {e!r}")
            self._scan_rate_changed = False
        if self._mag_changed and self._orig_mag:
            try:
                self._set_mag_verified(float(self._orig_mag))
                self.log(f"[sem-af] SEM mag restored to {self._orig_mag:.6g}")
            except Exception as e:  # noqa: BLE001
                self.log(f"[sem-af] WARNING: could not restore SEM mag "
                         f"{self._orig_mag:.6g}: {e!r}")
            self._mag_changed = False
        if self.pause_at_end:
            self._pause_scan()
        self._prepared = False

    def _pause_scan(self) -> None:
        """Leave the scan PAUSED (frozen) for the operator (Evan, 2026-10-06: it kept
        rastering after hand-back). Runs LAST in restore(), after the raster, scan speed
        and mag are back, because those changes can restart the scan. With freeze on End
        Frame the scan stops at the end of the frame in progress; waits briefly to confirm.
        Never raises."""
        try:
            try:
                self.sem.execute("CMD_FREEZE_ZONE")
            except Exception:
                self.sem.execute("CMD_FREEZE_ALL")
        except Exception as e:  # noqa: BLE001
            self.log(f"[sem-af] WARNING: could not pause the scan ({e!r}); press Freeze in "
                     f"SmartSEM.")
            return
        ft = self._f("AP_FRAME_TIME")
        ft_s = ft / 1000.0 if (ft is not None and 0 < ft < 6.0e5) else None
        wait = min(PAUSE_CONFIRM_MAX_S, (1.2 * ft_s + 0.5) if ft_s else PAUSE_CONFIRM_MAX_S)
        t0 = self.clock.now()
        while True:
            if self._is_frozen():
                self.log("[sem-af] scan paused (frozen)")
                return
            if self.clock.now() - t0 > wait:
                break
            self.clock.sleep(0.1)
        self.log(f"[sem-af] freeze requested: the scan pauses at the end of the current frame"
                 + (f" (~{ft_s:.1f} s)" if ft_s else ""))

    def _restore_raster(self) -> None:
        """Back to the normal full-frame scan and the operator's box values. Never raises."""
        if self._reduced_applied:
            try:
                self.sem.execute("CMD_MODE_NORMAL")
                self._reduced_applied = False
                self.clock.sleep(0.3)
                mode = self._s("DP_IMAGE_MODE")
                if mode is not None and G.norm(mode) != "full":
                    self.log(f"[sem-af] *** WARNING: DP_IMAGE_MODE reads {mode!r} after "
                             f"CMD_MODE_NORMAL. Check the scan is back to the full frame. ***")
                else:
                    self.log("[sem-af] raster back to the full frame (CMD_MODE_NORMAL)")
            except Exception as e:  # noqa: BLE001
                self.log(f"[sem-af] *** WARNING: FAILED to leave reduced raster ({e!r}). "
                         f"Switch the scan back to normal by hand. ***")
        if self._raster_touched:
            for mn in G.RASTER_MNS:
                v = self._orig_raster.get(mn)
                if v is None:
                    continue
                try:
                    self.sem.set_value(mn, float(v))
                except Exception:  # noqa: BLE001
                    pass
            self._raster_touched = False
        self.box = None

    # ------------------------------------------------------------------ focus
    def set_wd(self, wd_m: float) -> float:
        """Set AP_WD and VERIFY it. Returns the read-back. Raises SemAutofocusAbort on a
        read-back mismatch or if the stage Z moved (a WD change must never move it)."""
        wd_m = float(wd_m)
        if not G.wd_in_window(wd_m, self.window):
            raise SemAutofocusAbort(f"target WD {G.mm(wd_m)} is outside the window.")
        self.wd_set_attempts += 1
        self.sem.set_value("AP_WD", wd_m)          # GuardedSem re-checks the window
        self.clock.sleep(self.settle_s)
        rb = self.read_wd()
        self.wd_log.append((wd_m, rb))
        if rb is None:
            raise SemAutofocusAbort(f"AP_WD unreadable after setting {G.mm(wd_m)}.")
        if abs(rb - wd_m) > self.wd_tol_m:
            raise SemAutofocusAbort(
                f"AP_WD read back {G.mm(rb)} after setting {G.mm(wd_m)} "
                f"(off by {(rb - wd_m) * 1e6:+.2f} um, tolerance {self.wd_tol_m * 1e6:.1f} um).")
        z0 = self.baseline.get("stage_z_m")
        if z0 is not None:
            z = self._f("AP_STAGE_AT_Z")
            if z is None or abs(z - z0) > G.STAGE_Z_TOL_M:
                raise SemAutofocusAbort(
                    f"stage Z changed from {G.mm(z0)} to {G.mm(z)} when WD was set -- a WD "
                    f"change must never move the stage (is a Z/WD link on?).")
        return rb

    def check_unchanged(self) -> Optional[str]:
        """None if contrast/brightness/mag/stage Z/imaging are as at prepare(), else why."""
        b = self.baseline
        mode = self.imaging_mode()
        if mode is None or mode.upper() != "SEM":
            return f"imaging mode changed to {mode!r} during the run"
        for key, mn in (("contrast_pct", "AP_CONTRAST"), ("brightness_pct", "AP_BRIGHTNESS")):
            v = self._f(mn)
            if v is None or abs(v - b[key]) > G.BC_CHANGE_TOL_PCT:
                return (f"{mn} changed from {b[key]:.2f} % to "
                        f"{'unreadable' if v is None else '%.2f %%' % v} during the run "
                        f"(pyFIB never sets it: auto BC or someone at the console?)")
        m = self._f("AP_MAG")
        if m is None or abs(m - b["mag"]) > G.MAG_REL_TOL * b["mag"]:
            return f"AP_MAG changed from {b['mag']:.6g} to {m} during the run"
        z = self._f("AP_STAGE_AT_Z")
        if z is None or abs(z - b["stage_z_m"]) > G.STAGE_Z_TOL_M:
            return f"stage Z changed from {G.mm(b['stage_z_m'])} to {G.mm(z)} during the run"
        return None

    # ------------------------------------------------------------------ imaging
    def _is_frozen(self) -> Optional[bool]:
        """True/False from DP_FROZEN (name first, then number, like frame_grab.is_frozen);
        None if unreadable."""
        v = self._s("DP_FROZEN")
        if v is not None:
            if v.lower() in ("frozen", "1", "yes"):
                return True
            if v.lower() in ("live", "0", "no"):
                return False
        n = self._f("DP_FROZEN")
        if n is None:
            return None
        return int(round(n)) == 1

    def grab(self, save_path: Optional[str] = None) -> np.ndarray:
        """Frame-safe SEM grab. Raises SemAutofocusAbort unless the scan is PROVEN to have
        gone Live and then Frozen again (a stale or partial frame would give a meaningless
        focus score). Grabs to an absolute temp file (the proven autofocus path), then
        copies it to ``save_path`` if one is given."""
        if not _HAVE_PIL:
            raise RuntimeError("Pillow is required to read grabbed BMP images.")
        t_start = self.clock.now()
        self.sem.execute("CMD_UNFREEZE_ZONE")        # a failure here aborts the run
        t0 = self.clock.now()
        while True:
            st = self._is_frozen()
            if st is None:
                raise SemAutofocusAbort("DP_FROZEN is unreadable, so a fresh frame cannot be "
                                        "confirmed.")
            if st is False:
                break
            if self.clock.now() - t0 > G.UNFREEZE_LIVE_TIMEOUT_S:
                raise SemAutofocusAbort(
                    f"the scan did not go Live within {G.UNFREEZE_LIVE_TIMEOUT_S:.0f} s of "
                    f"CMD_UNFREEZE_ZONE: the next frame would be the old one.")
            self.clock.sleep(FROZEN_POLL_S)
        t_live = self.clock.now()
        self.clock.sleep(self.eff_frame_wait_s)
        try:
            self.sem.execute("CMD_FREEZE_ZONE")
        except Exception:
            self.sem.execute("CMD_FREEZE_ALL")
        t0 = self.clock.now()
        while True:
            st = self._is_frozen()
            if st is None:
                raise SemAutofocusAbort("DP_FROZEN became unreadable; the frame may be partial.")
            if st:
                break
            if self.clock.now() - t0 > self.eff_frozen_timeout_s:
                raise SemAutofocusAbort(
                    f"DP_FROZEN did not read Frozen within {self.eff_frozen_timeout_s:.0f} s "
                    f"-- the frame may be partial or stale.")
            self.clock.sleep(FROZEN_POLL_S)
        t_frozen = self.clock.now()

        fd, tmp = tempfile.mkstemp(suffix=".bmp", prefix="pyfib_semaf_")
        os.close(fd)
        try:
            x, y, w, h = self.grab_rect
            self.sem.grab_bmp(tmp, xoff=int(x), yoff=int(y), width=int(w), height=int(h),
                              reduction=0)
            with Image.open(tmp) as im:
                arr = np.asarray(im.convert("L"), dtype=np.float64)
            if save_path:
                shutil.copyfile(tmp, os.path.abspath(str(save_path)))
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        self.frame_log.append((round(t_frozen - t_live, 3), round(self.clock.now() - t_start, 3)))
        return arr
