"""FIB autofocus (fork module).

Standalone, decoupled from the mill workflow. Sweeps the FIB objective voltage
around the last best-known focus, grabs an image at each voltage, scores sharpness,
and sets the voltage that maximises it.

Layers:
    metrics  -- pure sharpness metrics (numpy)
    sweep    -- voltage planning + parabola refinement (pure)
    search   -- the coarse->refine orchestrator (drives an ImageSource)
    sources  -- ImageSource interface + SimulatedImageSource (offline) +
                ScopeImageSource (real SmartSEM adapter; instrument-untested)

Reintegration is deliberately NOT wired yet: nothing in the mill/positions/UI
paths imports this package.
"""

from __future__ import annotations

from .metrics import METRICS, DEFAULT_METRIC, sharpness, all_scores
from .sweep import plan_voltages, refine_peak, RefineResult
from .search import autofocus, AutofocusResult
from .sources import ImageSource, SimulatedImageSource, ScopeImageSource

__all__ = [
    "METRICS",
    "DEFAULT_METRIC",
    "sharpness",
    "all_scores",
    "plan_voltages",
    "refine_peak",
    "RefineResult",
    "autofocus",
    "AutofocusResult",
    "ImageSource",
    "SimulatedImageSource",
    "ScopeImageSource",
]
