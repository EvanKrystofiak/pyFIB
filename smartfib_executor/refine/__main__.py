"""Standalone runner for the position-refinement fork.

Offline demo (no hardware) -- prove the closed loop converges on the simulator:
    python -m smartfib_executor.refine --simulate --drift-um 4

Real instrument (Windows + SmartSEM; instrument-untested path):
    python -m smartfib_executor.refine --reference C:\\path\\to\\ref.bmp \\
        --magnification 2000 [--enable-stage] [--dry-run]

The reference BMP and its magnification are exactly what positions.py stored in a
PositionRecord (image_bmp + fib_magnification_x). Stage correction is DISABLED
unless --enable-stage is passed. Its signs and tilt-dependent scale are MEASURED
(2026-09-22); it is REFINE-ONLY limited: X/Y only, <= 100 um per axis (refused
above), one move, undone unless drift drops below 50%, tilt within the model range
and R equal to the calibration R. Real mode asks for confirmation before running.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np

from .refine import refine_position, refine_two_stage, TwoStageResult
from .geometry import BEAM_SHIFT_THRESHOLD_M, BEAM_SIGN_X, BEAM_SIGN_Y
from .calibrate_stage import (calibrate_stage, DEFAULT_STAGE_CALIBRATION_FILENAME,
                              DEFAULT_STAGE_TEST_STEP_M, REFINE_STAGE_MAX_M)
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
                   help="Allow ONE stage X/Y correction for drift beyond the beam-shift "
                        "window (measured signs + tilt model; <= 100 um/axis, refused "
                        "above; undone unless drift halves; off by default).")
    # --- two-stage (coarse lower-mag pass, then the unchanged fine pass) ---
    p.add_argument("--coarse-reference", default=None,
                   help="Coarse reference BMP (capture-position --coarse-mag). When given, "
                        "refine runs a COARSE pass at --coarse-magnification first (stage "
                        "if --enable-stage and needed, then beam shift), then the normal "
                        "fine pass at --magnification.")
    p.add_argument("--coarse-magnification", type=float, default=800.0,
                   help="Magnification of the coarse reference (default 800).")
    p.add_argument("--coarse-scan-rate", type=int, default=5,
                   help="Scan rate for coarse-pass frames (default 5; faster than the "
                        "fine pass's --scan-rate).")
    p.add_argument("--coarse-tolerance-um", type=float, default=1.0,
                   help="Residual the coarse pass converges to before handing over to the "
                        "fine pass (default 1.0 um).")
    p.add_argument("--coarse", action="store_true",
                   help="[--simulate] run the two-stage refine in the simulator.")
    p.add_argument("--plan-only", action="store_true",
                   help="Grab REAL frames and measure the drift, run every guard, and "
                        "print what refine WOULD do -- but move NOTHING (no beam shift, "
                        "no stage). Use this first. (--dry-run grabs no images in real "
                        "mode, so it cannot show a decision.)")
    p.add_argument("--stage-max-um", type=float, default=100.0,
                   help="Refine stage limit per axis in um (default 100; a larger "
                        "required move is REFUSED, never partially made).")
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
    # --- STAGE calibration (refine-only stage correction; sign is UNVERIFIED) ---
    p.add_argument("--calibrate-stage", action="store_true",
                   help="MEASURE the STAGE<->image sign/scale for refine's stage "
                        "correction: steps the stage a known amount on X then Y "
                        "(X/Y only, other axes held), cross-correlates, reports the "
                        "sign, achieved step, pixel scale and any rotation, then "
                        "restores the start position. Refuses steps above the refine "
                        f"cap ({REFINE_STAGE_MAX_M * 1e6:.0f} um). Use a sacrificial area.")
    p.add_argument("--stage-test-step-um", type=float,
                   default=DEFAULT_STAGE_TEST_STEP_M * 1e6,
                   help="[calibrate-stage] Stage step per axis in microns (default 20; "
                        "mechanical stages have error -- go higher if the measurement "
                        "is noisy).")
    p.add_argument("--stage-calibration-file", default=DEFAULT_STAGE_CALIBRATION_FILENAME,
                   help=f"Where to save the stage calibration "
                        f"(default {DEFAULT_STAGE_CALIBRATION_FILENAME}).")
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
    if isinstance(res, TwoStageResult):
        print("\n=== two-stage result ===")
        print(f"  success      : {res.success}   stage moved: {res.stage_moved}")
        print(f"  message      : {res.message}")
        if res.coarse is not None:
            print("--- coarse pass ---"); _report(res.coarse)
        if res.fine is not None:
            print("--- fine pass ---"); _report(res.fine)
        return 0 if res.success else 1
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
    if args.calibrate_stage:
        cal = calibrate_stage(scope, args.magnification,
                              test_step_m=args.stage_test_step_um * 1e-6, logger=print)
        if args.save_calibration:
            cal.save(args.stage_calibration_file)
            print(f"\nSaved stage calibration -> {args.stage_calibration_file}")
        return 0 if cal.ok else 1
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
    if args.coarse:
        res = refine_two_stage(scope, scope.reference_image(args.coarse_magnification),
                               args.coarse_magnification, scope.reference_image(),
                               args.magnification, coarse_scan_rate=args.coarse_scan_rate,
                               coarse_tolerance_m=args.coarse_tolerance_um * 1e-6,
                               enable_stage=args.enable_stage,
                               stage_max_m=args.stage_max_um * 1e-6,
                               plan_only=args.plan_only, beam_sign_x=sx, beam_sign_y=sy,
                               max_iterations=args.max_iterations,
                               verify=not args.no_verify, logger=print)
        return _report(res)
    res = refine_position(scope, scope.reference_image(), args.magnification,
                          enable_stage=args.enable_stage,
                          stage_max_m=args.stage_max_um * 1e-6,
                          plan_only=args.plan_only,
                          max_iterations=args.max_iterations,
                          beam_sign_x=sx, beam_sign_y=sy,
                          verify=not args.no_verify, logger=print)
    return _report(res)


def _run_real(args) -> int:
    if not args.reference and not args.calibrate and not args.calibrate_stage:
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
        if args.calibrate_stage:
            if args.dry_run:
                print("[calibrate-stage] DRY RUN: would step the stage "
                      f"{args.stage_test_step_um:.0f} um on X, then Y (X/Y only, other "
                      "axes held), grab + correlate each, and restore. Nothing moved.")
                return 0
            # This MOVES the stage. Explicit confirmation before touching it.
            print(f"[calibrate-stage] This will move the stage {args.stage_test_step_um:.0f} um "
                  f"on X then Y (X/Y only), then return to the start position. Use a "
                  f"sacrificial area.")
            try:
                ans = input("Proceed? [yes/N] ").strip().lower()
            except EOFError:
                ans = ""
            if ans not in ("y", "yes"):
                print("[calibrate-stage] cancelled.")
                return 2
            cal = calibrate_stage(scope, args.magnification,
                                  test_step_m=args.stage_test_step_um * 1e-6,
                                  logger=print)
            if args.save_calibration:
                cal.save(args.stage_calibration_file)
                print(f"\nSaved stage calibration -> {args.stage_calibration_file}")
            return 0 if cal.ok else 1
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
        if args.enable_stage and not args.dry_run and not args.plan_only:
            # The stage may MOVE. Explicit confirmation, as for --calibrate-stage.
            print(f"[refine] --enable-stage: if the drift exceeds the beam-shift window, "
                  f"refine may move the stage ONCE (X/Y only, <= {args.stage_max_um:.0f} "
                  f"um per axis), then undo it unless the drift halves.")
            try:
                ans = input("Proceed? [yes/N] ").strip().lower()
            except EOFError:
                ans = ""
            if ans not in ("y", "yes"):
                print("[refine] cancelled.")
                return 2
        if args.coarse_reference:
            with Image.open(args.coarse_reference) as im:
                coarse_ref = np.asarray(im.convert("L"), dtype=np.float64)
            res = refine_two_stage(scope, coarse_ref, args.coarse_magnification,
                                   reference, args.magnification,
                                   coarse_scan_rate=args.coarse_scan_rate,
                                   coarse_tolerance_m=args.coarse_tolerance_um * 1e-6,
                                   enable_stage=args.enable_stage,
                                   stage_max_m=args.stage_max_um * 1e-6,
                                   plan_only=args.plan_only, beam_sign_x=sx,
                                   beam_sign_y=sy, max_iterations=args.max_iterations,
                                   verify=not args.no_verify, logger=print)
        else:
            res = refine_position(scope, reference, args.magnification,
                                  enable_stage=args.enable_stage,
                                  stage_max_m=args.stage_max_um * 1e-6,
                                  plan_only=args.plan_only,
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
