"""Standalone runner for the FIB autofocus fork.

Offline demo (no hardware) -- prove the search converges on the simulator:
    python -m smartfib_executor.autofocus --simulate --true-focus 30 --start 24

Real instrument (Windows + SmartSEM; instrument-untested code path):
    python -m smartfib_executor.autofocus --vmin 0 --vmax 60 \
        --half-range 6 --coarse-points 7 --metric tenengrad \
        --roi 256 192 512 384 --settle 0.3

Add --dry-run to the real mode to log the intended set/grab calls without moving
the column. Real mode requires pywin32 + a running EM Server.
"""

from __future__ import annotations

import argparse
import sys

from .search import autofocus
from .metrics import METRICS, DEFAULT_METRIC, DEFAULT_PRESMOOTH_FRAC


def _presmooth(args) -> float:
    """Resolve the pre-smoothing fraction (None -> calibrated default)."""
    return DEFAULT_PRESMOOTH_FRAC if args.presmooth_frac is None else args.presmooth_frac
from .sources import SimulatedImageSource


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--half-range", type=float, default=150.0,
                   help="Half the total sweep width, in VOLTS (default 150). Calibrated "
                        "from real 2000x data: focus sat at ~16.4 kV and the sharpness "
                        "peak spanned roughly +/-150-200 V.")
    p.add_argument("--coarse-points", type=int, default=7,
                   help="Number of coarse sweep points (default 7).")
    p.add_argument("--fine-points", type=int, default=5,
                   help="Number of fine sweep points (default 5).")
    p.add_argument("--metric", choices=sorted(METRICS), default=DEFAULT_METRIC,
                   help=f"Sharpness metric (default {DEFAULT_METRIC}).")
    p.add_argument("--scan-rate", type=int, default=8,
                   help="[real] DP_SCANRATE for focus frames (higher = more dwell = "
                        "better SNR). Use -1 to leave the scan rate unchanged.")
    p.add_argument("--scan-rate-method", choices=("set", "cmd", "set_then_cmd"),
                   default="cmd",
                   help="How to change DP_SCANRATE: 'set' (direct, default), 'cmd', "
                        "or 'set_then_cmd'.")
    p.add_argument("--analysis-height-frac", type=float, default=1.0,
                   help="Keep only this central fraction of ROWS when scoring focus "
                        "(0<f<=1). At grazing FIB incidence the box top/bottom sit at a "
                        "different defocus; e.g. 0.5 scores only the central half. "
                        "Default 1.0 (all rows).")
    p.add_argument("--raster-h-scale", type=float, default=None,
                   help="Height of the reduced-raster box as a fraction of the field. "
                        "Lower = a SHORTER box (less defocus spread and less dose). "
                        "Default keeps the built-in value.")
    p.add_argument("--no-refine", action="store_true",
                   help="Skip fine sweep + parabola refinement.")
    p.add_argument("--max-frames", type=int, default=None,
                   help="Cap total acquisitions (ion dose control).")
    p.add_argument("--min-contrast", type=float, default=0.15,
                   help="Confidence floor for score contrast (default 0.15). Real FIB "
                        "data with preprocessing gives ~0.25-0.45 when focus is found.")
    p.add_argument("--presmooth-frac", type=float, default=None,
                   help="Low-pass sigma as a fraction of image width before scoring "
                        "(default 0.004 = 0.4%%). Critical on noisy FIB frames: raises "
                        "peak contrast ~5x. Use 0 to disable.")
    p.add_argument("--no-crop-databar", action="store_true",
                   help="Do not auto-detect/remove the SmartSEM databar before scoring.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m smartfib_executor.autofocus",
                                description="FIB autofocus (fork).")
    p.add_argument("--simulate", action="store_true",
                   help="Run against the built-in simulator (no hardware).")
    # simulator options
    p.add_argument("--true-focus", type=float, default=16425.0,
                   help="[sim] True focus voltage the simulator peaks at "
                        "(default 16425 = the value measured from real data).")
    p.add_argument("--start", type=float, default=None,
                   help="Start / last-best voltage to sweep around "
                        "(sim: defaults to true-focus + 100).")
    p.add_argument("--noise", type=float, default=10.4,
                   help="[sim] Additive noise sigma in grey levels (default 10.4 = the "
                        "noise measured on real slow-dwell 512x384 FIB frames).")
    p.add_argument("--seed", type=int, default=0, help="[sim] RNG seed.")
    # real-instrument options
    p.add_argument("--vmin", type=float, help="[real] Minimum safe objective voltage.")
    p.add_argument("--vmax", type=float, help="[real] Maximum safe objective voltage.")
    p.add_argument("--settle", type=float, default=None,
                   help="[real] Seconds to wait after setting the objective voltage, "
                        "before imaging (default 1.0).")
    p.add_argument("--frame-wait", type=float, default=None,
                   help="[real] Seconds to scan after unfreezing, before freezing and "
                        "grabbing (default 5.0). MUST exceed the frame cycle time "
                        "(~1.3 s at the measured settings) or Grab() silently returns "
                        "the PREVIOUS frame and the whole sweep reads identical.")
    p.add_argument("--fib-mag", type=float, default=None,
                   help="[real] AP_FIB_MAGNIFICATION to set before focusing "
                        "(default 2000, matching the main workflow and the conditions "
                        "the focus metric was calibrated at). Use 0 to leave it alone.")
    p.add_argument("--beam-shift-x", type=float, default=None,
                   help="[real] AP_FIB_BEAM_SHIFT_X in METRES to park the beam off the "
                        "area of interest while focusing (default 1e-05 = 10 um). "
                        "FIB imaging sputters, so focusing happens on a throwaway patch.")
    p.add_argument("--beam-shift-return", type=float, default=None,
                   help="[real] AP_FIB_BEAM_SHIFT_X in METRES to return to when done "
                        "(default 0.0). Always applied, even on error.")
    p.add_argument("--raster-side", choices=("left", "right", "auto"), default=None,
                   help="[real] Scan ONLY one side of the field using a reduced raster, "
                        "so the beam never touches the area of interest. 'auto' picks "
                        "the side away from the target from the beam-shift sign. NOTE: "
                        "Grab()'s --roi only CROPS the framestore and saves no dose; "
                        "this is the flag that actually shrinks the burned area.")
    p.add_argument("--raster-fraction", type=float, default=0.5,
                   help="[real] Fraction of the field width to scan with --raster-side "
                        "(default 0.5 = half).")
    p.add_argument("--reduced-raster", type=int, nargs=4, metavar=("X", "Y", "W", "H"),
                   help="[real] Explicit reduced-raster rectangle in screen pixels. "
                        "Overrides --raster-side.")
    p.add_argument("--screen-width", type=int, default=1024,
                   help="[real] Screen/store width in px used for raster geometry "
                        "(default 1024).")
    p.add_argument("--screen-height", type=int, default=768,
                   help="[real] Screen/store height in px (default 768).")
    p.add_argument("--no-beam-shift", action="store_true",
                   help="[real] Do not move the beam. Focusing then rasters the area of "
                        "interest itself and WILL sputter it.")
    p.add_argument("--no-freeze-on-end-frame", action="store_true",
                   help="[real] Do not set DP_FREEZE_ON=End Frame. Not recommended: a "
                        "mid-scan freeze can leave a half-scanned frame in the buffer.")
    p.add_argument("--roi", type=int, nargs=4, metavar=("XOFF", "YOFF", "W", "H"),
                   help="[real] Grab ROI (default 0 0 1024 768).")
    p.add_argument("--reduction", type=int, default=0,
                   help="[real] Grab subsampling factor (default 0 = none).")
    p.add_argument("--dry-run", action="store_true",
                   help="[real] Log intended set/grab calls; do not touch hardware.")
    _add_common(p)
    return p


