"""SEM autofocus (fork): pyFIB's own SEM focus, replacing SmartSEM's CMD_AUTO_FOCUS_*.

Why (bug list #3): SmartSEM's autofocus sometimes leaves AP_CONTRAST at 0, so every
image after it is blank. This fork never calls it. It steps the SEM focus (AP_WD) at
FIXED contrast/brightness, grabs frame-safe SEM images, scores sharpness with the
metrics validated for FIB autofocus, fits the peak, and verifies the result.

Guards (see search.py): the 4-8 mm WD window (start, every set, the result), AP_WD
read-back, contrast/brightness/mag/stage Z unchanged, blank/saturated/stale frames,
an interior (bracketed) peak with real curve contrast, and a verification frame.
Any failure puts AP_WD back where it started.

Layers:
    guards  -- limits, frame sanity, GuardedSem (allow-list: cannot move the stage,
               set contrast/brightness, or run SmartSEM autofocus)
    scope   -- SemFocusScope: every SmartSEM call (instrument-untested)
    search  -- sem_autofocus(): preflight -> coarse -> fine -> verify
    two_stage -- sem_autofocus_two_stage() (v0.48.0, the CLI default): coarse at 500x,
               fine at the working mag, both scan speed 4 in a 0.35 box (measured design)
    curve   -- focus-curve measurement (v0.47.0)
    sim     -- SimulatedSem: a fake SmartSEM with fault knobs (offline + tests)

Isolated like the other forks: nothing in the mill / coincidence / GISDEP paths imports
it yet. Run it with ``python -m smartfib_executor.sem_autofocus`` (dry run by default).
"""

from __future__ import annotations

from .guards import WD_MIN_M, WD_MAX_M, GuardedSem, SemAutofocusSafetyError, frame_sanity
from .scope import SemFocusScope, SemAutofocusAbort, FakeClock
from .search import sem_autofocus, preflight, SemAutofocusResult
from .two_stage import sem_autofocus_two_stage, TwoStageResult
from .sim import SimulatedSem

__all__ = ["WD_MIN_M", "WD_MAX_M", "GuardedSem", "SemAutofocusSafetyError", "frame_sanity",
           "SemFocusScope", "SemAutofocusAbort", "FakeClock", "sem_autofocus", "preflight",
           "SemAutofocusResult", "SimulatedSem", "sem_autofocus_two_stage", "TwoStageResult"]
