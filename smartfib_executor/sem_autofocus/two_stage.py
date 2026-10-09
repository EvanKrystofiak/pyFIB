"""Two-stage SEM autofocus (v0.48.0): coarse at low mag, fine at the working mag.

Designed from MEASURED focus curves (sem_af_curve_20261005_162406, Evan 2026-10-05; true
focus 6.942-6.945 mm, all seven passes agreed):

  * Coarse, 500x, scan speed 4, centred box 0.35, WD steps 200 um: the parabola through
    the best three points lands within ~30 um of focus (half-band at 500x ~ +/-300 um).
    Starts at WD +/-600 um (7 frames); if the sharpest frame is at an edge it extends 3
    steps that way, again and again, inside 4-8 mm (at most COARSE_MAX_POINTS frames).
  * Fine, working mag (2000x), scan speed 4, box 0.35, coarse result +/-90 um in 30 um
    steps (7 frames) + 2 frames halfway around the best + 1 verification frame. 30 um
    steps alone landed within 2 um at speed 4 (speeds 5/6 gave the same focus).
  * Box 0.35 is as fast as smaller boxes (the 0.25 s frame-wait floor dominates at speed
    4) with twice the features of 0.25; at 0.18 the coarse estimate broke down.
  * The first frame after a big WD jump scored within 5% of the next one at that WD (with
    a 2 s settle), so there is no throwaway frame -- just that settle.

Every guard of the single-stage search applies (see search.py): read-only preflight;
GuardedSem allow-list; 4-8 mm window on every set; AP_WD read-back; a 10 um probe step
first; stage Z, contrast, brightness, mag and SEM imaging unchanged; fresh, non-blank,
non-identical frames; an interior (bracketed) peak with real contrast in BOTH stages; a
verification frame. Any failure puts AP_WD back where it started.

v0.49.0 (Evan, 2026-10-06 run: 1.78 mm out of focus at 66x -> OK at 6.1544 mm, 23 frames,
55 s): one scope for both stages, going straight from the coarse mag to the fine mag
(retarget) instead of back to the operator's mag in between; the operator's mag, scan
speed and full-frame raster are restored once at the end and the scan is left PAUSED
(frozen). The fine stage uses a 0.2 contrast floor (FINE_MIN_CURVE_CONTRAST); the coarse
stage keeps 0.3. The 2 s big-jump settle is settable (big_jump_settle_s).
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

import numpy as np

from ..autofocus import metrics as _metrics
from ..autofocus import sweep as _sweep
from . import guards as G
from .scope import SemAutofocusAbort, SemFocusScope
from .search import SemAutofocusResult, preflight

COARSE_MAG = 500.0
COARSE_SPEED = 4
COARSE_HALF_RANGE_M = 600e-6
COARSE_STEP_M = 200e-6
COARSE_EXTEND_POINTS = 3
COARSE_MAX_POINTS = 21                 # 200 um x 20 = 4 mm: the whole 4-8 mm window
FINE_SPEED = 4
FINE_HALF_RANGE_M = 90e-6
FINE_STEP_M = 30e-6
FINE_EXTEND_MAX = 2                    # extra 2-point extensions if the fine peak is at an edge
BOX_FRAC = 0.35
BIG_JUMP_M = 300e-6                    # jumps at least this big get the measured 2 s settle
BIG_JUMP_SETTLE_S = 2.0
# v0.49.0: the fine window (+/-90 um at speed 4) only spans 0.41-0.53 of curve contrast on
# the 2026-10-05 data and 0.43 on the 2026-10-06 run; noise alone gives ~0.1. The coarse
# stage (which proves the image has features) keeps the 0.3 floor.
FINE_MIN_CURVE_CONTRAST = 0.2
MAX_FRAMES = 40


@dataclass
class TwoStageResult(SemAutofocusResult):
    coarse_mag: Optional[float] = None
    fine_mag: Optional[float] = None
    coarse_curve: List[Tuple[float, float]] = field(default_factory=list)
    coarse_best_m: Optional[float] = None
    coarse_contrast: float = 0.0
    coarse_extensions: int = 0
    fine_extensions: int = 0
    coarse_frames: int = 0
    fine_frames: int = 0


def _grid(center: float, step: float, k_lo: int, k_hi: int,
          window: Tuple[float, float]) -> List[float]:
    lo, hi = window
    return [round(center + k * step, 9) for k in range(k_lo, k_hi + 1)
            if lo - 1e-12 <= center + k * step <= hi + 1e-12]


def estimate_seconds(state: dict, *, coarse_points: int = 7, fine_frames: int = 10,
                     coarse_speed: int = COARSE_SPEED, fine_speed: int = FINE_SPEED,
                     box: Optional[float] = BOX_FRAC, settle_s: float = 0.5,
                     frame_wait_s: float = 0.25) -> Optional[float]:
    """Rough run time from the current full-frame time (x2 per scan-speed step and the
    measured box factor, as in curve.py) plus the measured per-grab overhead."""
    from .curve import estimate_frame_s
    ft_now, rate = state.get("frame_time_s"), state.get("scan_rate")
    if not ft_now or rate is None:
        return None
    total = 2 * BIG_JUMP_SETTLE_S + 3.0          # two big jumps, three mag changes
    for n, sp in ((coarse_points, coarse_speed), (fine_frames, fine_speed)):
        ft = estimate_frame_s(ft_now, int(round(rate)), sp, box) or 0.5
        total += n * (settle_s + max(frame_wait_s, 1.2 * ft + 0.3) + ft + 0.6)
    return total


def sem_autofocus_two_stage(target, *,
                            fine_mag: Optional[float] = None,
                            coarse_mag: float = COARSE_MAG,
                            coarse_speed: int = COARSE_SPEED,
                            fine_speed: int = FINE_SPEED,
                            coarse_half_range_m: float = COARSE_HALF_RANGE_M,
                            coarse_step_m: float = COARSE_STEP_M,
                            fine_half_range_m: float = FINE_HALF_RANGE_M,
                            fine_step_m: float = FINE_STEP_M,
                            raster_frac: Optional[float] = BOX_FRAC,
                            metric: str = _metrics.DEFAULT_METRIC,
                            presmooth_frac: float = _metrics.DEFAULT_PRESMOOTH_FRAC,
                            min_curve_contrast: float = G.MIN_CURVE_CONTRAST,
                            fine_min_curve_contrast: float = FINE_MIN_CURVE_CONTRAST,
                            big_jump_settle_s: float = BIG_JUMP_SETTLE_S,
                            log_preflight: bool = True,
                            verify_min_ratio: float = G.VERIFY_MIN_RATIO,
                            max_frames: int = MAX_FRAMES,
                            settle_s: float = 0.5,
                            frame_wait_s: float = 0.25,
                            wd_tol_m: float = G.WD_READBACK_TOL_M,
                            probe_step_m: float = G.PROBE_STEP_M,
                            save_dir: Optional[str] = None,
                            plan_only: bool = False,
                            clock=None,
                            logger: Optional[Callable[[str], None]] = None) -> TwoStageResult:
    """Coarse (low mag) -> fine (working mag) SEM focus. See the module docstring."""
    log = logger or (lambda m: None)
    if metric not in _metrics.METRICS:
        raise ValueError(f"unknown metric {metric!r}")
    for name, v in (("coarse step", coarse_step_m), ("fine step", fine_step_m),
                    ("coarse half-range", coarse_half_range_m),
                    ("fine half-range", fine_half_range_m)):
        if not v > 0:
            raise ValueError(f"{name} must be > 0")
    if coarse_half_range_m > G.HALF_RANGE_MAX_M:
        raise ValueError(f"coarse half-range must be <= {G.HALF_RANGE_MAX_M * 1e6:.0f} um "
                         f"(the extension step covers more when needed)")
    for sp in (coarse_speed, fine_speed):
        if not 1 <= int(sp) <= 15:
            raise ValueError(f"scan speed {sp} is outside 1-15")
    mk = dict(settle_s=settle_s, frame_wait_s=frame_wait_s, wd_tol_m=wd_tol_m,
              raster_frac=raster_frac, clock=clock, logger=log)
    probe = SemFocusScope(target, magnification=None, scan_rate=None,
                          **{**mk, "raster_frac": None})
    warns: List[str] = []
    reasons, st, _ = preflight(probe, half_range_m=coarse_half_range_m, coarse_points=3,
                               max_frames=max_frames, warnings=warns)
    res = TwoStageResult(success=False, message="", metric=metric, state_before=st,
                         wd_start_m=st["wd_m"], warnings=warns, coarse_mag=coarse_mag,
                         fine_mag=fine_mag if fine_mag is not None else st.get("mag"))
    n_coarse = int(math.floor(coarse_half_range_m / coarse_step_m + 1e-9))
    n_fine = int(math.floor(fine_half_range_m / fine_step_m + 1e-9))
    plog = log if log_preflight else (lambda m: None)
    plog(f"[sem-af2] start: WD {G.mm(st['wd_m'])}, mag {st['mag']}, contrast "
        f"{st['contrast_pct']} %, brightness {st['brightness_pct']} %, EHT {st['eht_v']} V, "
        f"imaging {st['imaging']!r}, auto BC {st['auto_video']!r}")
    plog(f"[sem-af2] coarse: {coarse_mag:g}x, scan speed {coarse_speed}, "
        f"{2 * n_coarse + 1} WDs +/-{coarse_half_range_m * 1e6:g} um in "
        f"{coarse_step_m * 1e6:g} um steps (extends {COARSE_EXTEND_POINTS} steps at an edge, "
        f"<= {COARSE_MAX_POINTS} frames)")
    plog(f"[sem-af2] fine:   {res.fine_mag}x, scan speed {fine_speed}, {2 * n_fine + 1} WDs "
        f"+/-{fine_half_range_m * 1e6:g} um in {fine_step_m * 1e6:g} um steps around the "
        f"coarse result, +2 halfway, +1 verify; box "
        f"{raster_frac if raster_frac else 'full frame'}")
    est = estimate_seconds(st, coarse_points=2 * n_coarse + 1, fine_frames=2 * n_fine + 4,
                           coarse_speed=coarse_speed, fine_speed=fine_speed, box=raster_frac,
                           settle_s=settle_s, frame_wait_s=frame_wait_s)
    if est:
        plog(f"[sem-af2] estimated ~{est:.0f} s (more if the coarse range has to extend)")
    for w in warns:
        plog(f"[sem-af2] note: {w}")
    if fine_mag is None and st.get("mag") is None:
        reasons.append("cannot read the current SEM mag (needed for the fine stage)")
    if reasons:
        res.refused = True
        res.reasons = reasons
        res.message = "REFUSED (nothing changed): " + "; ".join(reasons) + "."
        log("[sem-af2] " + res.message)
        return res
    if plan_only:
        res.planned = True
        res.message = "DRY RUN: guards pass; nothing was changed."
        return res

    wd0 = float(st["wd_m"])
    window = probe.window
    frames = {"left": int(max_frames), "n": 0}
    prev = {"img": None}
    active: dict = {"scope": None}
    any_set = {"v": False}
    keep_new_focus = False

    def _path(stage: str, wd: float, tag: str) -> Optional[str]:
        if not save_dir:
            return None
        return os.path.join(save_dir, f"af2_{frames['n']:02d}_{stage}_{tag}_wd{wd * 1e3:.4f}mm.bmp")

    def _set(scope: SemFocusScope, wd: float) -> float:
        cur = scope.read_wd()
        any_set["v"] = True                # BEFORE the set: one that raised may still have landed
        rb = scope.set_wd(wd)
        if cur is not None and abs(wd - cur) >= BIG_JUMP_M:
            scope.clock.sleep(big_jump_settle_s)
        return rb

    def _score(scope: SemFocusScope, stage: str, wd: float, tag: str) -> float:
        if frames["left"] <= 0:
            raise SemAutofocusAbort(f"frame budget ({max_frames}) used up")
        frames["left"] -= 1
        frames["n"] += 1
        path = _path(stage, wd, tag)
        img = scope.grab(path)
        res.n_frames += 1
        if path:
            res.frames.append(path)
        why = G.frame_sanity(img, crop_bar=scope.frame_has_databar)
        if why:
            raise SemAutofocusAbort(f"bad frame at WD {G.mm(wd)}: {why}")
        if prev["img"] is not None and prev["img"].shape == img.shape and \
                np.array_equal(prev["img"], img):
            raise SemAutofocusAbort(f"frame at WD {G.mm(wd)} is identical to the previous one "
                                    f"(stale framestore: the scan did not refresh)")
        prev["img"] = img
        why = scope.check_unchanged()
        if why:
            raise SemAutofocusAbort(why)
        s = float(_metrics.sharpness(img, metric, crop_bar=scope.frame_has_databar,
                                     presmooth_frac=presmooth_frac * scope.presmooth_scale))
        if not math.isfinite(s):
            raise SemAutofocusAbort(f"non-finite sharpness at WD {G.mm(wd)}")
        return s

    def _measure(scope, stage, wd, curve) -> None:
        if any(abs(wd - w) < 0.1e-6 for w, _ in curve):
            return
        rb = _set(scope, wd)
        s = _score(scope, stage, wd, "sweep")
        curve.append((wd, s))
        log(f"[sem-af2] {stage:<6} WD {G.mm(wd)} ({(wd - wd0) * 1e6:+7.1f} um, reads "
            f"{G.mm(rb)}) -> {metric} {s:.5g}")

    def _stage(scope: SemFocusScope) -> None:
        active["scope"] = scope            # before prepare(): a half-done prepare is restored too
        scope.prepare()
        for n in scope.notes:
            res.warnings.append(n)
            log(f"[sem-af2] note: {n}")
        if res.raster_box is None:
            res.raster_box = scope.box

    def _end_stage() -> None:
        sc = active["scope"]
        if sc is not None:
            res.wd_readbacks.extend(sc.wd_log)
            res.frame_timing_s.extend(sc.frame_log)
            sc.restore()
        active["scope"] = None

    coarse: List[Tuple[float, float]] = []
    fine: List[Tuple[float, float]] = []
    try:
        # ================= COARSE (low mag) =================
        cs = SemFocusScope(target, magnification=coarse_mag, scan_rate=int(coarse_speed), **mk)
        _stage(cs)
        wd_now = cs.read_wd()
        if wd_now is None or abs(wd_now - wd0) > cs.wd_tol_m:
            raise SemAutofocusAbort(f"AP_WD reads {G.mm(wd_now)} after preparing the coarse "
                                    f"stage, not the start {G.mm(wd0)}")
        if probe_step_m > 0:
            pw = wd0 - probe_step_m if wd0 - probe_step_m >= window[0] else wd0 + probe_step_m
            rb = _set(cs, pw)
            log(f"[sem-af2] probe: WD {G.mm(pw)} reads {G.mm(rb)}, stage Z unchanged")
        k_lo, k_hi = -n_coarse, n_coarse
        for wd in _grid(wd0, coarse_step_m, k_lo, k_hi, window):
            _measure(cs, "coarse", wd, coarse)
        while True:
            ref = _sweep.refine_peak(coarse)
            if ref.bracketed:
                break
            wds = sorted(w for w, _ in coarse)
            low_edge = abs(ref.best_voltage - wds[0]) < 1e-9
            new_lo, new_hi = (k_lo - COARSE_EXTEND_POINTS, k_lo - 1) if low_edge else \
                (k_hi + 1, k_hi + COARSE_EXTEND_POINTS)
            more = _grid(wd0, coarse_step_m, new_lo, new_hi, window)
            if not more:
                # The next grid step is outside 4-8 mm: try the window limit itself, so a
                # focus just inside the limit can still be bracketed.
                edge_wd = window[0] if low_edge else window[1]
                if abs(edge_wd - (wds[0] if low_edge else wds[-1])) > 20e-6:
                    more = [edge_wd]
            if not more or len(coarse) + len(more) > COARSE_MAX_POINTS:
                at_window = not more
                raise SemAutofocusAbort(
                    f"coarse: sharpest frame at the sweep edge (WD {G.mm(ref.best_voltage)})"
                    + (" which is the 4-8 mm window limit: the reading does not make sense"
                       if at_window else f" after {len(coarse)} frames; focus not found"))
            res.coarse_extensions += 1
            log(f"[sem-af2] coarse peak at the {'low' if low_edge else 'high'} edge: extending "
                f"{len(more)} steps")
            for wd in (sorted(more, reverse=True) if low_edge else sorted(more)):
                _measure(cs, "coarse", wd, coarse)
            if low_edge:
                k_lo = new_lo
            else:
                k_hi = new_hi
        res.coarse_curve = sorted(coarse)
        res.coarse_contrast = float(ref.contrast)
        res.coarse_frames = len(coarse)
        cbest = float(ref.estimated_voltage if ref.refined else ref.best_voltage)
        res.coarse_best_m = cbest
        log(f"[sem-af2] coarse result: WD {G.mm(cbest)} ({(cbest - wd0) * 1e6:+.0f} um), "
            f"contrast {ref.contrast:.3f}")
        if ref.contrast < min_curve_contrast:
            raise SemAutofocusAbort(f"coarse focus curve is flat (contrast {ref.contrast:.3f} < "
                                    f"{min_curve_contrast:.3f}): no reliable peak")
        # ================= FINE (working mag) =================
        # v0.49.0: straight from the coarse mag to the fine mag on the SAME scope (no detour
        # back to the operator's mag); restore() at the end still puts the operator's back.
        fs = cs
        target_mag = fine_mag if fine_mag is not None else (cs._orig_mag or st.get("mag"))
        fs.retarget(magnification=float(target_mag), scan_rate=int(fine_speed))
        res.fine_mag = float(target_mag)
        prev["img"] = None                 # a different mag: the first frame is new anyway
        f_lo, f_hi = -n_fine, n_fine
        for wd in _grid(cbest, fine_step_m, f_lo, f_hi, window):
            _measure(fs, "fine", wd, fine)
        ref = _sweep.refine_peak(fine)
        while not ref.bracketed and res.fine_extensions < FINE_EXTEND_MAX:
            wds = sorted(w for w, _ in fine)
            low_edge = abs(ref.best_voltage - wds[0]) < 1e-9
            new = (f_lo - 2, f_lo - 1) if low_edge else (f_hi + 1, f_hi + 2)
            more = _grid(cbest, fine_step_m, new[0], new[1], window)
            if not more:
                break
            res.fine_extensions += 1
            log(f"[sem-af2] fine peak at the {'low' if low_edge else 'high'} edge: extending")
            for wd in (sorted(more, reverse=True) if low_edge else sorted(more)):
                _measure(fs, "fine", wd, fine)
            if low_edge:
                f_lo = new[0]
            else:
                f_hi = new[1]
            ref = _sweep.refine_peak(fine)
        if ref.bracketed:                  # halfway points around the best fine frame
            for wd in (ref.best_voltage - fine_step_m / 2, ref.best_voltage + fine_step_m / 2):
                if G.wd_in_window(wd, window):
                    _measure(fs, "fine", wd, fine)
            ref = _sweep.refine_peak(fine)

        res.curve = sorted(fine)
        res.fine_frames = len(fine)
        res.bracketed = bool(ref.bracketed)
        res.refined = bool(ref.refined)
        res.curve_contrast = float(ref.contrast)
        res.best_score = float(ref.best_score)
        best = float(ref.estimated_voltage if res.refined else ref.best_voltage)
        res.wd_best_m = best
        wds = [w for w, _ in fine]
        if res.curve_contrast < fine_min_curve_contrast:
            raise SemAutofocusAbort(f"fine focus curve is flat (contrast {res.curve_contrast:.3f}"
                                    f" < {fine_min_curve_contrast:.3f}): no reliable peak")
        if not res.bracketed:
            raise SemAutofocusAbort(f"fine: sharpest frame at the sweep edge (WD "
                                    f"{G.mm(ref.best_voltage)}), focus not bracketed")
        if not G.wd_in_window(best, window) or not (min(wds) - 1e-9 <= best <= max(wds) + 1e-9):
            raise SemAutofocusAbort(f"fitted focus {G.mm(best)} is outside the window or the "
                                    f"swept range")
        _set(fs, best)
        vs = _score(fs, "verify", best, "verify")
        res.verify_score = vs
        res.verify_ratio = vs / res.best_score if res.best_score > 0 else None
        log(f"[sem-af2] verify at WD {G.mm(best)}: {metric} {vs:.5g} "
            f"({(res.verify_ratio or 0) * 100:.0f}% of the fine peak)")
        if verify_min_ratio > 0 and (res.verify_ratio is None
                                     or res.verify_ratio < verify_min_ratio):
            raise SemAutofocusAbort(f"focus did not reproduce: verification frame scored "
                                    f"{(res.verify_ratio or 0) * 100:.0f}% of the fine peak")
        keep_new_focus = True
        res.success = True
        res.message = (f"Focus set: WD {G.mm(wd0)} -> {G.mm(best)} ({(best - wd0) * 1e6:+.1f} um;"
                       f" coarse said {(cbest - wd0) * 1e6:+.0f} um).")
    except SemAutofocusAbort as e:
        res.message = f"ABORTED: {str(e).rstrip('.')}."
        res.reasons.append(str(e))
        log(f"[sem-af2] {res.message}")
    except G.SemAutofocusSafetyError as e:
        res.message = f"SAFETY STOP: {e}"
        res.reasons.append(str(e))
        log(f"[sem-af2] {res.message}")
    except Exception as e:  # noqa: BLE001 -- show the real error (bug #6)
        res.message = f"ERROR: {type(e).__name__}: {e!r}"
        res.reasons.append(res.message)
        log(f"[sem-af2] {res.message}")
    finally:
        sc = active["scope"] or probe
        if not keep_new_focus and any_set["v"]:
            try:
                rb = sc.set_wd(wd0)
                res.restored = True
                log(f"[sem-af2] WD restored to the start: {G.mm(rb)}")
            except Exception as e:  # noqa: BLE001
                res.restore_failed = True
                log(f"[sem-af2] *** WARNING: could not restore WD to {G.mm(wd0)} ({e!r}). "
                    f"CHECK SEM FOCUS BY HAND. ***")
        try:
            _end_stage()
        except Exception:  # noqa: BLE001
            pass
        res.wd_final_m = probe.read_wd()
        if not res.curve and fine:
            res.curve = sorted(fine)
        if not res.coarse_curve and coarse:
            res.coarse_curve = sorted(coarse)
    return res
