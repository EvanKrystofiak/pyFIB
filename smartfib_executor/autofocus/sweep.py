"""Voltage-sweep planning and sub-step focus refinement.

Pure arithmetic only (no numpy needed, no hardware) so it is trivially testable.

Two jobs:
    * ``plan_voltages`` -- given a centre voltage and a half-range, produce the
      list of objective voltages to sample (clamped to instrument-safe bounds,
      de-duplicated, sorted).
    * ``refine_peak`` -- given the (voltage, score) samples, decide whether the
      maximum is properly *bracketed* (interior, not at a sweep edge) and, if so,
      fit a parabola through the three points around the peak to estimate the
      sub-step optimum. If the peak sits on an edge we report ``bracketed=False``
      so the caller can widen/shift or warn instead of trusting an extrapolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple


def plan_voltages(
    center: float,
    half_range: float,
    n_points: int,
    bounds: Optional[Tuple[float, float]] = None,
) -> List[float]:
    """Return sorted, unique voltages spanning ``center +/- half_range``.

    Parameters
    ----------
    center : the last best-known focus voltage to sweep around.
    half_range : half the total sweep width (volts). Must be > 0.
    n_points : number of samples (>= 2).
    bounds : optional (vmin, vmax) hard clamp for instrument safety.
    """
    if half_range <= 0:
        raise ValueError("half_range must be > 0")
    if n_points < 2:
        raise ValueError("n_points must be >= 2")

    lo = center - half_range
    hi = center + half_range
    if bounds is not None:
        vmin, vmax = bounds
        if vmin > vmax:
            vmin, vmax = vmax, vmin
        lo = max(lo, vmin)
        hi = min(hi, vmax)
        if lo > hi:  # centre outside bounds entirely
            lo = hi = min(max(center, vmin), vmax)

    if hi <= lo:
        return [lo]

    step = (hi - lo) / (n_points - 1)
    raw = [lo + i * step for i in range(n_points)]

    # De-duplicate while keeping order stable against float noise.
    out: List[float] = []
    for v in raw:
        if not out or abs(v - out[-1]) > 1e-12:
            out.append(v)
    out.sort()
    return out


@dataclass
class RefineResult:
    best_voltage: float          # sampled voltage with the highest score
    best_score: float
    estimated_voltage: float     # parabola vertex if bracketed, else best_voltage
    bracketed: bool              # True if the peak is interior (not at an edge)
    refined: bool                # True if a parabola vertex was used
    contrast: float              # (max - min) / |max|, a crude peak-strength gauge


def _argmax(scores: Sequence[float]) -> int:
    best_i = 0
    for i, s in enumerate(scores):
        if s > scores[best_i]:
            best_i = i
    return best_i


def _parabola_vertex(
    p0: Tuple[float, float], p1: Tuple[float, float], p2: Tuple[float, float]
) -> Optional[float]:
    """Vertex x of the parabola through three points, or None if degenerate."""
    x0, y0 = p0
    x1, y1 = p1
    x2, y2 = p2
    denom = (x0 - x1) * (x0 - x2) * (x1 - x2)
    if abs(denom) < 1e-18:
        return None
    a = (x2 * (y1 - y0) + x1 * (y0 - y2) + x0 * (y2 - y1)) / denom
    b = (
        x2 * x2 * (y0 - y1)
        + x1 * x1 * (y2 - y0)
        + x0 * x0 * (y1 - y2)
    ) / denom
    if abs(a) < 1e-18:  # nearly linear -> no meaningful vertex
        return None
    return -b / (2.0 * a)


def refine_peak(samples: Sequence[Tuple[float, float]]) -> RefineResult:
    """Locate and (if bracketed) sub-step-refine the focus peak.

    ``samples`` is a sequence of (voltage, score). Need not be sorted.
    """
    if not samples:
        raise ValueError("no samples provided")

    pts = sorted(samples, key=lambda vs: vs[0])
    volts = [v for v, _ in pts]
    scores = [s for _, s in pts]

    smax = max(scores)
    smin = min(scores)
    contrast = (smax - smin) / abs(smax) if abs(smax) > 1e-12 else 0.0

    i = _argmax(scores)
    best_v, best_s = volts[i], scores[i]

    # Edge peak -> not properly bracketed; caller should widen/shift or warn.
    if i == 0 or i == len(pts) - 1 or len(pts) < 3:
        return RefineResult(best_v, best_s, best_v, False, False, contrast)

    vx = _parabola_vertex(
        (volts[i - 1], scores[i - 1]),
        (volts[i], scores[i]),
        (volts[i + 1], scores[i + 1]),
    )
    if vx is None:
        return RefineResult(best_v, best_s, best_v, True, False, contrast)

    # Clamp the vertex to the bracketing interval; a valid maximum cannot lie
    # outside the three points that bracket it.
    lo, hi = volts[i - 1], volts[i + 1]
    vx = min(max(vx, lo), hi)
    return RefineResult(best_v, best_s, vx, True, True, contrast)
