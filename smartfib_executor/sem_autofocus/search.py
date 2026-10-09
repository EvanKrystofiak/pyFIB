"""SEM autofocus search: step AP_WD, score sharpness, fit the peak -- with guards.

Same strategy as the validated FIB autofocus (coarse sweep -> fine sweep -> parabola),
built from its proven pure pieces (``autofocus.sweep`` planning / peak refinement and
``autofocus.metrics`` sharpness), with the SEM-specific guards the operator asked for:

  Before anything changes (the dry run stops here):
    * SEM beam on (AP_ACTUALKV); imaging is SEM / FIB / SEM + FIB and the FIB is not
      milling, depositing or switching (DP_FIB_IMAGING, DP_FIB_MODE);
    * noise reduction Line/Pixel Avg; normal full-frame scan (no split, reduced raster,
      spot or line scan) -- unreadable secondary modes are reported as warnings;
    * the starting AP_WD reads inside the 4-8 mm window;
    * contrast is not ~0 and auto brightness/contrast (DP_AUTO_VIDEO) is off;
    * the sweep range is sane (<= 1 mm half-range) and still has >= 3 points after
      clamping to the window.
  During the sweep (any trip -> abort, WD back to the start, report why):
    * WD is unchanged by the switch to SEM imaging; a 10 um probe step comes first;
    * every AP_WD set is read back and must match; the stage Z must not move;
    * every frame is proven fresh: the scan goes Live after the unfreeze and Frozen
      again at the end of a frame (DP_FROZEN unreadable = abort);
    * every frame must be a real image (not blank / black / saturated) and must differ
      from the previous one (a stale framestore gives identical frames);
    * contrast, brightness, mag, stage Z and SEM imaging must stay as they were.
  Before accepting a focus:
    * the peak must be interior (bracketed) -- an edge peak is NOT extrapolated;
    * the curve must have real contrast (not flat noise);
    * the chosen WD must be inside the window and inside the swept range;
    * a verification frame at the chosen WD must score >= 60% of the sweep peak.
  Otherwise the WD goes back to where it started. SmartSEM's CMD_AUTO_FOCUS_* is never
  called (GuardedSem makes that impossible).
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from ..autofocus import metrics as _metrics
from ..autofocus import sweep as _sweep
from . import guards as G
from .scope import SemAutofocusAbort


@dataclass
class SemAutofocusResult:
    success: bool
    message: str
    planned: bool = False                 # True for a dry run (nothing changed)
    refused: bool = False                 # preflight refused before anything changed
    wd_start_m: Optional[float] = None
    wd_final_m: Optional[float] = None    # AP_WD read back at the end
    wd_best_m: Optional[float] = None     # the focus the sweep found (even if rejected)
    best_score: float = float("nan")
    metric: str = _metrics.DEFAULT_METRIC
    bracketed: bool = False
    refined: bool = False
    curve_contrast: float = 0.0
    verify_score: Optional[float] = None
    verify_ratio: Optional[float] = None
    restored: bool = False                # WD was put back to the start
    restore_failed: bool = False          # WD could NOT be confirmed back at the start
    n_frames: int = 0
    curve: List[Tuple[float, float]] = field(default_factory=list)   # (wd_m, score)
    coarse_plan_m: List[float] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)     # could not check; run allowed
    frame_timing_s: List[Tuple[float, float]] = field(default_factory=list)  # (live->frozen, grab)
    raster_box: Optional[Tuple[int, int, int, int]] = None   # reduced raster used (screen px)
    state_before: Dict[str, object] = field(default_factory=dict)
    wd_readbacks: List[Tuple[float, Optional[float]]] = field(default_factory=list)
    frames: List[str] = field(default_factory=list)


def _estimate_s(scope, n_frames: int, state) -> float:
    """Rough: the frame time read now (full frame, current scan speed), scaled to the
    run's scan speed (it doubles per step -- measured) and by the box area."""
    ft = state.get("frame_time_s")
    cur, new = state.get("scan_rate"), getattr(scope, "scan_rate", None)
    if ft and cur and new:
        ft = ft * 2.0 ** (int(new) - int(round(cur)))
    frac = getattr(scope, "raster_frac", None)
    if ft and frac:
        ft = ft * frac * frac
    wait = max(scope.frame_wait_s, 1.2 * ft + 0.3) if ft else max(scope.frame_wait_s, 2.0)
    return n_frames * (scope.settle_s + wait + (ft or 0.0))


