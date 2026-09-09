"""Position refinement (fork module) -- roadmap item #4.

After a stage recall, cross-correlates the reference BMP captured by positions.py
with a fresh FIB image at the same magnification, and pulls the field back onto the
original target using FIB beam shift (<=10 um) or, when enabled, a stage move.

Isolated on purpose: nothing in the core workflow (or the autofocus fork) imports
this package. Shared helpers (databar crop, mag->pixel-size calibration) are
duplicated locally so the two forks stay independent until each is proven.

Layers:
    registration -- pure FFT phase cross-correlation (numpy)
    geometry     -- pixel<->metre + correction sign conventions (one tested place)
    refine       -- orchestrator with closed-loop verify
    sources      -- RefineScope iface + simulator + real ScopeRefineAdapter
"""

from __future__ import annotations

from .registration import phase_cross_correlation, RegistrationResult
from .geometry import (
    pixel_size_m, fov_width_m, drift_px_to_m, beam_shift_correction,
    stage_correction, BEAM_SHIFT_THRESHOLD_M, BEAM_SHIFT_MAX_M,
)
from .refine import refine_position, RefineResult
from .sources import RefineScope, SimulatedRefineScope, ScopeRefineAdapter

__all__ = [
    "phase_cross_correlation",
    "RegistrationResult",
    "pixel_size_m",
    "fov_width_m",
    "drift_px_to_m",
    "beam_shift_correction",
    "stage_correction",
    "BEAM_SHIFT_THRESHOLD_M",
    "BEAM_SHIFT_MAX_M",
    "refine_position",
    "RefineResult",
    "RefineScope",
    "SimulatedRefineScope",
    "ScopeRefineAdapter",
]
