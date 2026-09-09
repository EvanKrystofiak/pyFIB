"""SEM autofocus: coarse then fine, with a sustained-idle confirmation window.

Confirmed instrument behaviour (backed by a regression test via the mock):
DP_AUTO_FUNCTION can read Idle *before* the auto routine actually starts, so we cannot
trust the first Idle we see. The routine therefore:

  1. issues the focus command,
  2. waits for DP_AUTO_FUNCTION to LEAVE Idle (proof the routine started), and
  3. waits for it to RETURN to Idle and STAY Idle for a sustained window
     (default 10 s) before declaring completion.

All waiting goes through the injected clock, so tests run instantly.

This routine performs focus only. It does not check prerequisites or move anything;
orchestration lives in gisdep.orchestrate.prepare.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from gisdep.api import params as P
from gisdep.api.interface import SmartSEMInterface
from gisdep.clock import Clock, RealClock

_log = logging.getLogger("gisdep.focus.autofocus")


class AutoFocusError(Exception):
    """Base class for autofocus failures."""


class AutoFunctionNeverStarted(AutoFocusError):
    """DP_AUTO_FUNCTION never left Idle within the startup window."""


class AutoFunctionTimeout(AutoFocusError):
    """The auto routine did not settle back to sustained Idle in time."""


class AutoFocusAborted(AutoFocusError):
    """The caller signalled an abort while waiting."""


@dataclass
class AutoFocusConfig:
    # Sustained Idle required before completion is believed. 10 s matches the
    # confirmed DP_AUTO_FUNCTION "premature Idle" behaviour.
    sustained_idle_s: float = 10.0
    # How long to wait for the routine to *start* (leave Idle) before giving up.
    startup_timeout_s: float = 30.0
    # Overall cap on the settle phase.
    overall_timeout_s: float = 180.0
    # Poll cadence. GetMulti is not implemented in this API, so every poll is a
    # separate round-trip; keep this modest.
    poll_interval_s: float = 0.5
    # If the routine never appears to start, is that an error (safe default) or do
    # we assume it completed too fast to observe? Default: treat as error.
    require_started: bool = True


@dataclass
class AutoFocusResult:
    wd_before_m: float
    wd_after_m: float
    did_coarse: bool
    did_fine: bool

    @property
    def wd_delta_m(self) -> float:
        return self.wd_after_m - self.wd_before_m


class _Aborter:
    """Duck-typed abort signal. Anything with .is_set() -> bool works (e.g.
    threading.Event), which lets a GUI cancel a run."""

    def is_set(self) -> bool:  # pragma: no cover - trivial
        return False


def _check_abort(abort: Optional[object], api: SmartSEMInterface) -> None:
    if abort is not None and getattr(abort, "is_set", lambda: False)():
        try:
            api.execute(P.CMD_ABORT_AUTO)
        finally:
            raise AutoFocusAborted("abort signalled during autofocus")


def wait_for_auto_idle(
    api: SmartSEMInterface,
    clock: Optional[Clock] = None,
    cfg: Optional[AutoFocusConfig] = None,
    abort: Optional[object] = None,
    log: Optional[logging.Logger] = None,
) -> None:
    """Block until an in-progress auto routine has confirmably completed.

    Raises AutoFunctionNeverStarted / AutoFunctionTimeout / AutoFocusAborted.
    """
    clock = clock or RealClock()
    cfg = cfg or AutoFocusConfig()
    log = log or _log

    # Phase 1: wait for the routine to leave Idle.
    started = False
    t0 = clock.now()
    while clock.now() - t0 < cfg.startup_timeout_s:
        _check_abort(abort, api)
        if api.get_digital(P.DP_AUTO_FUNCTION) != P.AF_IDLE:
            started = True
            break
        clock.sleep(cfg.poll_interval_s)

    if not started:
        if cfg.require_started:
            raise AutoFunctionNeverStarted(
                "DP_AUTO_FUNCTION stayed Idle for "
                f"{cfg.startup_timeout_s:.0f}s after the focus command"
            )
        log.warning("auto routine never observed leaving Idle; assuming complete")
        return

    # Phase 2: wait for sustained Idle.
    settle_start = clock.now()
    idle_since: Optional[float] = None
    while True:
        _check_abort(abort, api)
        af = api.get_digital(P.DP_AUTO_FUNCTION)
        now = clock.now()
        if af == P.AF_IDLE:
            if idle_since is None:
                idle_since = now
            elif now - idle_since >= cfg.sustained_idle_s:
                return
        else:
            idle_since = None  # bounced back to active; reset the confirmation timer
        if now - settle_start > cfg.overall_timeout_s:
            raise AutoFunctionTimeout(
                f"auto routine did not reach sustained Idle within "
                f"{cfg.overall_timeout_s:.0f}s"
            )
        clock.sleep(cfg.poll_interval_s)


def autofocus(
    api: SmartSEMInterface,
    do_coarse: bool = True,
    do_fine: bool = True,
    clock: Optional[Clock] = None,
    cfg: Optional[AutoFocusConfig] = None,
    abort: Optional[object] = None,
    log: Optional[logging.Logger] = None,
) -> AutoFocusResult:
    """Run coarse and/or fine SEM autofocus, each as its own confirmed auto cycle.

    Reads AP_WD before and after so the caller can see how far focus moved the working
    distance. Does NOT gate on that value -- see gisdep.focus.wd_gate.
    """
    clock = clock or RealClock()
    cfg = cfg or AutoFocusConfig()
    log = log or _log

    if not (do_coarse or do_fine):
        raise ValueError("autofocus called with neither coarse nor fine enabled")

    wd_before = api.get_analogue(P.AP_WD)

    if do_coarse:
        log.info("autofocus: coarse")
        api.execute(P.CMD_AUTO_FOCUS_COARSE)
        wait_for_auto_idle(api, clock=clock, cfg=cfg, abort=abort, log=log)

    if do_fine:
        log.info("autofocus: fine")
        api.execute(P.CMD_AUTO_FOCUS_FINE)
        wait_for_auto_idle(api, clock=clock, cfg=cfg, abort=abort, log=log)

    wd_after = api.get_analogue(P.AP_WD)
    log.info(
        "autofocus complete: WD %.4f mm -> %.4f mm",
        wd_before * 1e3,
        wd_after * 1e3,
    )
    return AutoFocusResult(
        wd_before_m=wd_before,
        wd_after_m=wd_after,
        did_coarse=do_coarse,
        did_fine=do_fine,
    )