def _run_simulated(args) -> int:
    start = args.start if args.start is not None else (args.true_focus + 100.0)
    src = SimulatedImageSource(
        true_focus=args.true_focus,
        start_voltage=start,
        noise_sigma=args.noise,
        seed=args.seed,
    )
    res = autofocus(
        src,
        half_range=args.half_range,
        coarse_points=args.coarse_points,
        metric=args.metric,
        crop_bar=not args.no_crop_databar,
        presmooth_frac=_presmooth(args),
        analysis_height_frac=args.analysis_height_frac,
        refine=not args.no_refine,
        fine_points=args.fine_points,
        min_contrast=args.min_contrast,
        max_frames=args.max_frames,
        logger=print,
    )
    print("\n--- result ---")
    print(f"  success        : {res.success}")
    print(f"  best voltage   : {res.best_voltage:.3f} V "
          f"(true focus was {args.true_focus:.3f} V, |err|="
          f"{abs(res.best_voltage - args.true_focus):.3f} V)")
    print(f"  metric/contrast: {res.metric} / {res.contrast:.3g}")
    print(f"  bracketed      : {res.bracketed}   refined: {res.refined}")
    print(f"  frames used    : {res.n_frames}")
    print(f"  message        : {res.message}")
    return 0 if res.success else 2


