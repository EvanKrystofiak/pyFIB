"""Autofocus and working-distance gating."""

from gisdep.focus.autofocus import (
    AutoFocusConfig,
    AutoFocusResult,
    autofocus,
    wait_for_auto_idle,
    AutoFocusError,
    AutoFunctionNeverStarted,
    AutoFunctionTimeout,
    AutoFocusAborted,
)
from gisdep.focus.wd_gate import WDGateConfig, WDResult, WDVerdict, evaluate_wd

__all__ = [
    "AutoFocusConfig",
    "AutoFocusResult",
    "autofocus",
    "wait_for_auto_idle",
    "AutoFocusError",
    "AutoFunctionNeverStarted",
    "AutoFunctionTimeout",
    "AutoFocusAborted",
    "WDGateConfig",
    "WDResult",
    "WDVerdict",
    "evaluate_wd",
]
