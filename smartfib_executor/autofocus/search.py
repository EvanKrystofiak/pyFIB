"""FIB autofocus search: coarse sweep -> refine -> set best focus.

Drives an :class:`~.sources.ImageSource` (simulated or real). The instrument only
ever sees ``set_voltage`` / ``grab`` calls, so this whole file is exercised by the
simulator in the offline tests.

Strategy
--------
1. Coarse sweep of ``coarse_points`` voltages spanning ``initial +/- half_range``
   (clamped to the source's safety bounds), scoring each frame with the chosen
   sharpness metric.
2. If the peak is properly bracketed (interior, not at a sweep edge), optionally
   run a narrower fine sweep around it and then fit a parabola for a sub-step
   estimate. If the peak sits at an edge, we do NOT extrapolate -- we flag it so
   the operator can rerun with a wider range.
3. A confidence guard: if the score contrast across the sweep is below
   ``min_contrast`` (curve too flat / dominated by noise), the result is marked
   low-confidence and, if ``restore_on_low_confidence``, the voltage is returned
   to where it started rather than trusting a bogus peak.
4. On success the source is left AT the best focus voltage.

``max_frames`` caps total acquisitions (ion dose control). The coarse sweep is
never truncated below 3 frames (needed to bracket a peak); the fine sweep is
skipped or shortened to fit the remaining budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from . import metrics as _metrics
from . import sweep as _sweep
from .sources import ImageSource


@dataclass
class AutofocusResult:
    success: bool
    best_voltage: float
    best_score: float
    initial_voltage: float
    metric: str
    bracketed: bool
    refined: bool
    contrast: float
    low_confidence: bool
    n_frames: int
    curve: List[Tuple[float, float]] = field(default_factory=list)  # (voltage, score)
    message: str = ""
    # FIB working distance recorded at focus, for reference only. Focus itself is
    # driven by the objective voltage sweep; this value does not change it.
    working_distance_m: Optional[float] = None


def autofocus(
    source: ImageSource,
    *,
    half_range: float,
    coarse_points: int = 7,
    metric: str = _metrics.DEFAULT_METRIC,
    crop_bar: bool = True,
    presmooth_frac: float = _metrics.DEFAULT_PRESMOOTH_FRAC,
    analysis_height_frac: float = 1.0,
    refine: bool = True,
    fine_points: int = 5,
    fine_fraction: float = 0.35,
    min_contrast: float = 0.15,
    max_frames: Optional[int] = None,
    restore_on_low_confidence: bool = True,
    logger: Optional[Callable[[str], None]] = None,
) -> AutofocusResult:
    """Find and set the FIB objective voltage that maximises image sharpness."""
    log = logger or (lambda msg: None)
    if metric not in _metrics.METRICS:
        raise ValueError(f"Unknown metric {metric!r}; choose from {sorted(_metrics.METRICS)}")
    if coarse_points < 3:
        raise ValueError("coarse_points must be >= 3 to bracket a focus peak")

    # Bracket the whole run: prepare() may take out instrument state (e.g. shift the
    # beam off the area of interest) and restore() MUST put it back on every path --
    # success, failure, exception, or Ctrl-C. A beam shift left applied would
    # displace subsequent imaging and milling.
    source.prepare()
    try:
        result = _autofocus_inner(
            source, log=log, half_range=half_range, coarse_points=coarse_points,
            metric=metric, crop_bar=crop_bar, presmooth_frac=presmooth_frac,
            analysis_height_frac=analysis_height_frac,
            refine=refine, fine_points=fine_points, fine_fraction=fine_fraction,
            min_contrast=min_contrast, max_frames=max_frames,
            restore_on_low_confidence=restore_on_low_confidence,
        )
        # Record the FIB working distance for reference (best-effort; not all
        # sources expose it). Focus stays driven by the objective voltage above.
        get_wd = getattr(source, "get_working_distance_m", None)
        if callable(get_wd):
            try:
                result.working_distance_m = get_wd()
            except Exception:
                result.working_distance_m = None
        return result
    finally:
        source.restore()


def _autofocus_inner(
    source: ImageSource,
    *,
    log,
    half_range: float,
    coarse_points: int,
    metric: str,
    crop_bar: bool,
    presmooth_frac: float,
    analysis_height_frac: float = 1.0,
    refine: bool,
    fine_points: int,
    fine_fraction: float,
    min_contrast: float,
    max_frames: Optional[int],
    restore_on_low_confidence: bool,
) -> AutofocusResult:
    """The sweep itself. Always called between source.prepare()/restore()."""
    v0 = float(source.initial_voltage())
    bounds = source.bounds()
    frames_left = max_frames if max_frames is not None else 10 ** 9

    def _measure(v: float) -> float:
        source.set_voltage(v)
        img = source.grab()
        # Preprocess (drop databar, low-pass) BEFORE scoring: on real FIB frames
        # shot noise otherwise dominates the gradient and flattens the peak.
        s = _metrics.sharpness(
            img, metric, crop_bar=crop_bar, presmooth_frac=presmooth_frac,
            analysis_height_frac=analysis_height_frac,
        )
        log(f"[autofocus] V={v:.3f} -> {metric}={s:.5g}")
        return s

    # --- coarse sweep -----------------------------------------------------
    coarse_volts = _sweep.plan_voltages(v0, half_range, coarse_points, bounds)
    if len(coarse_volts) < 3:
        return AutofocusResult(
            success=False, best_voltage=v0, best_score=float("nan"),
            initial_voltage=v0, metric=metric, bracketed=False, refined=False,
            contrast=0.0, low_confidence=True, n_frames=0, curve=[],
            message=(
                "Sweep collapsed to fewer than 3 points (range vs bounds). "
                "Widen half_range or check voltage bounds."
            ),
        )

    curve: List[Tuple[float, float]] = []
    for v in coarse_volts:
        if frames_left <= 0:
            break
        curve.append((v, _measure(v)))
        frames_left -= 1

    ref = _sweep.refine_peak(curve)
    best_v, best_s = ref.best_voltage, ref.best_score
    bracketed, refined = ref.bracketed, False

    # --- optional fine sweep + parabola ----------------------------------
    if refine and bracketed and frames_left >= 3:
        fine_n = min(fine_points, max(3, frames_left))
        fine_volts = _sweep.plan_voltages(
            best_v, half_range * fine_fraction, fine_n, bounds
        )
        for v in fine_volts:
            if frames_left <= 0:
                break
            # Avoid re-grabbing a voltage we already sampled (dose saving).
            if any(abs(v - cv) < 1e-9 for cv, _ in curve):
                continue
            curve.append((v, _measure(v)))
            frames_left -= 1
        ref = _sweep.refine_peak(curve)
        best_v, best_s = ref.best_voltage, ref.best_score
        bracketed = ref.bracketed

    contrast = ref.contrast
    final_v = ref.estimated_voltage if (refine and ref.refined) else best_v
    refined = bool(refine and ref.refined)

    # --- confidence guard -------------------------------------------------
    low_conf = contrast < min_contrast
    messages: List[str] = []
    if low_conf:
        messages.append(
            f"Low confidence: score contrast {contrast:.3g} < {min_contrast:.3g} "
            "(curve is nearly flat; peak may be noise)."
        )
    if not bracketed:
        messages.append(
            "Focus peak was at a sweep edge (not bracketed); rerun with a wider "
            "half_range to be sure the true optimum is enclosed."
        )

    if low_conf and restore_on_low_confidence:
        source.set_voltage(v0)
        messages.append(f"Restored objective voltage to start ({v0:.3f} V).")
        return AutofocusResult(
            success=False, best_voltage=final_v, best_score=best_s,
            initial_voltage=v0, metric=metric, bracketed=bracketed, refined=refined,
            contrast=contrast, low_confidence=True, n_frames=len(curve), curve=curve,
            message=" ".join(messages),
        )

    # Success: leave the column at the best focus.
    source.set_voltage(final_v)
    if not messages:
        messages.append(f"Focus set to {final_v:.3f} V.")
    return AutofocusResult(
        success=True, best_voltage=final_v, best_score=best_s,
        initial_voltage=v0, metric=metric, bracketed=bracketed, refined=refined,
        contrast=contrast, low_confidence=low_conf, n_frames=len(curve), curve=curve,
        message=" ".join(messages),
    )