def _run_real(args) -> int:
    if args.vmin is None or args.vmax is None:
        print("ERROR: real mode requires --vmin and --vmax (safe voltage bounds).",
              file=sys.stderr)
        return 2
    try:
        from ..smartsem_com import SmartSEM  # lazy: needs pywin32
    except Exception as e:  # pragma: no cover
        print(f"ERROR: could not import SmartSEM from smartsem_com "
              f"(pywin32 missing, or not running on the SmartSEM PC?): {e}",
              file=sys.stderr)
        return 2
    from .sources import ScopeImageSource

    sem = SmartSEM()
    try:
        sem.connect_remoting()
    except Exception:
        sem.connect_local()

    from .sources import (DEFAULT_SETTLE_S, DEFAULT_FRAME_WAIT_S,
                          DEFAULT_FIB_MAGNIFICATION, DEFAULT_BEAM_SHIFT_X_M,
                          DEFAULT_BEAM_SHIFT_X_RETURN_M, DEFAULT_RASTER_H_SCALE)
    _h_scale = (args.raster_h_scale if args.raster_h_scale is not None
                else DEFAULT_RASTER_H_SCALE)
    src = ScopeImageSource(
        sem,
        bounds=(args.vmin, args.vmax),
        settle_s=DEFAULT_SETTLE_S if args.settle is None else args.settle,
        frame_wait_s=(DEFAULT_FRAME_WAIT_S if args.frame_wait is None
                      else args.frame_wait),
        freeze_on_end_frame=not args.no_freeze_on_end_frame,
        fib_magnification=(None if (args.fib_mag is not None and args.fib_mag <= 0)
                           else (DEFAULT_FIB_MAGNIFICATION if args.fib_mag is None
                                 else args.fib_mag)),
        beam_shift_x_m=(None if args.no_beam_shift
                        else (DEFAULT_BEAM_SHIFT_X_M if args.beam_shift_x is None
                              else args.beam_shift_x)),
        beam_shift_x_return_m=(DEFAULT_BEAM_SHIFT_X_RETURN_M
                               if args.beam_shift_return is None
                               else args.beam_shift_return),
        reduced_raster=(tuple(args.reduced_raster) if args.reduced_raster else None),
        raster_side=args.raster_side,
        raster_fraction=args.raster_fraction,
        screen_size=(args.screen_width, args.screen_height),
        roi=tuple(args.roi) if args.roi else None,
        grab_reduction=args.reduction,
        raster_h_scale=_h_scale,
        imaging_scan_rate=(None if args.scan_rate < 0 else args.scan_rate),
        scan_rate_method=args.scan_rate_method,
        dry_run=args.dry_run,
        logger=print,
    )
    try:
        res = autofocus(
            src,
            half_range=args.half_range,
            coarse_points=args.coarse_points,
            metric=args.metric,
            crop_bar=not args.no_crop_databar,
            presmooth_frac=_presmooth(args),
            analysis_height_frac=args.analysis_height_frac,
            refine=not args.no_refine,
            fine_points=args.fine_points,
            min_contrast=args.min_contrast,
            max_frames=args.max_frames,
            logger=print,
        )
    finally:
        sem.close()

    print("\n--- result ---")
    print(f"  frames      : {res.n_frames} "
          f"(~{res.n_frames * (src.settle_s + src.frame_wait_s):.0f} s of FIB exposure)")
    print(f"  success     : {res.success}")
    print(f"  best voltage: {res.best_voltage:.3f} V")
    print(f"  metric      : {res.metric}   contrast: {res.contrast:.3g}")
    print(f"  bracketed   : {res.bracketed}   refined: {res.refined}")
    print(f"  frames used : {res.n_frames}")
    print(f"  message     : {res.message}")
    return 0 if res.success else 2


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.simulate:
        return _run_simulated(args)
    return _run_real(args)


if __name__ == "__main__":
    raise SystemExit(main())
