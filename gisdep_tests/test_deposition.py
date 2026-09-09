import pytest

from gisdep.api.mock import MockSmartSEM
from gisdep.clock import FakeClock
from gisdep.gis.actuate import CommandGISBackend, GISBackendConfig
from gisdep.gis.deposition import (
    deposit,
    DepositionResult,
    NotSafeToStartError,
    CloseFailure,
    OperatorDeclined,
    DWELL_MIN_S,
    DWELL_MAX_S,
)
from gisdep.gis.state import (
    NEEDLE_INSERTED,
    NEEDLE_PARKED,
    VALVE_OPEN,
    VALVE_CLOSED,
    VALVE_TRANSIT,
)


def make(tilt_deg=30.0):
    api = MockSmartSEM(clock=FakeClock(), stage_tilt_deg=tilt_deg)
    api.initialise()
    return api


def run(api, port=1, dwell=5.0, confirm=None):
    b = CommandGISBackend(api, clock=api.clock, cfg=GISBackendConfig())
    return deposit(api, port, dwell, backend=b, clock=api.clock, confirm=confirm)


# --- happy path ------------------------------------------------------------
def test_full_deposition_completes_and_leaves_safe_state():
    api = make()
    res = run(api, port=1, dwell=5.0)
    assert isinstance(res, DepositionResult) and res.completed
    assert api.get_digital("DP_GIS_LOCATION") == NEEDLE_PARKED
    assert api.get_digital("DP_GIS_VALVE_STATE1") == VALVE_CLOSED
    assert api.executed == [
        "CMD_GIS_GOTO_GAS1",
        "CMD_GIS_OPEN_CHANNEL1",
        "CMD_GIS_CLOSE_CHANNEL",
        "CMD_GIS_GOTO_PARKED",
    ]


def test_command_order_never_opens_before_insert():
    api = make()
    run(api, port=1, dwell=3.0)
    ins = api.executed.index("CMD_GIS_GOTO_GAS1")
    opn = api.executed.index("CMD_GIS_OPEN_CHANNEL1")
    cls = api.executed.index("CMD_GIS_CLOSE_CHANNEL")
    ret = api.executed.index("CMD_GIS_GOTO_PARKED")
    assert ins < opn < cls < ret  # never open before insert, never retract before close


# --- dwell clamp -----------------------------------------------------------
@pytest.mark.parametrize("bad", [0.0, 1.9, 30.1, 60.0, -5.0])
def test_dwell_out_of_range_rejected_before_any_action(bad):
    api = make()
    with pytest.raises(ValueError):
        run(api, port=1, dwell=bad)
    assert api.executed == []  # nothing actuated


@pytest.mark.parametrize("ok", [DWELL_MIN_S, 10.0, DWELL_MAX_S])
def test_dwell_in_range_accepted(ok):
    api = make()
    res = run(api, port=1, dwell=ok)
    assert res.completed


# --- preconditions / mutual exclusion --------------------------------------
def test_refuses_when_other_needle_inserted():
    api = make()
    api.set_gis_needle(2, NEEDLE_INSERTED)
    with pytest.raises(NotSafeToStartError):
        run(api, port=1, dwell=5.0)
    assert api.executed == []  # never touched GIS1


def test_refuses_when_this_valve_open_at_start():
    api = make()
    api.set_gis_valve(1, VALVE_OPEN)
    with pytest.raises(NotSafeToStartError):
        run(api, port=1, dwell=5.0)


def test_refuses_when_this_needle_not_parked_at_start():
    api = make()
    api.set_gis_needle(1, NEEDLE_INSERTED)
    with pytest.raises(NotSafeToStartError):
        run(api, port=1, dwell=5.0)


# --- close-fail policy -----------------------------------------------------
def test_close_fail_retracts_needle_and_alarms():
    api = make()
    api.set_gis_command_stuck("CMD_GIS_CLOSE_CHANNEL")  # valve won't confirm closed
    with pytest.raises(CloseFailure):
        run(api, port=1, dwell=3.0)
    # Per policy, the needle is retracted even though the valve didn't close.
    assert "CMD_GIS_GOTO_PARKED" in api.executed
    assert api.get_digital("DP_GIS_LOCATION") == NEEDLE_PARKED
    # Valve is stuck in transit/error (3), not confirmed Closed -- the alarm tells the
    # operator gas may still be flowing.
    assert api.get_digital("DP_GIS_VALVE_STATE1") == VALVE_TRANSIT


