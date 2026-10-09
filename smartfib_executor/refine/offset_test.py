"""T06 helper: inject a KNOWN stage offset, then check refine's stage correction.

Moving the stage by hand by ~15 um is awkward, so this script does it, per axis:

  1. grab a REFERENCE frame at the current position (2000x);
  2. move the stage a known offset on ONE axis (X/Y only; Z/T/R/M held; arrival
     confirmed to 1 um);
  3. run refine --plan-only against the reference and CHECK the plan: the planned
     stage move must be the reverse of the offset (within --tolerance-um);
  4. with --live, and only if the plan passed: run the real refine correction (one
     stage move, then beam shift) and check the residual;
  5. put the stage AND the beam shift back where they started.

A final frame after restoring is correlated with the first reference to confirm the
site is back where it began.

    python -m smartfib_executor.refine.offset_test                  # dry run: guards + numbers, nothing moves, no imaging
    python -m smartfib_executor.refine.offset_test --arm            # plan check (stage moves for the offset only)
    python -m smartfib_executor.refine.offset_test --arm --live     # plan check, then the live correction
    python -m smartfib_executor.refine.offset_test --simulate --live

Default offsets are chosen from the tilt model so the IMAGE moves ~13 um on each
axis: past the 10 um beam-shift window (so the stage path is exercised) and inside
the aliasing guard (40% of the 2000x field). Override with --offset-x-um /
--offset-y-um (e.g. --axes x --offset-x-um 30 for T07, which should be REFUSED).

SAFETY: refuses to run outside the calibrated model (tilt -1..31 deg, R = 12) or
with an offset above 30 um; asks for confirmation before any motion; stops at the
first failed check; always restores (a failed restore prints the coordinates to
return to by hand). Imaging: ~5 FIB frames per axis + 1 -- use a sacrificial area.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field, asdict
from math import hypot
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from . import geometry as g
from .registration import phase_cross_correlation
from .refine import refine_position

INJECT_MAX_M = 30.0e-6          # largest offset this script will inject, per axis
TARGET_IMAGE_DRIFT_M = 13.0e-6  # default: image moves ~13 um (window 10, guard ~17)
PLAN_TOL_M = 1.0e-6             # planned stage move must reverse the offset within this
LIVE_RESID_M = 0.3e-6           # live correction must end within this
AT_TARGET_TOL_M = 1.0e-6        # stage must be within this of the injected target


@dataclass
class AxisResult:
    axis: str
    offset_commanded_m: float
    offset_achieved_m: float = 0.0
    tilt_deg: float = 0.0
    rot_deg: float = 0.0
    image_drift_m: Tuple[float, float] = (0.0, 0.0)          # measured by refine
    image_drift_model_m: Tuple[float, float] = (0.0, 0.0)    # predicted by tilt model
    planned_stage_m: Tuple[float, float] = (0.0, 0.0)
    plan_error_m: float = 0.0
    plan_snr: float = 0.0
    plan_message: str = ""
    plan_pass: bool = False
    live_run: bool = False
    live_method: str = ""
    live_applied_stage_m: Tuple[float, float] = (0.0, 0.0)
    live_residual_m: float = 0.0
    live_message: str = ""
    live_pass: bool = False
    verdict: str = "NOT RUN"


@dataclass
class OffsetTestResult:
    ok: bool
    message: str
    start_xy_m: Tuple[float, float] = (0.0, 0.0)
    start_beam_m: Tuple[float, float] = (0.0, 0.0)
    return_offset_m: Optional[float] = None
    axes: List[AxisResult] = field(default_factory=list)


class _Recorder:
    """Scope proxy that keeps the last grabbed frame (saved for bring-back)."""
    def __init__(self, scope):
        self._scope = scope
        self.last = None

    def grab_fib_image(self, magnification: float, scan_rate=None):
        if scan_rate is None:
            img = self._scope.grab_fib_image(magnification)
        else:
            img = self._scope.grab_fib_image(magnification, scan_rate=scan_rate)
        self.last = img
        return img

    def __getattr__(self, name):
        return getattr(self._scope, name)


def default_offsets(tilt_deg: float,
                    target_image_drift_m: float = TARGET_IMAGE_DRIFT_M) -> Dict[str, float]:
    """Stage offset per axis that moves the IMAGE by target_image_drift_m at this tilt."""
    sx, sy = g.stage_scale(tilt_deg)
    return {"x": target_image_drift_m / sx, "y": target_image_drift_m / sy}


def predicted_image_drift(axis: str, offset_m: float, tilt_deg: float) -> Tuple[float, float]:
    """Image drift the tilt model predicts for a +offset stage step on `axis`
    (image X follows +stage X; image Y moves opposite to +stage Y)."""
    sx, sy = g.stage_scale(tilt_deg)
    if axis == "x":
        return (-g.STAGE_SIGN_X * sx * offset_m, 0.0)
    return (0.0, -g.STAGE_SIGN_Y * sy * offset_m)


def _save_png(img, path: Path) -> None:
    try:
        from PIL import Image
        a = np.asarray(img, dtype=np.float64)
        lo, hi = np.percentile(a, 0.5), np.percentile(a, 99.5)
        a = np.clip((a - lo) / max(hi - lo, 1e-9) * 255.0, 0, 255).astype(np.uint8)
        Image.fromarray(a).save(str(path))
    except Exception:
        pass


def _confirm_at(scope, target: Tuple[float, float], tol: float,
                timeout_s: float = 20.0, poll_s: float = 0.25,
                sleep: Callable[[float], None] = time.sleep) -> Tuple[float, float]:
    """Wait until the stage reads within `tol` of target and is steady; return XY."""
    t0 = time.time()
    last = None
    while True:
        cur = scope.get_stage_xy()
        near = abs(cur[0] - target[0]) <= tol and abs(cur[1] - target[1]) <= tol
        steady = last is not None and hypot(cur[0] - last[0], cur[1] - last[1]) < 0.05e-6
        if near and steady:
            return cur
        if time.time() - t0 > timeout_s:
            raise RuntimeError(
                f"stage did not settle at ({target[0] * 1e3:.4f}, {target[1] * 1e3:.4f}) mm "
                f"(read {cur[0] * 1e3:.4f}, {cur[1] * 1e3:.4f})")
        last = cur
        sleep(poll_s)


def run_offset_test(scope, *, magnification: float = 2000.0,
                    axes: Tuple[str, ...] = ("x", "y"),
                    offsets_m: Optional[Dict[str, float]] = None,
                    live: bool = False,
                    beam_sign_x: float = g.BEAM_SIGN_X,
                    beam_sign_y: float = g.BEAM_SIGN_Y,
                    stage_max_m: float = g.REFINE_STAGE_MAX_M,
                    plan_tol_m: float = PLAN_TOL_M,
                    save_dir: Optional[Path] = None,
                    logger: Optional[Callable[[str], None]] = None,
                    sleep: Callable[[float], None] = time.sleep) -> OffsetTestResult:
    """Run the T06 offset test on `scope` (a RefineScope). See module docstring."""
    log = logger or (lambda m: None)
    rec = _Recorder(scope)

    # ---- guards: nothing moves until all pass ----
    tilt, rot = scope.get_stage_tilt_rot()
    ok, why = g.stage_model_applies(tilt, rot)
    if not ok:
        return OffsetTestResult(False, f"REFUSED before any motion: {why}")
    offs = dict(default_offsets(tilt))
    offs.update(offsets_m or {})
    for a in axes:
        if a not in ("x", "y"):
            return OffsetTestResult(False, f"REFUSED: unknown axis {a!r}")
        if abs(offs[a]) > INJECT_MAX_M:
            return OffsetTestResult(False, (
                f"REFUSED before any motion: {a.upper()} offset {offs[a] * 1e6:.1f} um "
                f"exceeds the {INJECT_MAX_M * 1e6:.0f} um injection limit"))
    sx0, sy0 = scope.get_stage_xy()
    bx0, by0 = scope.get_beam_shift()
    out = OffsetTestResult(True, "", (sx0, sy0), (bx0, by0))
    sx, sy = g.stage_scale(tilt)
    log(f"[offset-test] start XY ({sx0 * 1e3:.4f}, {sy0 * 1e3:.4f}) mm, tilt {tilt:.2f}, "
        f"R {rot:.2f}, beam shift ({bx0 * 1e6:+.2f}, {by0 * 1e6:+.2f}) um; "
        f"model scale X {sx:.3f} Y {sy:.3f}")
    first_ref = None
    try:
        for a in axes:
            off = float(offs[a])
            r = AxisResult(axis=a, offset_commanded_m=off, tilt_deg=tilt, rot_deg=rot)
            out.axes.append(r)
            log(f"\n[offset-test] ===== axis {a.upper()}: offset {off * 1e6:+.2f} um =====")
            ref = rec.grab_fib_image(magnification)
            if first_ref is None:
                first_ref = ref
            if save_dir:
                _save_png(ref, save_dir / f"{a}_1_reference.png")

            target = (sx0 + off, sy0) if a == "x" else (sx0, sy0 + off)
            scope.move_stage_xy(*target)
            ax_, ay_ = _confirm_at(scope, target, AT_TARGET_TOL_M, sleep=sleep)
            r.offset_achieved_m = (ax_ - sx0) if a == "x" else (ay_ - sy0)
            r.image_drift_model_m = predicted_image_drift(a, r.offset_achieved_m, tilt)
            log(f"[offset-test] stage offset achieved {r.offset_achieved_m * 1e6:+.3f} um; "
                f"model predicts image drift ({r.image_drift_model_m[0] * 1e6:+.2f}, "
                f"{r.image_drift_model_m[1] * 1e6:+.2f}) um")

            # ---- plan (moves nothing) ----
            plan = refine_position(rec, ref, magnification, enable_stage=True,
                                   stage_max_m=stage_max_m, plan_only=True,
                                   beam_sign_x=beam_sign_x, beam_sign_y=beam_sign_y,
                                   logger=log)
            if save_dir and rec.last is not None:
                _save_png(rec.last, save_dir / f"{a}_2_offset.png")
            r.plan_message, r.plan_snr = plan.message, plan.snr
            r.image_drift_m = (plan.initial_drift_x_m, plan.initial_drift_y_m)
            if plan.low_confidence or plan.method != "plan":
                r.verdict = "FAIL (plan: no confident measurement)"
            elif "PLAN: stage X/Y" not in plan.message:
                r.verdict = ("FAIL (plan: stage path not exercised -- "
                             + ("refused" if "REFUSED" in plan.message
                                else "drift within the beam-shift window; increase the offset")
                             + ")")
            else:
                pdx, pdy = g.stage_correction(plan.initial_drift_x_m, plan.initial_drift_y_m,
                                              tilt_deg=tilt)
                r.planned_stage_m = (pdx, pdy)
                along = pdx if a == "x" else pdy
                cross = pdy if a == "x" else pdx
                r.plan_error_m = hypot(along + r.offset_achieved_m, cross)
                sign_ok = along * r.offset_achieved_m < 0
                r.plan_pass = sign_ok and r.plan_error_m <= plan_tol_m
                log(f"[offset-test] planned stage ({pdx * 1e6:+.2f}, {pdy * 1e6:+.2f}) um vs "
                    f"expected ({-r.offset_achieved_m * 1e6 if a == 'x' else 0.0:+.2f}, "
                    f"{-r.offset_achieved_m * 1e6 if a == 'y' else 0.0:+.2f}) um -> error "
                    f"{r.plan_error_m * 1e6:.2f} um, sign {'OK' if sign_ok else 'WRONG'}")
                if not sign_ok:
                    r.verdict = "FAIL (plan: WRONG SIGN -- do NOT run live)"
                elif not r.plan_pass:
                    r.verdict = (f"FAIL (plan: error {r.plan_error_m * 1e6:.2f} um > "
                                 f"{plan_tol_m * 1e6:.1f} um)")
                else:
                    r.verdict = "PASS (plan)"
            if not r.plan_pass:
                out.ok = False
                log(f"[offset-test] axis {a.upper()}: {r.verdict}. Stopping.")
                break

            # ---- live correction ----
            if live:
                r.live_run = True
                res = refine_position(rec, ref, magnification, enable_stage=True,
                                      stage_max_m=stage_max_m,
                                      beam_sign_x=beam_sign_x, beam_sign_y=beam_sign_y,
                                      logger=log)
                if save_dir and rec.last is not None:
                    _save_png(rec.last, save_dir / f"{a}_3_after_refine.png")
                r.live_method = res.method
                r.live_applied_stage_m = tuple(res.applied_stage_delta_m)
                r.live_residual_m = res.residual_drift_m
                r.live_message = res.message
                r.live_pass = (bool(res.success) and res.applied_stage_delta_m != (0.0, 0.0)
                               and res.residual_drift_m <= LIVE_RESID_M)
                r.verdict = ("PASS (plan + live)" if r.live_pass else
                             f"FAIL (live: {res.method}, residual "
                             f"{res.residual_drift_m * 1e6:.2f} um: {res.message})")
                if not r.live_pass:
                    out.ok = False
                    log(f"[offset-test] axis {a.upper()}: {r.verdict}. Stopping.")
                    break

            # back to the start before the next axis
            scope.move_stage_xy(sx0, sy0)
            _confirm_at(scope, (sx0, sy0), AT_TARGET_TOL_M, sleep=sleep)
            scope.set_beam_shift(bx0, by0)
    except Exception as e:  # noqa: BLE001
        # Record it, still restore (finally) and still write the results file.
        out.ok = False
        if out.axes and out.axes[-1].verdict in ("NOT RUN", "PASS (plan)"):
            out.axes[-1].verdict = f"FAIL (error: {e})"
        log(f"[offset-test] ERROR: {e}. Stopping; restoring.")
    finally:
        restored = True
        try:
            scope.move_stage_xy(sx0, sy0)
            _confirm_at(scope, (sx0, sy0), AT_TARGET_TOL_M, sleep=sleep)
            scope.set_beam_shift(bx0, by0)
            log(f"[offset-test] restored stage XY ({sx0 * 1e3:.4f}, {sy0 * 1e3:.4f}) mm and "
                f"beam shift ({bx0 * 1e6:+.2f}, {by0 * 1e6:+.2f}) um")
        except Exception as e:  # noqa: BLE001
            restored = False
            out.ok = False
            log(f"[offset-test] *** RESTORE FAILED ({e}). Return the stage MANUALLY to "
                f"X={sx0 * 1e3:.4f} mm, Y={sy0 * 1e3:.4f} mm and the FIB beam shift to "
                f"({bx0 * 1e6:+.2f}, {by0 * 1e6:+.2f}) um. ***")
    # ---- return check: back where we began? ----
    if restored and first_ref is not None:
        try:
            back = rec.grab_fib_image(magnification)
            if save_dir:
                _save_png(back, save_dir / "9_returned.png")
            rr = phase_cross_correlation(first_ref, back, crop_bar=True)
            dx, dy = g.drift_px_to_m(rr.dy_px, rr.dx_px, magnification, rr.shape[1])
            out.return_offset_m = hypot(dx, dy)
            log(f"[offset-test] return check: {out.return_offset_m * 1e6:.2f} um from the "
                f"first reference (snr {rr.snr:.0f})")
        except Exception as e:  # noqa: BLE001
            log(f"[offset-test] return check failed: {e}")
    n_pass = sum(1 for r in out.axes if r.verdict.startswith("PASS"))
    out.message = (f"{'PASS' if out.ok and n_pass == len(axes) else 'FAIL'}: "
                   f"{n_pass}/{len(axes)} axes passed"
                   + (" (plan + live)" if live else " (plan only)"))
    out.ok = out.ok and n_pass == len(axes)
    return out


def _summary(res: OffsetTestResult, live: bool) -> str:
    lines = ["", "=" * 72, f"T06 OFFSET TEST: {res.message}", "=" * 72]
    for r in res.axes:
        lines.append(f"  {r.axis.upper()}: offset {r.offset_achieved_m * 1e6:+.2f} um  "
                     f"planned ({r.planned_stage_m[0] * 1e6:+.2f}, {r.planned_stage_m[1] * 1e6:+.2f}) um  "
                     f"plan error {r.plan_error_m * 1e6:.2f} um  snr {r.plan_snr:.0f}")
        lines.append(f"      image drift measured ({r.image_drift_m[0] * 1e6:+.2f}, "
                     f"{r.image_drift_m[1] * 1e6:+.2f}) um vs model "
                     f"({r.image_drift_model_m[0] * 1e6:+.2f}, {r.image_drift_model_m[1] * 1e6:+.2f}) um")
        if r.live_run:
            lines.append(f"      live: {r.live_method}, stage "
                         f"({r.live_applied_stage_m[0] * 1e6:+.2f}, {r.live_applied_stage_m[1] * 1e6:+.2f}) um, "
                         f"residual {r.live_residual_m * 1e6:.3f} um")
        lines.append(f"      -> {r.verdict}")
    if res.return_offset_m is not None:
        lines.append(f"  return check: {res.return_offset_m * 1e6:.2f} um from the first reference")
    lines.append("=" * 72)
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m smartfib_executor.refine.offset_test",
        description="T06: inject a known stage offset and check refine's stage correction.")
    p.add_argument("--arm", action="store_true",
                   help="Actually move the stage and image. Without it: dry run (reads the "
                        "stage, checks the guards, prints the offsets; nothing moves).")
    p.add_argument("--live", action="store_true",
                   help="After a passing plan, run the real refine correction.")
    p.add_argument("--axes", default="x,y", help="Axes to test, e.g. x or x,y (default x,y).")
    p.add_argument("--offset-x-um", type=float, default=None,
                   help="X offset in um (default: from the tilt model, ~13 um of image drift).")
    p.add_argument("--offset-y-um", type=float, default=None, help="Y offset in um (same).")
    p.add_argument("--magnification", type=float, default=2000.0)
    p.add_argument("--tolerance-um", type=float, default=PLAN_TOL_M * 1e6,
                   help="Allowed plan error in um (default 1.0).")
    p.add_argument("--stage-max-um", type=float, default=100.0,
                   help="Refine's stage limit per axis (default 100).")
    p.add_argument("--scan-rate", type=int, default=8)
    p.add_argument("--scan-rate-method", choices=("set", "cmd", "set_then_cmd"), default="cmd")
    p.add_argument("--calibration-file", default=None,
                   help="Beam-shift calibration JSON (default: refine_calibration.json if present).")
    p.add_argument("--out", default=None,
                   help="Folder for images + results.json (default refine_offset_test_<time>).")
    p.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    p.add_argument("--simulate", action="store_true", help="Run on the refine simulator.")
    p.add_argument("--sim-tilt", type=float, default=12.0)
    p.add_argument("--sim-rot", type=float, default=12.0)
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    axes = tuple(x.strip().lower() for x in a.axes.split(",") if x.strip())
    offsets = {}
    if a.offset_x_um is not None:
        offsets["x"] = a.offset_x_um * 1e-6
    if a.offset_y_um is not None:
        offsets["y"] = a.offset_y_um * 1e-6

    from .calibrate import load_calibrated_signs, DEFAULT_CALIBRATION_FILENAME
    signs = load_calibrated_signs(a.calibration_file or DEFAULT_CALIBRATION_FILENAME)
    bsx, bsy = signs if signs else (g.BEAM_SIGN_X, g.BEAM_SIGN_Y)
    print(f"[offset-test] beam signs ({bsx:+.0f}, {bsy:+.0f}) "
          f"from {'calibration' if signs else 'built-in defaults'}")

    sem = None
    if a.simulate:
        from .sources import SimulatedRefineScope
        scope = SimulatedRefineScope(magnification=a.magnification, noise_sigma=4.0,
                                     stage_tilt_deg=a.sim_tilt, stage_rot_deg=a.sim_rot)
        sleep = lambda s: None
    else:
        try:
            from ..smartsem_com import SmartSEM
        except Exception as e:  # pragma: no cover
            print(f"ERROR: could not import SmartSEM (not on the SmartSEM PC?): {e}",
                  file=sys.stderr)
            return 2
        from .sources import ScopeRefineAdapter
        sem = SmartSEM()
        try:
            sem.connect_remoting()
        except Exception:
            sem.connect_local()
        scope = ScopeRefineAdapter(
            sem, imaging_scan_rate=(None if a.scan_rate < 0 else a.scan_rate),
            scan_rate_method=a.scan_rate_method, logger=print)
        sleep = time.sleep

    try:
        # ---- dry run / preview (read-only) ----
        tilt, rot = scope.get_stage_tilt_rot()
        ok, why = g.stage_model_applies(tilt, rot)
        offs = dict(default_offsets(tilt)); offs.update(offsets)
        print(f"[offset-test] stage tilt {tilt:.2f} deg, R {rot:.2f} deg -> "
              f"{'OK' if ok else 'REFUSED: ' + why}")
        for ax in axes:
            pdx, pdy = predicted_image_drift(ax, offs[ax], tilt)
            print(f"[offset-test] {ax.upper()}: offset {offs[ax] * 1e6:+.2f} um -> image "
                  f"~{hypot(pdx, pdy) * 1e6:.1f} um; expected plan: stage "
                  f"{ax.upper()} {-offs[ax] * 1e6:+.2f} um")
        if not ok:
            return 2
        if not (a.arm or a.simulate):
            print("[offset-test] DRY RUN: nothing moved, nothing imaged. Add --arm to run.")
            return 0
        if not a.yes and not a.simulate:
            print(f"[offset-test] This will move the stage (X/Y only) by the offsets above, "
                  f"image ~{5 * len(axes) + 1} FIB frames"
                  + (", and apply LIVE refine corrections" if a.live else "")
                  + ", then restore the stage and beam shift. Use a sacrificial area.")
            try:
                ans = input("Proceed? [yes/N] ").strip().lower()
            except EOFError:
                ans = ""
            if ans not in ("y", "yes"):
                print("[offset-test] cancelled.")
                return 2

        out_dir = Path(a.out or time.strftime("refine_offset_test_%Y%m%d_%H%M%S"))
        out_dir.mkdir(parents=True, exist_ok=True)
        res = run_offset_test(scope, magnification=a.magnification, axes=axes,
                              offsets_m=offsets, live=a.live, beam_sign_x=bsx,
                              beam_sign_y=bsy, stage_max_m=a.stage_max_um * 1e-6,
                              plan_tol_m=a.tolerance_um * 1e-6, save_dir=out_dir,
                              logger=print, sleep=sleep)
        print(_summary(res, a.live))
        d = asdict(res); d["live"] = bool(a.live); d["simulated"] = bool(a.simulate)
        (out_dir / "results.json").write_text(json.dumps(d, indent=2), encoding="utf-8")
        print(f"[offset-test] results + images saved to {out_dir.resolve()}")
        return 0 if res.ok else 1
    finally:
        if sem is not None:
            try:
                sem.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
