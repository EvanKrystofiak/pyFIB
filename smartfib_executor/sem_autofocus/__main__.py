"""SEM autofocus CLI (fork). Steps AP_WD; never runs SmartSEM's own autofocus.

    python -m smartfib_executor.sem_autofocus              # DRY RUN: read-only checks + plan
    python -m smartfib_executor.sem_autofocus --arm        # focus (asks to confirm)
    python -m smartfib_executor.sem_autofocus --simulate   # offline, on the simulator

Since v0.48.0 the default is TWO-STAGE (two_stage.py, designed from the measured focus
curves of 2026-10-05): coarse at 500x / scan speed 4 / box 0.35, WD +/-600 um in 200 um
steps (extends at an edge, inside 4-8 mm); then fine at --sem-mag (default: the current
mag) / speed 4 / box 0.35, +/-90 um in 30 um steps + 2 halfway + verify. ~35-55 s.
At the end the mag / scan speed / full frame are back and the scan is PAUSED (v0.49.0).
--single-stage runs the previous search (+/-60 um, 10 um steps, speed 8 by default).

Useful options:
    --sem-mag 2000         set the SEM mag for the run (restored after); default: leave it
    --scan-rate 8          SEM scan speed for the run (restored after); default 8, -1 = leave it
    --raster-frac 0.5      scan only a centred box this fraction of the width/height
                           (default 0.5 = 1/4 of the area); --full-frame to scan it all
    --half-range-um 60     sweep WD start +/- this (<= 1000); default 60
    --points 13            coarse points (default 13 = 10 um steps); the fine sweep adds
                           points halfway between coarse points around the peak

Armed runs save every frame, curve.csv and results.json to sem_autofocus_<time>\\.
Exit code: 0 focus set (or dry run passed), 1 focus rejected (WD restored), 2 refused /
error, 3 WD could NOT be confirmed back at its start value (check SEM focus by hand).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import asdict

from ..autofocus.metrics import METRICS, DEFAULT_METRIC
from . import guards as G
from .scope import SemFocusScope, FakeClock, DEFAULT_SETTLE_S, DEFAULT_FRAME_WAIT_S
from .search import sem_autofocus
from . import two_stage as _TS
from .sim import SimulatedSem

SIM_FAULTS = {
    "none": {},
    "contrast0": {"contrast_zero_after": 3},
    "readback": {"wd_readback_offset_m": 8e-6},
    "stale": {"stale_after": 2},
    "zlink": {"z_follows_wd": True},
    "featureless": {"featureless": True},
    "autobc": {"auto_video": "BC"},
    "outside": {"wd_m": 9.5e-3},
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m smartfib_executor.sem_autofocus",
                                description="pyFIB SEM autofocus (steps AP_WD; 4-8 mm window).")
    p.add_argument("--arm", action="store_true",
                   help="Actually focus. Without it: dry run (reads only, prints the plan).")
    p.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    p.add_argument("--simulate", action="store_true", help="Run on the built-in simulator.")
    p.add_argument("--single-stage", action="store_true",
                   help="The previous single-stage search instead of coarse + fine.")
    p.add_argument("--coarse-mag", type=float, default=_TS.COARSE_MAG,
                   help="Two-stage: coarse-stage SEM mag (default 500).")
    p.add_argument("--coarse-speed", type=int, default=_TS.COARSE_SPEED,
                   help="Two-stage: coarse scan speed (default 4).")
    p.add_argument("--coarse-half-range-um", type=float, default=_TS.COARSE_HALF_RANGE_M * 1e6,
                   help="Two-stage: coarse start range +/- (default 600; extends at an edge).")
    p.add_argument("--coarse-step-um", type=float, default=_TS.COARSE_STEP_M * 1e6,
                   help="Two-stage: coarse WD step (default 200).")
    p.add_argument("--fine-speed", type=int, default=_TS.FINE_SPEED,
                   help="Two-stage: fine scan speed (default 4).")
    p.add_argument("--fine-half-range-um", type=float, default=_TS.FINE_HALF_RANGE_M * 1e6,
                   help="Two-stage: fine range +/- around the coarse result (default 90).")
    p.add_argument("--fine-step-um", type=float, default=_TS.FINE_STEP_M * 1e6,
                   help="Two-stage: fine WD step (default 30).")
    p.add_argument("--fine-min-contrast", type=float, default=_TS.FINE_MIN_CURVE_CONTRAST,
                   help="Two-stage: minimum fine-stage curve contrast (default 0.2; the "
                        "coarse stage uses --min-contrast).")
    p.add_argument("--jump-settle-s", type=float, default=_TS.BIG_JUMP_SETTLE_S,
                   help="Two-stage: extra settle after a WD jump >= 0.3 mm (default 2).")
    p.add_argument("--half-range-um", type=float, default=G.HALF_RANGE_DEFAULT_M * 1e6)
    p.add_argument("--points", type=int, default=G.COARSE_POINTS_DEFAULT,
                   help="Coarse sweep points (default 9).")
    p.add_argument("--fine-points", type=int, default=G.FINE_POINTS_DEFAULT)
    p.add_argument("--no-fine", action="store_true", help="Coarse sweep + parabola only.")
    p.add_argument("--max-frames", type=int, default=G.MAX_FRAMES_DEFAULT,
                   help="Cap on SEM frames incl. the verification frame (default 25).")
    p.add_argument("--sem-mag", type=float, default=None,
                   help="SEM magnification for the run (restored afterwards).")
    p.add_argument("--scan-rate", type=int, default=G.SCAN_RATE_DEFAULT,
                   help="Single-stage: SEM scan speed 1-15, restored afterwards (default 8; "
                        "-1 leaves it unchanged). Two-stage uses --coarse/--fine-speed.")
    p.add_argument("--metric", choices=sorted(METRICS), default=DEFAULT_METRIC)
    p.add_argument("--raster-frac", type=float, default=None,
                   help="Reduced raster: scan only a centred box this fraction of the "
                        "screen width and height (default 0.35 two-stage, 0.5 single-stage; "
                        "min 0.1). Restored to the full frame afterwards.")
    p.add_argument("--full-frame", action="store_true",
                   help="Scan the full frame (no reduced raster).")
    p.add_argument("--min-contrast", type=float, default=G.MIN_CURVE_CONTRAST,
                   help="Minimum focus-curve contrast to trust a peak (default 0.3).")
    p.add_argument("--verify-min-ratio", type=float, default=G.VERIFY_MIN_RATIO,
                   help="Verification frame must score >= this x the sweep peak (default "
                        "0.6; 0 disables).")
    p.add_argument("--wd-tol-um", type=float, default=G.WD_READBACK_TOL_M * 1e6,
                   help="Allowed AP_WD set/read-back difference (default 2 um).")
    p.add_argument("--settle-s", type=float, default=DEFAULT_SETTLE_S)
    p.add_argument("--frame-wait-s", type=float, default=DEFAULT_FRAME_WAIT_S,
                   help="Minimum live scan before each freeze (raised automatically from "
                        "AP_FRAME_TIME).")
    p.add_argument("--out", default=None, help="Output folder (default sem_autofocus_<time>).")
    p.add_argument("--no-save-frames", action="store_true")
    p.add_argument("--sim-true-wd-mm", type=float, default=5.03)
    p.add_argument("--sim-start-wd-mm", type=float, default=5.00)
    p.add_argument("--sim-fault", choices=sorted(SIM_FAULTS), default="none")
    return p


def _write_outputs(out_dir: str, res, simulated: bool) -> None:
    d = asdict(res)
    d["simulated"] = simulated
    with open(os.path.join(out_dir, "results.json"), "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=2, default=str)
    with open(os.path.join(out_dir, "curve.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["wd_mm", res.metric])
        for wd, s in res.curve:
            w.writerow([f"{wd * 1e3:.5f}", f"{s:.6g}"])


def _summary(res) -> str:
    L = ["", "=" * 64, "SEM AUTOFOCUS " + ("OK" if res.success else
                                          ("DRY RUN" if res.planned else
                                           ("REFUSED" if res.refused else "REJECTED"))),
         f"  {res.message}"]
    if not (res.planned or res.refused):
        L.append(f"  start WD {G.mm(res.wd_start_m)} | best {G.mm(res.wd_best_m)} | "
                 f"now {G.mm(res.wd_final_m)}")
    if res.curve:
        L.append("  scanned: " + ("reduced raster %dx%d at (%d,%d)" % (
            res.raster_box[2], res.raster_box[3], res.raster_box[0], res.raster_box[1])
            if res.raster_box else "full frame"))
        L.append(f"  frames {res.n_frames} | curve contrast {res.curve_contrast:.3f} | "
                 f"bracketed {res.bracketed} | verify "
                 + ("n/a" if res.verify_ratio is None else f"{res.verify_ratio * 100:.0f}%"))
        if res.wd_readbacks:
            worst = max((abs(rb - s) for s, rb in res.wd_readbacks if rb is not None),
                        default=0.0)
            L.append(f"  AP_WD set vs read-back: worst {worst * 1e6:.2f} um over "
                     f"{len(res.wd_readbacks)} sets")
    if res.restored:
        L.append("  AP_WD was put back to the start value.")
    for w in res.warnings:
        L.append(f"  note: {w}")
    if res.frame_timing_s:
        lf = [a for a, _ in res.frame_timing_s]
        L.append(f"  measured live->frozen per frame: {min(lf):.2f}-{max(lf):.2f} s "
                 f"(AP_FRAME_TIME before the run, full frame: {res.state_before.get('frame_time_raw')} ms)")
    if res.restore_failed:
        L.append(f"  *** AP_WD could NOT be confirmed back at {G.mm(res.wd_start_m)} (reads "
                 f"{G.mm(res.wd_final_m)}). CHECK SEM FOCUS BY HAND. ***")
    L.append("=" * 64)
    return "\n".join(L)


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    sem = None
    if a.simulate:
        kw = dict(true_wd_m=a.sim_true_wd_mm * 1e-3, wd_m=a.sim_start_wd_mm * 1e-3)
        kw.update(SIM_FAULTS[a.sim_fault])
        target = SimulatedSem(**kw)
        clock = FakeClock()
    else:
        try:
            from ..smartsem_com import SmartSEM
        except Exception as e:  # pragma: no cover
            print(f"ERROR: could not import SmartSEM (not on the SmartSEM PC?): {e}",
                  file=sys.stderr)
            return 2
        try:
            sem = SmartSEM()
            try:
                sem.connect_remoting()
            except Exception:
                sem.connect_local()
        except Exception as e:  # noqa: BLE001
            print(f"ERROR: could not connect to SmartSEM: {e!r}", file=sys.stderr)
            return 2
        target = sem
        clock = None
    if not a.single_stage:
        return _main_two_stage(a, target, clock, sem)
    try:
        scope = SemFocusScope(target, magnification=a.sem_mag,
                              scan_rate=(None if a.scan_rate is None or a.scan_rate < 0
                                         else a.scan_rate),
                              settle_s=a.settle_s, frame_wait_s=a.frame_wait_s,
                              wd_tol_m=a.wd_tol_um * 1e-6, clock=clock, logger=print,
                              raster_frac=_frac(a, G.RASTER_FRAC_DEFAULT))
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        if sem is not None:
            sem.close()
        return 2
    kwargs = dict(half_range_m=a.half_range_um * 1e-6, coarse_points=a.points,
                  fine_points=a.fine_points, refine=not a.no_fine, metric=a.metric,
                  min_curve_contrast=a.min_contrast, verify_min_ratio=a.verify_min_ratio,
                  max_frames=a.max_frames, logger=print)
    try:
        armed = a.arm or a.simulate
        plan = sem_autofocus(scope, plan_only=True, **kwargs)
        if plan.refused or not armed:
            print(_summary(plan))
            if not plan.refused:
                print("Nothing was changed. Add --arm to focus.")
            return 2 if plan.refused else 0
        if not a.simulate and not a.yes:
            print("This steps the SEM focus (AP_WD) inside the plan above and images with the "
                  "SEM. Contrast, brightness and the stage are never touched.")
            try:
                ans = input("Proceed? [yes/N] ").strip().lower()
            except EOFError:
                ans = ""
            if ans not in ("y", "yes"):
                print("Cancelled; nothing changed.")
                return 2
        out_dir = None
        if a.out or not a.simulate:
            out_dir = os.path.abspath(a.out or time.strftime("sem_autofocus_%Y%m%d_%H%M%S"))
            os.makedirs(out_dir, exist_ok=True)
        res = sem_autofocus(scope, log_preflight=False, save_dir=(None if (a.no_save_frames or out_dir is None)
                                             else out_dir), **kwargs)
        print(_summary(res))
        if out_dir:
            _write_outputs(out_dir, res, a.simulate)
            print(f"Saved to {os.path.abspath(out_dir)}")
        if res.success:
            return 0
        if res.restore_failed:
            return 3
        return 2 if res.refused else 1
    finally:
        if sem is not None:
            try:
                sem.close()
            except Exception:
                pass


def _frac(a, default):
    if a.full_frame:
        return None
    f = default if a.raster_frac is None else a.raster_frac
    return None if f >= 1.0 else f


def _two_stage_lines(res) -> str:
    L = []
    if res.coarse_curve:
        L.append(f"  coarse ({res.coarse_mag:g}x): {res.coarse_frames} frames, contrast "
                 f"{res.coarse_contrast:.3f}, result {G.mm(res.coarse_best_m)}"
                 + (f", extended {res.coarse_extensions}x" if res.coarse_extensions else ""))
    if res.curve:
        L.append(f"  fine ({res.fine_mag}x): {res.fine_frames} frames"
                 + (f", extended {res.fine_extensions}x" if res.fine_extensions else ""))
    return "\n".join(L)


def _main_two_stage(a, target, clock, sem) -> int:
    kwargs = dict(fine_mag=a.sem_mag, coarse_mag=a.coarse_mag, coarse_speed=a.coarse_speed,
                  fine_speed=a.fine_speed, coarse_half_range_m=a.coarse_half_range_um * 1e-6,
                  coarse_step_m=a.coarse_step_um * 1e-6,
                  fine_half_range_m=a.fine_half_range_um * 1e-6,
                  fine_step_m=a.fine_step_um * 1e-6, raster_frac=_frac(a, _TS.BOX_FRAC),
                  metric=a.metric, min_curve_contrast=a.min_contrast,
                  fine_min_curve_contrast=a.fine_min_contrast,
                  big_jump_settle_s=a.jump_settle_s,
                  verify_min_ratio=a.verify_min_ratio, settle_s=a.settle_s,
                  frame_wait_s=a.frame_wait_s, wd_tol_m=a.wd_tol_um * 1e-6, clock=clock,
                  logger=print)
    try:
        try:
            plan = _TS.sem_autofocus_two_stage(target, plan_only=True, **kwargs)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        armed = a.arm or a.simulate
        if plan.refused or not armed:
            print(_summary(plan))
            if not plan.refused:
                print("Nothing was changed. Add --arm to focus.")
            return 2 if plan.refused else 0
        if not a.simulate and not a.yes:
            print(f"This steps the SEM focus (AP_WD): first at {a.coarse_mag:g}x, then at the "
                  f"working mag, and images with the SEM. Mag, scan speed and scan box are "
                  f"put back. Contrast, brightness and the stage are never touched.")
            try:
                ans = input("Proceed? [yes/N] ").strip().lower()
            except EOFError:
                ans = ""
            if ans not in ("y", "yes"):
                print("Cancelled; nothing changed.")
                return 2
        out_dir = None
        if a.out or not a.simulate:
            out_dir = os.path.abspath(a.out or time.strftime("sem_autofocus_%Y%m%d_%H%M%S"))
            os.makedirs(out_dir, exist_ok=True)
        t0 = time.monotonic()
        res = _TS.sem_autofocus_two_stage(
            target, save_dir=(None if (a.no_save_frames or out_dir is None) else out_dir),
            log_preflight=False, **kwargs)
        print(_summary(res))
        print(_two_stage_lines(res))
        if not a.simulate:
            print(f"  took {time.monotonic() - t0:.0f} s")
        if out_dir:
            _write_outputs(out_dir, res, a.simulate)
            with open(os.path.join(out_dir, "coarse_curve.csv"), "w", newline="",
                      encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["wd_mm", res.metric])
                for wd, sc in res.coarse_curve:
                    w.writerow([f"{wd * 1e3:.5f}", f"{sc:.6g}"])
            print(f"Saved to {os.path.abspath(out_dir)}")
        if res.success:
            return 0
        if res.restore_failed:
            return 3
        return 2 if res.refused else 1
    finally:
        if sem is not None:
            try:
                sem.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