def preflight(scope, *, half_range_m: float, coarse_points: int, max_frames: int,
              warnings: Optional[List[str]] = None
              ) -> Tuple[List[str], Dict[str, object], List[float]]:
    """Read-only checks. Returns (refusal reasons, state, coarse plan). Checks that
    could not be made (value unreadable) but are backed up by a mid-run guard are
    appended to ``warnings`` instead of refusing."""
    st = scope.read_state()
    reasons: List[str] = []
    warns = warnings if warnings is not None else []
    lo, hi = scope.window

    kv = st["eht_v"]
    if kv is None:
        reasons.append("cannot read AP_ACTUALKV (SEM high voltage)")
    elif kv < G.EHT_MIN_ON_V:
        reasons.append(f"SEM beam appears OFF (AP_ACTUALKV = {kv:.0f} V)")

    mode = st["imaging"]
    if mode is None:
        reasons.append("cannot read DP_FIB_IMAGING (needed to confirm SEM imaging)")
    elif G.norm(mode) not in G.START_IMAGING_OK:
        reasons.append(f"DP_FIB_IMAGING reads {mode!r}: only SEM, FIB or SEM + FIB imaging is "
                       f"a safe start (not milling, deposition, drift, external)")
    fm = G.norm(st["fib_mode"])
    if fm is None:
        warns.append("DP_FIB_MODE unreadable (FIB milling state not double-checked)")
    elif "mill" in fm or "depos" in fm or fm.startswith("to "):
        reasons.append(f"DP_FIB_MODE reads {st['fib_mode']!r}: the FIB is busy")

    # Scan settings that would make frames stale, mixed across WD steps, or park the beam.
    for key, mn, ok, why in (
            ("noise_reduction", "DP_NOISE_REDUCTION", G.NOISE_REDUCTION_OK,
             "frame/continuous averaging or integration mixes or freezes frames; set Noise "
             "Reduction to Line Avg or Pixel Avg for the focus run"),
            ("operating_mode", "DP_OPERATING_MODE", {"normal"},
             "the scan must be the normal full frame (not split/quad/reduced/spot)"),
            ("image_mode", "DP_IMAGE_MODE", {"full"}, "a reduced raster leaves stale pixels"),
            ("spot", "DP_SPOT", {"off"}, "spot mode parks the beam on the sample"),
            ("line_scan", "DP_LINE_SCAN", {"off"}, "line scan refreshes one line only")):
        v = st[key]
        if v is None:
            warns.append(f"{mn} unreadable (not checked)")
        elif G.norm(v) not in ok:
            reasons.append(f"{mn} reads {v!r}: {why}")

    wd = st["wd_m"]
    if wd is None:
        reasons.append("cannot read AP_WD")
    elif not G.wd_in_window(wd, (lo, hi)):
        reasons.append(f"AP_WD reads {G.mm(wd)}, outside the {lo * 1e3:.1f}-{hi * 1e3:.1f} mm "
                       f"window: that reading does not make sense for this sample. Focus by "
                       f"hand near the sample first")

    c = st["contrast_pct"]
    if c is None:
        reasons.append("cannot read AP_CONTRAST (needed to guard contrast)")
    elif c < G.CONTRAST_MIN_PCT:
        reasons.append(f"AP_CONTRAST reads {c:.2f} % (the 'contrast 0' failure): set contrast "
                       f"by hand first")
    if st["brightness_pct"] is None:
        reasons.append("cannot read AP_BRIGHTNESS (needed to guard brightness)")
    av = st["auto_video"]
    if av is None:
        warns.append("DP_AUTO_VIDEO unreadable (auto BC not checked; contrast/brightness "
                     "are still watched every frame)")
    elif av.strip().lower() not in ("off", "0"):
        reasons.append(f"auto brightness/contrast is on (DP_AUTO_VIDEO = {av!r}): it would "
                       f"change the image gain between frames. Turn it off")
    if st["stage_z_m"] is None:
        reasons.append("cannot read AP_STAGE_AT_Z (needed to prove the stage does not move)")

    if not (0 < half_range_m <= G.HALF_RANGE_MAX_M):
        reasons.append(f"half-range {half_range_m * 1e6:.1f} um is outside "
                       f"(0, {G.HALF_RANGE_MAX_M * 1e6:.0f}] um")
    if coarse_points < 3:
        reasons.append("need at least 3 coarse points to bracket a peak")
    if max_frames < coarse_points + 1:
        reasons.append(f"max frames {max_frames} < coarse points {coarse_points} + 1 "
                       f"verification frame")

    plan: List[float] = []
    if wd is not None and G.wd_in_window(wd, (lo, hi)) and 0 < half_range_m and coarse_points >= 3:
        plan = _sweep.plan_voltages(wd, min(half_range_m, G.HALF_RANGE_MAX_M),
                                    coarse_points, (lo, hi))
        if len(plan) < 3:
            reasons.append("the sweep collapses to < 3 points inside the window")
    if len(plan) < 3 and not reasons:
        reasons.append("no valid sweep plan")          # belt and braces: never sweep blind
    return reasons, st, plan


