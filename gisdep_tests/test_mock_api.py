import pytest

from gisdep.api import params as P
from gisdep.api.interface import SmartSEMError
from gisdep.api.mock import MockSmartSEM, MockAutoConfig
from gisdep.clock import FakeClock


def make(clock=None, **kw):
    api = MockSmartSEM(clock=clock or FakeClock(), **kw)
    api.initialise()
    return api


def test_requires_initialise():
    api = MockSmartSEM(clock=FakeClock())
    with pytest.raises(SmartSEMError) as exc:
        api.get_analogue(P.AP_WD)
    assert exc.value.code == 1019


def test_wd_readback_default():
    api = make(wd_m=0.0055)
    assert api.get_analogue(P.AP_WD) == pytest.approx(0.0055)


def test_stage_state_readbacks():
    api = make(stage_state=P.STAGE_BUSY)
    assert api.get_digital(P.DP_STAGE_IS) == P.STAGE_BUSY
    assert api.get_digital_text(P.DP_STAGE_IS) == "Busy"
    api.set_stage_busy(False)
    assert api.get_digital_text(P.DP_STAGE_IS) == "Idle"


def test_unknown_param_translate_fail():
    api = make()
    with pytest.raises(SmartSEMError) as exc:
        api.get_digital("DP_GIS_NEEDLE")
    assert exc.value.code == 1000  # API_E_GET_TRANSLATE_FAIL


def test_unknown_command_translate_fail():
    api = make()
    with pytest.raises(SmartSEMError) as exc:
        api.execute("CMD_GIS_INSERT")
    assert exc.value.code == 1011  # API_E_EXEC_TRANSLATE_FAIL


def test_auto_function_premature_idle_then_focus_then_idle():
    clock = FakeClock()
    api = make(clock=clock, auto_config=MockAutoConfig(latency=0.4, active=2.0))
    # Before any command: Idle.
    assert api.get_digital(P.DP_AUTO_FUNCTION) == P.AF_IDLE
    api.execute(P.CMD_AUTO_FOCUS_COARSE)
    # Immediately after: STILL Idle (the premature-idle quirk).
    assert api.get_digital(P.DP_AUTO_FUNCTION) == P.AF_IDLE
    clock.advance(0.5)  # past latency
    assert api.get_digital(P.DP_AUTO_FUNCTION) == P.AF_FOCUS
    clock.advance(2.0)  # past active window
    assert api.get_digital(P.DP_AUTO_FUNCTION) == P.AF_IDLE


def test_abort_clears_auto():
    clock = FakeClock()
    api = make(clock=clock, auto_config=MockAutoConfig(latency=0.1, active=5.0))
    api.execute(P.CMD_AUTO_FOCUS_FINE)
    clock.advance(0.2)
    assert api.get_digital(P.DP_AUTO_FUNCTION) == P.AF_FOCUS
    api.execute(P.CMD_ABORT_AUTO)
    assert api.get_digital(P.DP_AUTO_FUNCTION) == P.AF_IDLE


def test_executed_log():
    api = make()
    api.execute(P.CMD_AUTO_FOCUS_COARSE)
    api.execute(P.CMD_AUTO_FOCUS_FINE)
    assert api.executed == [P.CMD_AUTO_FOCUS_COARSE, P.CMD_AUTO_FOCUS_FINE]


def test_stage_position_default_flat():
    api = make()
    pos = api.get_stage_position()
    assert pos.tilt_deg == 0.0  # flat dev stage by default


def test_stage_position_tilt_settable():
    api = make(stage_tilt_deg=30.0)
    assert api.get_stage_position().tilt_deg == pytest.approx(30.0)
    api.set_stage_tilt(29.7)
    assert api.get_stage_position().tilt_deg == pytest.approx(29.7)


def test_stage_position_returns_copy():
    api = make(stage_tilt_deg=30.0)
    pos = api.get_stage_position()
    pos.tilt_deg = 999.0  # mutating the returned copy must not affect the mock
    assert api.get_stage_position().tilt_deg == pytest.approx(30.0)


def test_stage_position_requires_init():
    api = MockSmartSEM(clock=FakeClock())
    with pytest.raises(SmartSEMError) as exc:
        api.get_stage_position()
    assert exc.value.code == 1019
