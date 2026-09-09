"""Standalone runner for the position-refinement fork.

Offline demo (no hardware) -- prove the closed loop converges on the simulator:
    python -m smartfib_executor.refine --simulate --drift-um 4

Real instrument (Windows + SmartSEM; instrument-untested path):
    python -m smartfib_executor.refine --reference C:\\path\\to\\ref.bmp \\
        --magnification 2000 [--enable-stage] [--dry-run]

The reference BMP and its magnification are exactly what positions.py stored in a
PositionRecord (image_bmp + fib_magnification_x). Stage correction is DISABLED
unless --enable-stage is passed (and its sign/rotation is uncalibrated).
"""

from __future__ import annotations

import argparse
import sys

import numpy as np

from .refine import refine_position
from .geometry import BEAM_SHIFT_THRESHOLD_M, BEAM_SIGN_X, BEAM_SIGN_Y
from .calibrate import (calibrate_beam_shift, load_calibrated_signs,
                        DEFAULT_CALIBRATION_FILENAME)
from .sources import SimulatedRefineScope


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m smartfib_executor.refine",
                                description="Position refinement by cross-correlation.")
    p.add_argument("--simulate", action="store_true",
                   help="Run against the simulator (no hardware).")
    p.add_argument("--drift-um", type=float, default=4.0,
                   help="[sim] Introduced drift magnitude in microns (default 4).")
    p.add_argument("--seed", type=int, default=5, help="[sim] RNG seed.")
    # real mode
    p.add_argument("--reference", help="[real] Path to the reference BMP (image_bmp).")
    p.add_argument("--scan-rate", type=int, default=8,
                   help="[real] DP_SCANRATE for correlation frames (higher = more dwell "
                        "= better SNR). Use -1 to leave the scan rate unchanged.")
    p.add_argument("--scan-rate-method", choices=("set", "cmd", "set_then_cmd"),
                   default="cmd",
                   help="How to change DP_SCANRATE: 'set' (direct, default), 'cmd' "
                        "(CMD_SCANRATE#), or 'set_then_cmd' (set, fall back to cmd).")
    p.add_argument("--magnification", type=float, default=2000.0,
                   help="Magnification of the reference (fib_magnification_x).")
    p.add_argument("--enable-stage", action="store_true",
                   help="Allow stage X/Y correction for drift > threshold "
                        "(UNCALIBRATED; off by default).")
    p.add_argument("--max-iterations", type=int, default=2)
    p.add_argument("--no-verify", action="store_true",
                   help="Skip the closed-loop residual check (not recommended).")
    p.add_argument("--dry-run", action="store_true",
                   help="[real] Log intended sets/moves without touching hardware.")
    # --- calibration ---
    p.add_argument("--calibrate", action="store_true",
                   help="MEASURE the beam-shift sign and scale instead of refining: "
                        "applies a known shift on each axis, cross-correlates before/"
                        "after, and reports which way the image actually moves. Also "
                        "reports the safe raster side for the autofocus fork. "
                        "Grabs ~3 FIB frames -- use a sacrificial area.")
    p.add_argument("--test-shift-um", type=float, default=2.0,
                   help="[calibrate] Known shift applied per axis, in microns "
                        "(default 2).")
    p.add_argument("--calibration-file", default=DEFAULT_CALIBRATION_FILENAME,
                   help=f"Where to save/load the measured calibration "
                        f"(default {DEFAULT_CALIBRATION_FILENAME}).")
    p.add_argument("--save-calibration", action="store_true",
                   help="[calibrate] Write the result to --calibration-file so later "
                        "refine runs use the measured signs.")
    p.add_argument("--ignore-calibration", action="store_true",
                   help="Do not load a saved calibration; use the built-in defaults.")
    return p


def _resolve_signs(args):
    """Prefer a measured calibration over the built-in defaults."""
    if args.ignore_calibration:
        return BEAM_SIGN_X, BEAM_SIGN_Y, "built-in defaults (calibration ignored)"
    signs = load_calibrated_signs(args.calibration_file)
    if signs is None:
        return (BEAM_SIGN_X, BEAM_SIGN_Y,
                f"built-in defaults (no {args.calibration_file} found -- consider "
                f"running --calibrate)")
    return signs[0], signs[1], f"measured calibration from {args.calibration_file}"


