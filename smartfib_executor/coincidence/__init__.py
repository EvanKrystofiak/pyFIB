"""SEM working-distance / FIB-SEM coincidence branch (isolated).

Measures the SEM working distance by autofocusing (CMD_AUTO_FOCUS_COARSE then FINE)
and reading AP_WD, then RECOMMENDS a Z move to reach 5 mm -- the SEM/FIB coincidence
working distance. It never moves the stage; the operator makes the move by hand and
re-runs to verify.

    python -m smartfib_executor.coincidence --simulate     # offline demo
    python -m smartfib_executor.coincidence                # against the instrument

Safety: Z is never moved by pyFIB. The SEM adapter (FocusSem) structurally forbids
stage motion and parameter sets; only reads and the two autofocus commands are
allowed. The recommended move is checked against a per-move cap (0.5 mm) and hard Z
bounds [20, 40] mm, and against a WD sanity window [3, 8] mm.
"""

from __future__ import annotations

from .limits import (TARGET_WD_M, Z_HARD_MIN_M, Z_HARD_MAX_M, MAX_Z_STEP_M,
                     WD_SANE_MIN_M, WD_SANE_MAX_M, ZRecommendation, recommend_z_move)
from .sem import FocusSem, SimulatedFocusSem, CoincidenceSafetyError
from .workflow import CoincidenceResult, measure_and_recommend, run_coincidence
from .autofocus import (autofocus, wait_for_auto_idle, AutoFocusConfig,
                        AutoFocusResult, AutoFocusError, AutoFunctionNeverStarted,
                        AutoFunctionTimeout, AutoFocusAborted)
from .clock import Clock, RealClock, FakeClock

__all__ = [
    "TARGET_WD_M", "Z_HARD_MIN_M", "Z_HARD_MAX_M", "MAX_Z_STEP_M",
    "WD_SANE_MIN_M", "WD_SANE_MAX_M", "ZRecommendation", "recommend_z_move",
    "FocusSem", "SimulatedFocusSem", "CoincidenceSafetyError",
    "CoincidenceResult", "measure_and_recommend", "run_coincidence",
    "autofocus", "wait_for_auto_idle", "AutoFocusConfig", "AutoFocusResult",
    "AutoFocusError", "AutoFunctionNeverStarted", "AutoFunctionTimeout",
    "AutoFocusAborted", "Clock", "RealClock", "FakeClock",
]
