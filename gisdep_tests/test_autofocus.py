import pytest

from gisdep.api import params as P
from gisdep.api.mock import MockSmartSEM, MockAutoConfig
from gisdep.clock import FakeClock
from gisdep.focus.autofocus import (
    AutoFocusConfig,
    autofocus,
    wait_for_auto_idle,
    AutoFunctionNeverStarted,
    AutoFunctionTimeout,
    AutoFocusAborted,
)


def make(clock, **kw):
    api = MockSmartSEM(clock=clock, **kw)
    api.initialise()
    return api


def cfg(**kw):
    base = dict(
        sustained_idle_s=10.0,
        startup_timeout_s=30.0,
        overall_timeout_s=180.0,
        poll_interval_s=0.5,
    )
    base.update(kw)
    return AutoFocusConfig(**base)


def test_wait_completes_after_sustained_idle():
    clock = FakeClock()
    api = make(clock, auto_config=MockAutoConfig(latency=0.4, active=2.5))
    api.execute(P.CMD_AUTO_FOCUS_COARSE)
    t_start = clock.now()
    wait_for_auto_idle(api, clock=clock, cfg=cfg())
    elapsed = clock.now() - t_start
    # Must have waited at least the sustained-idle window past the ~2.9 s active phase.
    assert elapsed >= 10.0
    # And not absurdly long.
    assert elapsed < 60.0


def test_premature_idle_does_not_short_circuit():
    # If sustained-idle logic were broken, the routine would "complete" during the
    # premature-idle window before the routine truly started. It must not.
    clock = FakeClock()
    api = make(clock, auto_config=MockAutoConfig(latency=1.0, active=3.0))
    api.execute(P.CMD_AUTO_FOCUS_FINE)
    wait_for_auto_idle(api, clock=clock, cfg=cfg())
    # The active phase ends at ~4.0 s; completion needs +10 s sustained idle.
    assert clock.now() >= 14.0


def test_never_started_raises():
    clock = FakeClock()
    api = make(clock)
    # No focus command issued -> DP_AUTO_FUNCTION stays Idle forever.
    with pytest.raises(AutoFunctionNeverStarted):
        wait_for_auto_idle(api, clock=clock, cfg=cfg(startup_timeout_s=5.0))


def test_never_started_can_be_permissive():
    clock = FakeClock()
    api = make(clock)
    # require_started=False -> assume complete instead of raising.
    wait_for_auto_idle(
        api, clock=clock, cfg=cfg(startup_timeout_s=5.0, require_started=False)
    )


def test_overall_timeout_when_stuck_active():
    clock = FakeClock()
    # active far longer than overall_timeout -> never reaches sustained idle.
    api = make(clock, auto_config=MockAutoConfig(latency=0.2, active=10_000.0))
    api.execute(P.CMD_AUTO_FOCUS_COARSE)
    with pytest.raises(AutoFunctionTimeout):
        wait_for_auto_idle(api, clock=clock, cfg=cfg(overall_timeout_s=30.0))


class _Abort:
    def __init__(self):
        self._set = False

    def set(self):
        self._set = True

    def is_set(self):
        return self._set


def test_abort_raises_and_cancels():
    clock = FakeClock()
    api = make(clock, auto_config=MockAutoConfig(latency=0.1, active=1000.0))
    api.execute(P.CMD_AUTO_FOCUS_COARSE)
    ab = _Abort()
    ab.set()
    with pytest.raises(AutoFocusAborted):
        wait_for_auto_idle(api, clock=clock, cfg=cfg(), abort=ab)
    assert P.CMD_ABORT_AUTO in api.executed


def test_autofocus_runs_coarse_then_fine():
    clock = FakeClock()
    api = make(clock, wd_m=0.0055, auto_config=MockAutoConfig(latency=0.3, active=1.5))
    res = autofocus(api, clock=clock, cfg=cfg())
    assert res.did_coarse and res.did_fine
    # coarse then fine, in order (abort not present)
    assert api.executed == [P.CMD_AUTO_FOCUS_COARSE, P.CMD_AUTO_FOCUS_FINE]
    assert res.wd_before_m == pytest.approx(0.0055)
    assert res.wd_after_m == pytest.approx(0.0055)


def test_autofocus_fine_only():
    clock = FakeClock()
    api = make(clock, auto_config=MockAutoConfig(latency=0.3, active=1.0))
    res = autofocus(api, do_coarse=False, do_fine=True, clock=clock, cfg=cfg())
    assert not res.did_coarse and res.did_fine
    assert api.executed == [P.CMD_AUTO_FOCUS_FINE]


def test_autofocus_requires_at_least_one():
    clock = FakeClock()
    api = make(clock)
    with pytest.raises(ValueError):
        autofocus(api, do_coarse=False, do_fine=False, clock=clock, cfg=cfg())
