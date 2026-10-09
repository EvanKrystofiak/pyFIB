"""Integration layer: per-position autofocus + position refinement for milling.

This wires the two previously-isolated forks (autofocus, refine) into the milling
workflow. For each recalled position the enhanced workflow is, in order:

    recall (stage + objective voltage)         [done by batch.mill_positions_sequential]
      -> autofocus   (if selected for this position)
      -> refine      (if selected for this position)
      -> mill

Autofocus uses the position's SAVED objective voltage as the expected focus and
sweeps above and below it; it leaves the column at best focus. Refine then cross-
correlates the live FIB image against the position's reference BMP and nulls the
residual offset with FIB beam shift; that correction stays applied so milling lands
on the intended target.

Design notes:
  * The heavy, instrument-facing logic lives in the autofocus/refine forks, which
    are already unit-tested offline. This module is deliberately thin.
  * The functions here are injectable into batch.mill_positions_sequential, so the
    ORDER and seriality of operations can be tested offline without a real column
    (see tests/test_workflow.py).
  * Failure policy is graceful by default: if autofocus or refine cannot proceed
    confidently, we log and fall back to the recalled position / saved voltage
    (i.e. no worse than running without the feature). Set abort_on_failure=True in
    the config to make a failure raise instead.
  * INSTRUMENT-UNTESTED integration: the individual forks are validated, but this
    end-to-end wiring has not yet run on hardware.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .positions import PositionRecord


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
@dataclass
class AutofocusConfig:
    """Parameters for per-position autofocus."""
    half_range_v: float = 150.0        # sweep saved_v +/- this many volts
    coarse_points: int = 7
    metric: str = "tenengrad"
    bound_margin_v: float = 400.0      # hard safety clamp around saved_v
    beam_shift_x_m: float = 1.20e-05   # park off the target (fork default)
    raster_side: str = "auto"          # scan the safe side (fork default)
    strict_scan_rotation: bool = False
    imaging_scan_rate: Optional[int] = 8      # raise dwell for SNR; None = leave as-is
    scan_rate_method: str = "cmd"             # DP_SCANRATE is RO -> CMD_SCANRATE#
    analysis_height_frac: float = 1.0         # central row band scored (tilt/defocus)
    raster_h_scale: Optional[float] = None    # None -> fork default box height
    settle_s: float = 1.0
    frame_wait_s: float = 3.0
    abort_on_failure: bool = False


@dataclass
class RefineConfig:
    """Parameters for per-position refinement."""
    beam_shift_threshold_m: float = 1.00e-05   # <=10 um -> beam shift; above -> stage
    residual_tolerance_m: float = 3.0e-7
    max_iterations: int = 2
    verify: bool = True
    # Stage X/Y correction for drift beyond the beam-shift window. OFF unless the
    # operator opts in (mill-positions --refine-enable-stage / Mill tab checkbox).
    # Signs + tilt model MEASURED 2026-09-22; refine-only limits apply (X/Y only,
    # <= stage_max_m per axis, one move, undone unless drift halves, tilt -1..31 deg,
    # R = 12).
    enable_stage: bool = False
    stage_max_m: float = 100.0e-6
    strict_scan_rotation: bool = False
    imaging_scan_rate: Optional[int] = 8      # raise dwell for SNR; None = leave as-is
    scan_rate_method: str = "cmd"             # DP_SCANRATE is RO -> CMD_SCANRATE#
    analysis_height_frac: float = 1.0         # central row band scored (tilt/defocus)
    raster_h_scale: Optional[float] = None    # None -> fork default box height
    settle_s: float = 1.0
    frame_wait_s: float = 3.0
    abort_on_failure: bool = False
    calibration_file: Optional[str] = None   # refine_calibration.json (None -> default)
    # v0.39.0: 800x FALLBACK. If the 2000x refine cannot locate the site (low
    # confidence / aliasing guard / correction not converging) and the position has an
    # 800x coarse image, put the beam shift back and run the two-stage refine: coarse
    # pass at the coarse mag (stage move only if enable_stage), then 2000x. Validated
    # in the CLI 2026-10-01 (30+30 um off -> 0.05 um).
    coarse_fallback: bool = True
    coarse_scan_rate: Optional[int] = 5
    coarse_tolerance_m: float = 1.0e-6
    coarse_magnification_default: float = 800.0


# --------------------------------------------------------------------------- #
# Autofocus at a position
# --------------------------------------------------------------------------- #
def run_autofocus_at_position(
    sem,
    rec: PositionRecord,
    cfg: AutofocusConfig,
    *,
    dry_run: bool = False,
    logger: Optional[Callable[[str], None]] = None,
):
    """Autofocus around the position's saved objective voltage.

    Returns the AutofocusResult (or None if the fork could not be constructed).
    Leaves the column at the chosen focus; on low confidence the fork restores the
    original voltage itself.
    """
    log = logger or (lambda m: None)
    from .autofocus.search import autofocus
    from .autofocus.sources import ScopeImageSource

    saved_v = float(rec.fib_objective_potential_v)
    margin = max(float(cfg.bound_margin_v), 2.0 * float(cfg.half_range_v))
    bounds = (saved_v - margin, saved_v + margin)
    log(f"[autofocus] start at saved objective {saved_v:.1f} V, sweep +/-"
        f"{cfg.half_range_v:.0f} V (clamp {bounds[0]:.0f}..{bounds[1]:.0f} V)")

    src = ScopeImageSource(
        sem,
        start_voltage=saved_v,
        bounds=bounds,
        fib_magnification=float(rec.fib_magnification_x),
        beam_shift_x_m=float(cfg.beam_shift_x_m),
        raster_side=cfg.raster_side,
        settle_s=float(cfg.settle_s),
        frame_wait_s=float(cfg.frame_wait_s),
        strict_scan_rotation=bool(cfg.strict_scan_rotation),
        imaging_scan_rate=cfg.imaging_scan_rate,
        scan_rate_method=cfg.scan_rate_method,
        **({"raster_h_scale": float(cfg.raster_h_scale)}
           if cfg.raster_h_scale is not None else {}),
        dry_run=bool(dry_run),
        logger=log,
    )
    res = autofocus(src, half_range=float(cfg.half_range_v),
                    coarse_points=int(cfg.coarse_points), metric=cfg.metric,
                    analysis_height_frac=float(cfg.analysis_height_frac),
                    logger=log)
    if res.success:
        log(f"[autofocus] best focus {res.best_voltage:.1f} V "
            f"(from {saved_v:.1f} V; {res.n_frames} frames)"
            + (f", WD {res.working_distance_m * 1e3:.3f} mm"
               if res.working_distance_m else ""))
    else:
        msg = f"[autofocus] did NOT converge: {res.message}"
        if cfg.abort_on_failure:
            raise RuntimeError(msg)
        log(msg + " -- milling at the recalled voltage.")
    return res


# --------------------------------------------------------------------------- #
# Refine at a position
# --------------------------------------------------------------------------- #
def _resolve_reference(rec: PositionRecord, ref_base_dir: Optional[Path]) -> Optional[Path]:
    if not rec.image_bmp:
        return None
    p = Path(rec.image_bmp)
    if not p.is_absolute() and ref_base_dir is not None:
        p = (Path(ref_base_dir) / p).resolve()
    return p


def _resolve_coarse_reference(rec: PositionRecord,
                              ref_base_dir: Optional[Path]) -> Optional[Path]:
    path = getattr(rec, "image_bmp_coarse", None)
    if not path:
        return None
    p = Path(path)
    if not p.is_absolute() and ref_base_dir is not None:
        p = (Path(ref_base_dir) / p).resolve()
    return p


def _coarse_fallback_applies(res, cfg: "RefineConfig") -> bool:
    """True when the 2000x pass failed to LOCATE the site. Not when it located it but
    was not allowed / able to correct (stage disabled, or a stage guard refused): the
    800x pass would hit the same limit."""
    return (bool(getattr(cfg, "coarse_fallback", True)) and not res.success
            and not res.stage_required_but_disabled and not res.stage_refused)


def run_refine_at_position(
    sem,
    rec: PositionRecord,
    cfg: RefineConfig,
    *,
    ref_base_dir: Optional[Path] = None,
    dry_run: bool = False,
    logger: Optional[Callable[[str], None]] = None,
    reference_path: Optional[Path] = None,
):
    """Cross-correlate the live FIB image against the reference and null the offset.

    The beam-shift correction (<= threshold) stays applied so milling lands on the
    intended target. Returns the RefineResult, or None if there is no reference
    image to align against.

    reference_path (v0.51.0 milling stages): align to THIS image instead of the capture
    image -- Prepolish passes the Rough Mill after image, Polish the Prepolish one. It
    must be at the capture magnification and size (after images are). The 800x
    fallback still uses the capture 800x image.
    """
    log = logger or (lambda m: None)
    ref_path = (Path(reference_path) if reference_path is not None
                else _resolve_reference(rec, ref_base_dir))
    if reference_path is not None:
        log(f"[refine] {rec.name}: reference = {Path(reference_path).name}")
    if ref_path is None:
        msg = f"[refine] {rec.name}: no reference BMP recorded; cannot refine."
        if cfg.abort_on_failure:
            raise RuntimeError(msg)
        log(msg + " -- skipping refine.")
        return None
    if not Path(ref_path).exists():
        msg = f"[refine] {rec.name}: reference BMP not found at {ref_path}."
        if cfg.abort_on_failure:
            raise RuntimeError(msg)
        log(msg + " -- skipping refine.")
        return None

    from .refine import refine_position, ScopeRefineAdapter
    from .refine.calibrate import load_calibrated_signs
    from .refine import geometry as _rg

    # Prefer the measured signs from refine_calibration.json (same file the
    # standalone refine tool uses) so the integrated path can't diverge from it.
    signs = None
    try:
        signs = load_calibrated_signs(getattr(cfg, "calibration_file", None))
    except Exception:
        signs = None
    beam_sign_x, beam_sign_y = signs if signs else (_rg.BEAM_SIGN_X, _rg.BEAM_SIGN_Y)
    log(f"[refine] {rec.name}: beam signs ({beam_sign_x:+.0f}, {beam_sign_y:+.0f}) "
        + ("from calibration file" if signs else "from built-in default"))

    if dry_run:
        log(f"[refine] (dry-run) would cross-correlate against {ref_path} and "
            f"null the offset with beam shift.")
        return None

    import numpy as np
    from PIL import Image
    with Image.open(ref_path) as im:
        reference = np.asarray(im.convert("L"), dtype=np.float64)

    scope = ScopeRefineAdapter(sem, settle_s=float(cfg.settle_s),
                               frame_wait_s=float(cfg.frame_wait_s),
                               imaging_scan_rate=cfg.imaging_scan_rate,
                               scan_rate_method=cfg.scan_rate_method, logger=log)
    try:
        beam0 = scope.get_beam_shift()       # to undo a failed 2000x pass before 800x
    except Exception:  # noqa: BLE001
        beam0 = None
    res = refine_position(
        scope, reference, float(rec.fib_magnification_x),
        beam_shift_threshold_m=float(cfg.beam_shift_threshold_m),
        residual_tolerance_m=float(cfg.residual_tolerance_m),
        max_iterations=int(cfg.max_iterations), verify=bool(cfg.verify),
        enable_stage=bool(cfg.enable_stage),
        stage_max_m=float(cfg.stage_max_m),
        beam_sign_x=beam_sign_x, beam_sign_y=beam_sign_y,
        strict_scan_rotation=bool(cfg.strict_scan_rotation), logger=log,
    )
    coarse_stage = (0.0, 0.0)
    coarse_tried = False
    if _coarse_fallback_applies(res, cfg):
        coarse_tried = True
        res, coarse_stage = _coarse_fallback(scope, rec, cfg, reference, res, beam0,
                                             ref_base_dir=ref_base_dir,
                                             beam_sign_x=beam_sign_x,
                                             beam_sign_y=beam_sign_y, log=log)
    # For the run log (v0.40.0): note whether the 800x fallback ran and what stage move
    # it kept. Annotation only -- nothing reads these to make decisions.
    try:
        res.coarse_fallback_tried = coarse_tried
        res.coarse_stage_delta_m = tuple(coarse_stage)
    except Exception:  # noqa: BLE001
        pass
    if res.success and coarse_stage != (0.0, 0.0):
        log(f"[refine] {rec.name}: STAGE moved "
            f"({coarse_stage[0] * 1e6:+.2f}, {coarse_stage[1] * 1e6:+.2f}) um (X/Y) "
            f"in the 800x pass.")
    if res.success and res.applied_stage_delta_m != (0.0, 0.0):
        log(f"[refine] {rec.name}: STAGE moved "
            f"({res.applied_stage_delta_m[0] * 1e6:+.2f}, "
            f"{res.applied_stage_delta_m[1] * 1e6:+.2f}) um (X/Y).")
    if res.success:
        log(f"[refine] {rec.name}: aligned via {res.method}; residual "
            f"{res.residual_drift_m * 1e6:.2f} um "
            f"(beam delta {res.applied_beam_shift_delta_m[0] * 1e6:+.2f},"
            f"{res.applied_beam_shift_delta_m[1] * 1e6:+.2f} um).")
    else:
        detail = res.message
        if res.stage_required_but_disabled:
            detail = (f"drift {res.initial_drift_mag_m * 1e6:.1f} um exceeds the "
                      f"{cfg.beam_shift_threshold_m * 1e6:.0f} um beam-shift limit and "
                      f"stage correction is disabled (--refine-enable-stage)")
        msg = f"[refine] {rec.name}: NOT refined ({detail})"
        if cfg.abort_on_failure:
            raise RuntimeError(msg)
        log(msg + (" -- milling where the stage is now (the 800x pass moved it)."
                   if coarse_stage != (0.0, 0.0) else
                   " -- milling at the recalled position."))
    return res


def _coarse_fallback(scope, rec: PositionRecord, cfg: RefineConfig, reference, res,
                     beam0, *, ref_base_dir, beam_sign_x, beam_sign_y, log):
    """2000x could not locate the site: retry with the position's 800x image.

    Returns (result, stage delta applied by the coarse pass). The result is the 2000x
    RefineResult of the two-stage run (or the coarse one if that failed), or the
    original 2000x result if there is no usable 800x image.
    """
    cpath = _resolve_coarse_reference(rec, ref_base_dir)
    if cpath is None or not Path(cpath).exists():
        log(f"[refine] {rec.name}: 2000x could not locate the site and there is no 800x "
            f"image for it" + (f" (missing: {cpath})" if cpath else "")
            + " -- no fallback.")
        return res, (0.0, 0.0)
    from .refine.refine import refine_two_stage
    import numpy as np
    from PIL import Image
    cmag = float(getattr(rec, "coarse_magnification_x", None)
                 or cfg.coarse_magnification_default)
    log(f"[refine] {rec.name}: 2000x could not locate the site ({res.message}); "
        f"retrying with the {cmag:g}x image.")
    if beam0 is not None:
        try:
            scope.set_beam_shift(*beam0)     # start the 800x pass from a clean state
            log(f"[refine] {rec.name}: beam shift put back to "
                f"({beam0[0] * 1e6:+.2f}, {beam0[1] * 1e6:+.2f}) um before the 800x pass.")
        except Exception as e:  # noqa: BLE001
            log(f"[refine] {rec.name}: could not put the beam shift back ({e!r}); "
                f"no fallback.")
            return res, (0.0, 0.0)
    with Image.open(cpath) as im:
        coarse_ref = np.asarray(im.convert("L"), dtype=np.float64)
    # ONE stage move per site (refine's rule): if the failed 2000x pass already kept a
    # stage move, the 800x pass may only use beam shift.
    allow_stage = bool(cfg.enable_stage) and tuple(res.applied_stage_delta_m) == (0.0, 0.0)
    if cfg.enable_stage and not allow_stage:
        log(f"[refine] {rec.name}: the 2000x pass already moved the stage; the 800x pass "
            f"may use beam shift only (one stage move per site).")
    two = refine_two_stage(
        scope, coarse_ref, cmag, reference, float(rec.fib_magnification_x),
        coarse_scan_rate=cfg.coarse_scan_rate,
        coarse_tolerance_m=float(cfg.coarse_tolerance_m),
        enable_stage=allow_stage, stage_max_m=float(cfg.stage_max_m),
        beam_sign_x=beam_sign_x, beam_sign_y=beam_sign_y,
        max_iterations=int(cfg.max_iterations), verify=bool(cfg.verify), logger=log)
    stage = tuple(two.coarse.applied_stage_delta_m) if two.coarse is not None else (0.0, 0.0)
    log(f"[refine] {rec.name}: 800x fallback {'OK' if two.success else 'FAILED'}: "
        f"{two.message}")
    out = two.fine if two.fine is not None else two.coarse
    if out is None:
        return res, stage
    if two.success and not out.success:      # defensive: trust the two-stage verdict
        out.success = True
    if not two.success:
        out.success = False
        if two.fine is None and two.coarse is not None:
            out.message = f"800x pass: {two.coarse.message}"
    return out, stage