def _report(res) -> int:
    print("\n--- result ---")
    print(f"  success        : {res.success}")
    print(f"  method         : {res.method}"
          + ("  (STAGE DISABLED)" if res.stage_required_but_disabled else ""))
    print(f"  initial drift  : dx={res.initial_drift_x_m * 1e6:+.2f} um "
          f"dy={res.initial_drift_y_m * 1e6:+.2f} um "
          f"|d|={res.initial_drift_mag_m * 1e6:.2f} um")
    if res.residual_drift_m == res.residual_drift_m:  # not NaN
        print(f"  residual       : {res.residual_drift_m * 1e6:.2f} um")
    print(f"  beam delta     : ({res.applied_beam_shift_delta_m[0] * 1e6:+.2f}, "
          f"{res.applied_beam_shift_delta_m[1] * 1e6:+.2f}) um")
    print(f"  frames         : {res.frames}   iterations: {res.iterations}   "
          f"snr: {res.snr:.1f}")
    print(f"  message        : {res.message}")
    return 0 if res.success else 2


def _run_simulated(args) -> int:
    px_per_um = 1e-6 / (0.1143 / args.magnification / 1024)
    ox = args.drift_um * px_per_um * 0.7
    oy = -args.drift_um * px_per_um * 0.7
    scope = SimulatedRefineScope(magnification=args.magnification,
                                 true_offset_px=(oy, ox), seed=args.seed)
    if args.calibrate:
        cal = calibrate_beam_shift(scope, args.magnification,
                                   test_shift_m=args.test_shift_um * 1e-6,
                                   logger=print)
        print()
        print(cal.summary())
        if args.save_calibration:
            cal.save(args.calibration_file)
            print(f"\nSaved calibration -> {args.calibration_file}")
        return 0
    sx, sy, src = _resolve_signs(args)
    print(f"[refine] using beam signs ({sx:+.0f}, {sy:+.0f}) from {src}")
    res = refine_position(scope, scope.reference_image(), args.magnification,
                          enable_stage=args.enable_stage,
                          max_iterations=args.max_iterations,
                          beam_sign_x=sx, beam_sign_y=sy,
                          verify=not args.no_verify, logger=print)
    return _report(res)


def _run_real(args) -> int:
    if not args.reference and not args.calibrate:
        print("ERROR: --reference BMP is required in real mode.", file=sys.stderr)
        return 2
    try:
        from PIL import Image
    except Exception as e:  # pragma: no cover
        print(f"ERROR: Pillow required to read the reference BMP: {e}", file=sys.stderr)
        return 2
    try:
        from ..smartsem_com import SmartSEM
    except Exception as e:  # pragma: no cover
        print(f"ERROR: could not import SmartSEM (pywin32 missing / not on the "
              f"SmartSEM PC?): {e}", file=sys.stderr)
        return 2
    from .sources import ScopeRefineAdapter

    reference = None
    if args.reference:
        with Image.open(args.reference) as im:
            reference = np.asarray(im.convert("L"), dtype=np.float64)

    sem = SmartSEM()
    try:
        sem.connect_remoting()
    except Exception:
        sem.connect_local()

    scope = ScopeRefineAdapter(sem, dry_run=args.dry_run,
                               imaging_scan_rate=(None if args.scan_rate < 0 else args.scan_rate),
                               scan_rate_method=args.scan_rate_method, logger=print)
    try:
        if args.calibrate:
            cal = calibrate_beam_shift(scope, args.magnification,
                                       test_shift_m=args.test_shift_um * 1e-6,
                                       logger=print)
            print()
            print(cal.summary())
            if args.save_calibration:
                cal.save(args.calibration_file)
                print(f"\nSaved calibration -> {args.calibration_file}")
            return 0
        sx, sy, src = _resolve_signs(args)
        print(f"[refine] using beam signs ({sx:+.0f}, {sy:+.0f}) from {src}")
        res = refine_position(scope, reference, args.magnification,
                              enable_stage=args.enable_stage,
                              max_iterations=args.max_iterations,
                              beam_sign_x=sx, beam_sign_y=sy,
                              verify=not args.no_verify, logger=print)
    finally:
        sem.close()
    return _report(res)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return _run_simulated(args) if args.simulate else _run_real(args)


if __name__ == "__main__":
    raise SystemExit(main())
