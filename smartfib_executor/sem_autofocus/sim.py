"""A fake SmartSEM for the SEM autofocus fork (offline demo + tests).

Presents the same surface the real ``SmartSEM`` wrapper does (get_float / get_str /
set_value / execute / grab_bmp) so the REAL adapter (scope.SemFocusScope) and search
run unchanged against it. Images sharpen as AP_WD approaches ``true_wd_m``; the
brightness/contrast values shape the image, so contrast 0 gives a flat frame exactly
as on the instrument.

Fault knobs reproduce the failures the guards exist for:
    contrast_zero_after   AP_CONTRAST drops to 0 after N grabs (bug list #3's symptom)
    wd_readback_offset_m  AP_WD reads back off by this much (set did not take)
    stale_after           from grab N on, the framestore stops refreshing
    never_frozen          DP_FROZEN never reports Frozen
    z_follows_wd          a WD change moves stage Z (a Z/WD link)
    featureless           a blank specimen (flat curve)
The simulator also executes CMD_AUTO_FOCUS_* the way the bug report describes
(contrast -> 0), so a test can prove the guard blocks it before it gets here.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np

try:
    from PIL import Image, ImageFilter  # type: ignore
    _HAVE_PIL = True
except Exception:  # pragma: no cover
    _HAVE_PIL = False

IMAGING_NAMES = {0: "SEM", 1: "FIB", 2: "Milling", 3: "SEM + FIB", 4: "Mill + SEM"}


class SimulatedSem:
    def __init__(self, *, true_wd_m: float = 5.030e-3, wd_m: float = 5.000e-3,
                 mag: float = 2000.0, contrast_pct: float = 32.0,
                 brightness_pct: float = 50.0, eht_v: float = 2000.0,
                 imaging: str = "FIB", auto_video: str = "Off",
                 stage_z_m: float = 30.0e-3, scan_rate: int = 5,
                 frame_time_ms: float = 1300.0, store: Tuple[int, int] = (1024, 768),
                 blur_per_um: float = 0.08, base_blur: float = 0.6,
                 noise_sigma: float = 4.0, seed: int = 0,
                 contrast_zero_after: Optional[int] = None,
                 wd_readback_offset_m: float = 0.0,
                 stale_after: Optional[int] = None,
                 never_frozen: bool = False,
                 z_follows_wd: bool = False,
                 featureless: bool = False,
                 mode_switch_works: bool = True,
                 noise_reduction: str = "Line Avg", fib_mode: str = "Imaging",
                 operating_mode: str = "Normal", image_mode: str = "Full",
                 spot: str = "Off", line_scan: str = "Off",
                 unfreeze_fails: bool = False, stuck_frozen: bool = False,
                 reduced_works: bool = True,
                 raster: Tuple[float, float, float, float] = (100.0, 100.0, 300.0, 200.0),
                 unreadable: Tuple[str, ...] = ()) -> None:
        self.true_wd_m = float(true_wd_m)
        self.wd_m = float(wd_m)
        self.mag = float(mag)
        self.contrast = float(contrast_pct)
        self.brightness = float(brightness_pct)
        self.eht_v = float(eht_v)
        self.imaging = imaging
        self.auto_video = auto_video
        self.stage_z_m = float(stage_z_m)
        self.scan_rate = int(scan_rate)
        self.frame_time_ms = float(frame_time_ms)
        self.store = tuple(store)
        self.blur_per_um = float(blur_per_um)
        self.base_blur = float(base_blur)
        self.noise_sigma = float(noise_sigma)
        self.contrast_zero_after = contrast_zero_after
        self.wd_readback_offset_m = float(wd_readback_offset_m)
        self.stale_after = stale_after
        self.never_frozen = bool(never_frozen)
        self.z_follows_wd = bool(z_follows_wd)
        self.featureless = bool(featureless)
        self.mode_switch_works = bool(mode_switch_works)
        self.unreadable = set(unreadable)
        self.dp = {"DP_NOISE_REDUCTION": noise_reduction, "DP_FIB_MODE": fib_mode,
                   "DP_OPERATING_MODE": operating_mode, "DP_IMAGE_MODE": image_mode,
                   "DP_SPOT": spot, "DP_LINE_SCAN": line_scan}
        self.reduced_works = bool(reduced_works)
        self.raster = dict(zip(("AP_RED_RASTER_POSN_X", "AP_RED_RASTER_POSN_Y",
                                "AP_RED_RASTER_W", "AP_RED_RASTER_H"),
                               (float(v) for v in raster)))
        self.unfreeze_fails = bool(unfreeze_fails)
        self.stuck_frozen = bool(stuck_frozen)
        self.frozen = True
        self.freeze_on = "End Frame"
        self._rng = np.random.default_rng(seed)
        self._feature = self._make_feature(self.store, seed)
        self._store_img: Optional[np.ndarray] = None   # the framestore (persists between frames)
        self.grab_rects: List[tuple] = []
        self.n_grabs = 0
        self.set_log: List[Tuple[str, object]] = []
        self.cmd_log: List[str] = []
        self.moves: List[tuple] = []

    @staticmethod
    def _make_feature(store: Tuple[int, int], seed: int) -> np.ndarray:
        w, h = store
        rng = np.random.default_rng(seed + 777)
        img = np.zeros((h, w))
        for _ in range(int(w * h / 1500)):
            cy, cx = int(rng.integers(4, h - 4)), int(rng.integers(4, w - 4))
            r = int(rng.integers(1, 5))
            img[cy - r:cy + r, cx - r:cx + r] = float(rng.uniform(0.3, 1.0))
        return img - img.mean()

    # ------------------------------------------------------------- reads
    def get_float(self, m: str) -> float:
        if m in self.unreadable:
            raise RuntimeError(f"sim: {m} unreadable")
        vals = {
            "AP_WD": self.wd_m + self.wd_readback_offset_m,
            "AP_MAG": self.mag,
            "AP_CONTRAST": self.contrast,
            "AP_BRIGHTNESS": self.brightness,
            "AP_ACTUALKV": self.eht_v,
            "AP_STAGE_AT_Z": self.stage_z_m,
            "DP_SCANRATE": float(self.scan_rate),
            "AP_FRAME_TIME": self._frame_time_ms(),
            "DP_FROZEN": 1.0 if self.frozen else 0.0,
        }
        if m in self.raster:
            return self.raster[m]
        if m == "DP_FIB_IMAGING":
            inv = {v: k for k, v in IMAGING_NAMES.items()}
            return float(inv.get(self.imaging, 7))
        if m in vals:
            return float(vals[m])
        raise KeyError(m)

    def get_str(self, m: str) -> str:
        if m in self.unreadable:
            raise RuntimeError(f"sim: {m} unreadable")
        if m == "DP_FIB_IMAGING":
            return self.imaging
        if m == "DP_AUTO_VIDEO":
            return self.auto_video
        if m == "DP_FROZEN":
            return "Frozen" if self.frozen else "Live"
        if m in self.dp:
            return self.dp[m]
        if m == "DP_IMAGE_STORE":
            return f"{self.store[0]} * {self.store[1]}"
        if m == "AP_WD":
            return f"= {self.wd_m * 1e3:.3f} mm"
        raise KeyError(m)

    # ------------------------------------------------------------- writes
    def set_value(self, m: str, v) -> None:
        self.set_log.append((m, v))
        if m == "AP_WD":
            new = float(v)
            if self.z_follows_wd:
                self.stage_z_m += (self.wd_m - new)     # shorter WD -> stage up
            self.wd_m = new
        elif m == "AP_MAG":
            self.mag = float(v)
        elif m == "AP_CONTRAST":
            self.contrast = float(v)
        elif m == "AP_BRIGHTNESS":
            self.brightness = float(v)
        elif m == "DP_FREEZE_ON":
            self.freeze_on = v
        elif m in self.raster:
            self.raster[m] = float(v)
        else:
            raise KeyError(m)

    def execute(self, c: str) -> None:
        self.cmd_log.append(c)
        if c == "CMD_FIB_MODE_SEM":
            if self.mode_switch_works:
                self.imaging = "SEM"
        elif c == "CMD_FIB_MODE_FIB":
            self.imaging = "FIB"
        elif c == "CMD_UNFREEZE_ZONE":
            if self.unfreeze_fails:
                raise RuntimeError("sim: CMD_UNFREEZE_ZONE failed (API_E_EXEC_CMD_FAIL)")
            self.frozen = self.stuck_frozen
        elif c in ("CMD_FREEZE_ZONE", "CMD_FREEZE_ALL"):
            self.frozen = not self.never_frozen
        elif c == "CMD_MODE_REDUCED":
            if self.reduced_works:
                self.dp["DP_IMAGE_MODE"] = "Reduced"
        elif c == "CMD_MODE_NORMAL":
            self.dp["DP_IMAGE_MODE"] = "Full"
        elif c.startswith("CMD_SCANRATE"):
            self.scan_rate = int(c[len("CMD_SCANRATE"):])
        elif c in ("CMD_AUTO_FOCUS_COARSE", "CMD_AUTO_FOCUS_FINE"):
            self.wd_m = self.true_wd_m
            self.contrast = 0.0                    # the reported SmartSEM side effect
        else:
            raise KeyError(c)

    def move_stage(self, *a) -> None:              # present so a test can prove it is never reached
        self.moves.append(a)

    # ------------------------------------------------------------- image
    def _render(self) -> np.ndarray:
        w, h = self.store
        radius = self.base_blur + self.blur_per_um * abs(self.wd_m - self.true_wd_m) * 1e6
        feat = np.zeros((h, w)) if self.featureless else self._feature
        if radius > 1e-6 and _HAVE_PIL and not self.featureless:
            lo, hi = feat.min(), feat.max()
            u8 = ((feat - lo) / (hi - lo) * 255.0).astype(np.uint8)
            b = np.asarray(Image.fromarray(u8).filter(ImageFilter.GaussianBlur(radius)),
                           dtype=np.float64)
            feat = b / 255.0 * (hi - lo) + lo
        gain = self.contrast / 32.0 * 220.0        # contrast 0 -> no signal at all
        base = 30.0 + self.brightness / 50.0 * 60.0
        img = base + gain * feat
        if gain > 0:
            img = img + self._rng.normal(0.0, self.noise_sigma, size=img.shape)
        img = np.clip(img, 0, 255)
        # SmartSEM databar: bright separator row at ~90.6% height, dark bar below.
        sep = int(round(h * 0.906))
        img[sep, :] = 255.0
        img[sep + 1:, :] = 20.0
        img[sep + 4:sep + 8, 10:60] = 230.0         # "text"
        return img

    def reduced(self) -> bool:
        return self.dp["DP_IMAGE_MODE"] == "Reduced"

    def _box(self):
        r = self.raster
        return (int(r["AP_RED_RASTER_POSN_X"]), int(r["AP_RED_RASTER_POSN_Y"]),
                int(r["AP_RED_RASTER_W"]), int(r["AP_RED_RASTER_H"]))

    def _frame_time_ms(self) -> float:
        """Frame time scales with the scanned area (reduced raster -> shorter)."""
        if not self.reduced():
            return self.frame_time_ms
        _, _, w, h = self._box()
        return self.frame_time_ms * (w * h) / float(self.store[0] * self.store[1])

    def grab_bmp(self, path, *, xoff=0, yoff=0, width=1024, height=768, reduction=0):
        self.n_grabs += 1
        self.grab_rects.append((int(xoff), int(yoff), int(width), int(height)))
        if self.contrast_zero_after is not None and self.n_grabs > self.contrast_zero_after:
            self.contrast = 0.0
        stale = (self.stale_after is not None and self.n_grabs > self.stale_after
                 and self._store_img is not None)
        if not stale:
            fresh = self._render()
            if self.reduced() and self._store_img is not None:
                # Only the box is scanned; the rest of the framestore keeps old pixels.
                x, y, w, h = self._box()
                img = self._store_img.copy()
                img[y:y + h, x:x + w] = fresh[y:y + h, x:x + w]
            else:
                img = fresh
            self._store_img = img
        img = self._store_img
        crop = img[int(yoff):int(yoff) + int(height), int(xoff):int(xoff) + int(width)]
        Image.fromarray(crop.astype(np.uint8)).save(str(path), format="BMP")