def sem_autofocus(scope, *,
                  half_range_m: float = G.HALF_RANGE_DEFAULT_M,
                  coarse_points: int = G.COARSE_POINTS_DEFAULT,
                  fine_points: int = G.FINE_POINTS_DEFAULT,
                  fine_fraction: Optional[float] = G.FINE_FRACTION_DEFAULT,
                  refine: bool = True,
                  metric: str = _metrics.DEFAULT_METRIC,
                  crop_bar: bool = True,
                  presmooth_frac: float = _metrics.DEFAULT_PRESMOOTH_FRAC,
                  min_curve_contrast: float = G.MIN_CURVE_CONTRAST,
                  verify_min_ratio: float = G.VERIFY_MIN_RATIO,
                  max_frames: int = G.MAX_FRAMES_DEFAULT,
                  save_dir: Optional[str] = None,
                  plan_only: bool = False,
                  probe_step_m: float = G.PROBE_STEP_M,
                  log_preflight: bool = True,
                  logger: Optional[Callable[[str], None]] = None) -> SemAutofocusResult:
    """Focus the SEM by stepping AP_WD. See the module docstring for every guard."""
    log = logger or (lambda m: None)
    plog = log if log_preflight else (lambda m: None)
    if metric not in _metrics.METRICS:
        raise ValueError(f"unknown metric {metric!r}; choose from {sorted(_metrics.METRICS)}")
    if fine_fraction is not None and not 0 < fine_fraction <= G.FINE_FRACTION_MAX:
        raise ValueError(f"fine_fraction must be in (0, {G.FINE_FRACTION_MAX}]")
    if not 0 <= probe_step_m <= G.PROBE_STEP_MAX_M:
        raise ValueError(f"probe_step_m must be in [0, {G.PROBE_STEP_MAX_M * 1e6:.0f} um]")

    warns: List[str] = []
    reasons, st, plan = preflight(scope, half_range_m=half_range_m,
                                  coarse_points=coarse_points, max_frames=max_frames,
                                  warnings=warns)
    res = SemAutofocusResult(success=False, message="", metric=metric, state_before=st,
                             wd_start_m=st["wd_m"], coarse_plan_m=list(plan), warnings=warns)
    plog(f"[sem-af] start: WD {G.mm(st['wd_m'])}, mag {st['mag']}, contrast "
        f"{st['contrast_pct']} %, brightness {st['brightness_pct']} %, EHT {st['eht_v']} V, "
        f"imaging {st['imaging']!r}, auto BC {st['auto_video']!r}, noise reduction "
        f"{st['noise_reduction']!r}")
    for w in warns:
        plog(f"[sem-af] note: {w}")
    if reasons:
        res.refused = True
        res.reasons = reasons
        res.message = "REFUSED (nothing changed): " + "; ".join(reasons) + "."
        log("[sem-af] " + res.message)
        return res

    wd0 = float(st["wd_m"])
    lo, hi = scope.window
    clamped = plan[0] > wd0 - half_range_m + 1e-12 or plan[-1] < wd0 + half_range_m - 1e-12
    plog(f"[sem-af] coarse plan: {len(plan)} WDs {G.mm(plan[0])} .. {G.mm(plan[-1])} "
        f"(step {(plan[1] - plan[0]) * 1e6:.1f} um)"
        + (" -- clamped to the window" if clamped else ""))
    plog(f"[sem-af] at most {max_frames} SEM frames, ~{_estimate_s(scope, max_frames, st):.0f} s "
        f"(frame time read in the current mode)")
    if plan_only:
        res.planned = True
        res.message = "DRY RUN: guards pass; nothing was changed."
        return res

    frames_left = int(max_frames) - 1          # keep one for the verification frame
    prev = {"img": None}
    counter = {"n": 0}

    def _frame_path(wd: float, tag: str) -> Optional[str]:
        if not save_dir:
            return None
        counter["n"] += 1
        return os.path.join(save_dir, f"semaf_{counter['n']:02d}_{tag}_wd{wd * 1e3:.4f}mm.bmp")

    def _score_frame(wd: float, tag: str) -> float:
        path = _frame_path(wd, tag)
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
        # A reduced-raster box is narrower than the full frame at the same pixel size, so
        # scale the smoothing fraction to keep the validated PHYSICAL smoothing scale.
        s = float(_metrics.sharpness(img, metric,
                                     crop_bar=crop_bar and scope.frame_has_databar,
                                     presmooth_frac=presmooth_frac * scope.presmooth_scale))
        if not math.isfinite(s):
            raise SemAutofocusAbort(f"non-finite sharpness at WD {G.mm(wd)}")
        return s

    def _measure(wd: float, curve: List[Tuple[float, float]]) -> None:
        rb = scope.set_wd(wd)
        s = _score_frame(wd, "sweep")
        curve.append((wd, s))
        log(f"[sem-af] WD {G.mm(wd)} (reads {G.mm(rb)}) -> {metric} {s:.5g}")

    keep_new_focus = False
    curve: List[Tuple[float, float]] = []
    try:
        scope.prepare()
        res.raster_box = scope.box
        for n in scope.notes:
            res.warnings.append(n)
            log(f"[sem-af] note: {n}")
        # prepare() may have switched the imaging mode / mag: WD must still be the start.
        wd_now = scope.read_wd()
        if wd_now is None or abs(wd_now - wd0) > scope.wd_tol_m:
            raise SemAutofocusAbort(f"AP_WD reads {G.mm(wd_now)} after switching to SEM "
                                    f"imaging, not the start value {G.mm(wd0)}")
        # --- probe: a tiny WD step first, so a set that does not take (read-back) or a
        #     WD change that moves the stage (Z/WD link) is caught on a few-um move,
        #     before any 100-um sweep step. No image.
        if probe_step_m > 0:
            pw = wd0 - probe_step_m if wd0 - probe_step_m >= lo else wd0 + probe_step_m
            rb = scope.set_wd(pw)
            log(f"[sem-af] probe: WD {G.mm(pw)} reads {G.mm(rb)}, stage Z unchanged")
        # --- coarse ---
        for wd in plan:
            _measure(wd, curve)
            frames_left -= 1
        ref = _sweep.refine_peak(curve)
        # --- fine (only around an interior peak) ---
        if refine and ref.bracketed and frames_left >= 3:
            step = (plan[-1] - plan[0]) / max(1, len(plan) - 1)
            fine_half = step if fine_fraction is None else half_range_m * fine_fraction
            fine = _sweep.plan_voltages(ref.best_voltage, fine_half,
                                        min(fine_points, frames_left), (lo, hi))
            for wd in fine:
                if frames_left <= 0:
                    break
                if any(abs(wd - cw) < 0.1e-6 for cw, _ in curve):
                    continue
                _measure(wd, curve)
                frames_left -= 1
            ref = _sweep.refine_peak(curve)

        res.curve = sorted(curve)
        res.bracketed = bool(ref.bracketed)
        res.refined = bool(refine and ref.refined)
        res.curve_contrast = float(ref.contrast)
        res.best_score = float(ref.best_score)
        best = float(ref.estimated_voltage if res.refined else ref.best_voltage)
        res.wd_best_m = best
        wds = [w for w, _ in curve]

        # --- acceptance guards ---
        if res.curve_contrast < min_curve_contrast:
            raise SemAutofocusAbort(
                f"focus curve is flat (contrast {res.curve_contrast:.3f} < "
                f"{min_curve_contrast:.3f}): no reliable peak. Check the image has "
                f"features, or raise the mag")
        if not res.bracketed:
            edge = ref.best_voltage
            at_window = abs(edge - lo) < 1e-9 or abs(edge - hi) < 1e-9
            raise SemAutofocusAbort(
                f"sharpest frame is at the sweep edge (WD {G.mm(edge)}; swept "
                f"{G.mm(min(wds))}..{G.mm(max(wds))}), so the true focus was not bracketed"
                + (" -- and that edge is the 4-8 mm window limit: the reading does not make "
                   "sense" if at_window else " -- rerun with a wider --half-range-um"))
        if not G.wd_in_window(best, (lo, hi)) or not (min(wds) - 1e-9 <= best <= max(wds) + 1e-9):
            raise SemAutofocusAbort(f"fitted focus {G.mm(best)} is outside the window or the "
                                    f"swept range")

        # --- set it and verify with one more frame ---
        scope.set_wd(best)
        vs = _score_frame(best, "verify")
        res.verify_score = vs
        res.verify_ratio = vs / res.best_score if res.best_score > 0 else None
        log(f"[sem-af] verify at WD {G.mm(best)}: {metric} {vs:.5g} "
            f"({(res.verify_ratio or 0) * 100:.0f}% of the sweep peak)")
        if verify_min_ratio > 0 and (res.verify_ratio is None
                                     or res.verify_ratio < verify_min_ratio):
            raise SemAutofocusAbort(
                f"focus did not reproduce: the verification frame scored "
                f"{(res.verify_ratio or 0) * 100:.0f}% of the sweep peak (< "
                f"{verify_min_ratio * 100:.0f}%). Lens hysteresis or drift?")
        keep_new_focus = True
        res.success = True
        res.message = (f"Focus set: WD {G.mm(wd0)} -> {G.mm(best)} "
                       f"({(best - wd0) * 1e6:+.1f} um).")
    except SemAutofocusAbort as e:
        res.message = f"ABORTED: {str(e).rstrip('.')}."
        res.reasons.append(str(e))
        log(f"[sem-af] {res.message}")
    except G.SemAutofocusSafetyError as e:
        res.message = f"SAFETY STOP: {e}"
        res.reasons.append(str(e))
        log(f"[sem-af] {res.message}")
    except Exception as e:  # noqa: BLE001 -- COM errors etc.: show the real error (bug #6)
        res.message = f"ERROR: {type(e).__name__}: {e!r}"
        res.reasons.append(res.message)
        log(f"[sem-af] {res.message}")
    finally:
        # WD first (the thing that matters for the next image), then mag / scan rate.
        if not keep_new_focus and scope.wd_set_attempts:
            try:
                rb = scope.set_wd(wd0)
                res.restored = True
                log(f"[sem-af] WD restored to the start: {G.mm(rb)}")
            except Exception as e:  # noqa: BLE001
                res.restore_failed = True
                log(f"[sem-af] *** WARNING: could not restore WD to {G.mm(wd0)} ({e!r}). "
                    f"CHECK SEM FOCUS BY HAND. ***")
        scope.restore()
        res.wd_final_m = scope.read_wd()
        res.wd_readbacks = list(scope.wd_log)
        res.frame_timing_s = list(scope.frame_log)
        if not res.curve and curve:
            res.curve = sorted(curve)
    return res