# --- abort / teardown ------------------------------------------------------
def test_operator_decline_at_close_triggers_safe_teardown():
    api = make()

    def confirm(step):
        return "close" not in step  # decline the close step

    with pytest.raises(OperatorDeclined):
        run(api, port=1, dwell=3.0, confirm=confirm)
    # Teardown must leave it safe: valve closed, needle parked.
    assert api.get_digital("DP_GIS_VALVE_STATE1") == VALVE_CLOSED
    assert api.get_digital("DP_GIS_LOCATION") == NEEDLE_PARKED


def test_confirm_called_for_each_step():
    api = make()
    steps = []

    def confirm(step):
        steps.append(step)
        return True

    run(api, port=1, dwell=3.0, confirm=confirm)
    joined = " | ".join(steps)
    assert "insert" in joined and "open" in joined
    assert "close" in joined and "retract" in joined


def test_open_fail_teardown_leaves_needle_parked():
    api = make()
    api.set_gis_command_stuck("CMD_GIS_OPEN_CHANNEL1")  # valve won't open
    with pytest.raises(Exception):
        run(api, port=1, dwell=3.0)
    # After teardown the needle is retracted.
    assert api.get_digital("DP_GIS_LOCATION") == NEEDLE_PARKED


def test_dwell_actually_waits():
    api = make()
    b = CommandGISBackend(api, clock=api.clock, cfg=GISBackendConfig())
    t0 = api.clock.now()
    deposit(api, 1, 12.0, backend=b, clock=api.clock)
    # Elapsed virtual time must include the 12 s dwell (plus insert/retract).
    assert api.clock.now() - t0 >= 12.0


def test_close_through_transit3_still_confirms():
    # Regression for the live finding: the valve reports 3 (transit/error) mid-close
    # (1 -> 3 -> 0). The backend must poll through the 3 and still confirm Closed, so a
    # normal deposition completes without tripping the close-fail path.
    api = make()
    saw_transit = {"v": False}
    real_get = api.get_digital

    def spy(param):
        val = real_get(param)
        if param == "DP_GIS_VALVE_STATE1" and val == VALVE_TRANSIT:
            saw_transit["v"] = True
        return val

    api.get_digital = spy  # observe readings during the run
    res = run(api, port=1, dwell=3.0)
    assert res.completed
    assert saw_transit["v"], "mock should have surfaced valve transit state 3 during close"
    assert api.get_digital("DP_GIS_VALVE_STATE1") == VALVE_CLOSED


# --- insertion preconditions (tilt + stage idle), v0.14.1 ------------------
def test_deposition_refuses_when_stage_busy():
    from gisdep.gis.deposition import NotSafeToStartError
    api = make(tilt_deg=30.0)
    api.set_stage_busy(True)
    with pytest.raises(NotSafeToStartError):
        deposit(api, 1, 2.0, clock=api.clock)


def test_deposition_tilt_out_of_window_aborts_without_confirm():
    from gisdep.gis.deposition import NotSafeToStartError
    api = make(tilt_deg=0.0)               # flat -- not the cryo 29-31 window
    with pytest.raises(NotSafeToStartError):
        deposit(api, 1, 2.0, clock=api.clock)   # no precheck_confirm -> abort


def test_deposition_tilt_out_of_window_proceeds_if_confirmed():
    api = make(tilt_deg=0.0)
    asked = []
    res = deposit(api, 1, 2.0, clock=api.clock,
                  precheck_confirm=lambda msg: asked.append(msg) or True)
    assert res.completed
    assert asked and "proceed" in asked[0].lower()


def test_deposition_tilt_in_window_does_not_prompt():
    api = make(tilt_deg=30.0)
    called = {"n": 0}
    res = deposit(api, 1, 2.0, clock=api.clock,
                  precheck_confirm=lambda msg: called.__setitem__("n", called["n"] + 1) or True)
    assert res.completed
    assert called["n"] == 0                 # in-window tilt never prompts
