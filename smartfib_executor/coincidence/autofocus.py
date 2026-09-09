"""SEM autofocus: coarse then fine, with gisdep's confirmed sustained-idle method.

Ported from gisdep.focus.autofocus so pyFIB uses the same proven routine (and so the
two fold together cleanly when gisdep is integrated). Confirmed instrument behaviour:
DP_AUTO_FUNCTION can read Idle *before* the auto routine actually starts, and can
flicker Idle mid-run, so we cannot trust the first Idle we see. Each focus command
therefore:

  1. issues the command,
  2. waits for DP_AUTO_FUNCTION to LEAVE Idle -- proof the routine actually started
     (its own startup timeout; raises if it never starts, unless require_started is
     turned off),
  3. waits for it to RETURN to Idle and STAY Idle for a sustained window (default
     10 s) before declaring completion -- any bounce back to active resets the timer.

All waiting goes through an injected Clock, so tests run instantly.

This module performs focus only. It does not check prerequisites, read tilt, or move
anything -- that orchestration lives in coincidence.workflow. It talks to the SEM via
the same get_float / get_str / execute surface FocusSem exposes (so it stays behind
that no-stage-motion guard).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .clock import Clock, RealClock

# DP_AUTO_FUNCTION state: 0 == Idle (manual 6.2.4). Non-zero == an auto routine
# (Focus, Stigmation, ...). The operator confirmed 1/Focus while CMD_AUTO_FOCUS_*
# runs and 0/Idle when done.
AF_IDLE = 0

CMD_ABORT_AUTO = "CMD_ABORT_AUTO"


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
    sustained_idle_s: float = 10.0     # Idle must hold this long before believed
    startup_timeout_s: float = 30.0    # wait this long for the routine to START
    overall_timeout_s: float = 180.0   # cap on the settle phase
    poll_interval_s: float = 0.5       # GetMulti absent -> each poll is a round-trip
    require_started: bool = True        # error if the routine never appears to start


@dataclass
class AutoFocusResult:
    wd_before_m: float
    wd_after_m: float
    did_coarse: bool
    did_fine: bool

    @property
    def wd_delta_m(self) -> float:
        return self.wd_after_m - self.wd_before_m


def _auto_function(sem) -> Optional[int]:
    """Read DP_AUTO_FUNCTION as an int state, or None if unreadable."""
    try:
        return int(round(float(sem.get_float("DP_AUTO_FUNCTION"))))
    except Exception:
        try:
            return AF_IDLE if sem.get_str("DP_AUTO_FUNCTION").strip().lower() in (
                "idle", "0") else 1
        except Exception:
            return None


def _check_abort(abort, sem) -> None:
    if abort is not None and getattr(abort, "is_set", lambda: False)():
        try:
            sem.execute(CMD_ABORT_AUTO)
        finally:
            raise AutoFocusAborted("abort signalled during autofocus")


def wait_for_auto_idle(sem, *, clock: Optional[Clock] = None,
                       cfg: Optional[AutoFocusConfig] = None,
                       abort: Optional[object] = None,
                       logger: Optional[Callable[[str], None]] = None) -> None:
    """Block until an in-progress auto routine has confirmably completed.

    Raises AutoFunctionNeverStarted / AutoFunctionTimeout / AutoFocusAborted.
    """
    clock = clock or RealClock()
    cfg = cfg or AutoFocusConfig()
    log = logger or (lambda m: None)

    # Phase 1: wait for the routine to LEAVE Idle (proof it started).
    started = False
    t0 = clock.now()
    while clock.now() - t0 < cfg.startup_timeout_s:
        _check_abort(abort, sem)
        state = _auto_function(sem)
        if state is None:
            break                        # unreadable -> cannot confirm start; move on
        if state != AF_IDLE:
            started = True
            break
        clock.sleep(cfg.poll_interval_s)

    if not started:
        if cfg.require_started and _auto_function(sem) is not None:
            raise AutoFunctionNeverStarted(
                f"DP_AUTO_FUNCTION stayed Idle for {cfg.startup_timeout_s:.0f}s after "
                f"the focus command; the auto routine never started.")
        log("[autofocus] routine not observed leaving Idle; relying on the sustained-"
            "Idle window.")

    # Phase 2: wait for sustained Idle.
    settle_start = clock.now()
    idle_since: Optional[float] = None
    while True:
        _check_abort(abort, sem)
        state = _auto_function(sem)
        now = clock.now()
        if state is None or state == AF_IDLE:
            if idle_since is None:
                idle_since = now
            elif now - idle_since >= cfg.sustained_idle_s:
                return
        else:
            if idle_since is not None:
                log(f"[autofocus] busy again -- resetting the "
                    f"{cfg.sustained_idle_s:.0f}s idle timer.")
            idle_since = None            # bounced back to active; reset the timer
        if now - settle_start > cfg.overall_timeout_s:
            raise AutoFunctionTimeout(
                f"auto routine did not reach sustained Idle within "
                f"{cfg.overall_timeout_s:.0f}s.")
        clock.sleep(cfg.poll_interval_s)


def autofocus(sem, *, do_coarse: bool = True, do_fine: bool = True,
              clock: Optional[Clock] = None, cfg: Optional[AutoFocusConfig] = None,
              abort: Optional[object] = None,
              logger: Optional[Callable[[str], None]] = None) -> AutoFocusResult:
    """Run coarse and/or fine SEM autofocus, each as its own confirmed cycle.

    Reads AP_WD before and after. Does NOT gate on that value (the caller does).
    """
    clock = clock or RealClock()
    cfg = cfg or AutoFocusConfig()
    log = logger or (lambda m: None)
    if not (do_coarse or do_fine):
        raise ValueError("autofocus called with neither coarse nor fine enabled")

    wd_before = float(sem.get_float("AP_WD"))

    if do_coarse:
        log(f"[autofocus] coarse (confirm Idle stable {cfg.sustained_idle_s:.0f}s) ...")
        sem.execute("CMD_AUTO_FOCUS_COARSE")
        wait_for_auto_idle(sem, clock=clock, cfg=cfg, abort=abort, logger=log)

    if do_fine:
        log(f"[autofocus] fine (confirm Idle stable {cfg.sustained_idle_s:.0f}s) ...")
        sem.execute("CMD_AUTO_FOCUS_FINE")
        wait_for_auto_idle(sem, clock=clock, cfg=cfg, abort=abort, logger=log)

    wd_after = float(sem.get_float("AP_WD"))
    log(f"[autofocus] complete: WD {wd_before * 1e3:.4f} mm -> {wd_after * 1e3:.4f} mm")
    return AutoFocusResult(wd_before_m=wd_before, wd_after_m=wd_after,
                           did_coarse=do_coarse, did_fine=do_fine)
