import pytest

from gisdep.api.mock import MockSmartSEM, _MOCK_GIS_COMMAND_MAP
from gisdep.clock import FakeClock
from gisdep.gis.actuate import (
    CommandGISBackend,
    GISBackendConfig,
    GISActuationError,
    GISConfirmTimeout,
    MutualExclusionError,
)
from gisdep.gis.commands import GIS_COMMANDS
from gisdep.gis.state import (
    NEEDLE_INSERTED,
    NEEDLE_PARKED,
    VALVE_OPEN,
    VALVE_CLOSED,
)


def make():
    api = MockSmartSEM(clock=FakeClock())
    api.initialise()
    return api


def backend(api):
    return CommandGISBackend(api, clock=api.clock, cfg=GISBackendConfig())


def test_mock_command_map_matches_registry():
    # Drift guard: the mock's instrument-side command set must equal the registry.
    registry = set()
    for c in GIS_COMMANDS.values():
        registry.update({c.insert_needle, c.retract_needle, c.open_valve, c.close_valve})
    assert set(_MOCK_GIS_COMMAND_MAP) == registry


def test_insert_confirms_via_readback():
    api = make()
    b = backend(api)
    b.insert_needle(1)
    assert api.get_digital("DP_GIS_LOCATION") == NEEDLE_INSERTED
    assert api.executed == ["CMD_GIS_GOTO_GAS1"]


def test_insert_passes_through_transit():
    # Right after the command, the mock reads Unknown (transit) before Inserted.
    api = make()
    api.execute("CMD_GIS_GOTO_GAS1")
    assert api.get_digital("DP_GIS_LOCATION") == 0  # Unknown / in transit
    api.clock.advance(6.0)
    assert api.get_digital("DP_GIS_LOCATION") == NEEDLE_INSERTED


def test_full_cycle_port1():
    api = make()
    b = backend(api)
    b.insert_needle(1)
    b.open_valve(1)
    assert api.get_digital("DP_GIS_VALVE_STATE1") == VALVE_OPEN
    b.close_valve(1)
    assert api.get_digital("DP_GIS_VALVE_STATE1") == VALVE_CLOSED
    b.retract_needle(1)
    assert api.get_digital("DP_GIS_LOCATION") == NEEDLE_PARKED


def test_full_cycle_port2_uses_port2_commands():
    api = make()
    b = backend(api)
    b.insert_needle(2)
    b.open_valve(2)
    b.close_valve(2)
    b.retract_needle(2)
    assert api.executed == [
        "CMD_GIS_2ND_INSERT",
        "CMD_GIS_2ND_OPEN_VALVE",
        "CMD_GIS_2ND_CLOSE_VALVE",
        "CMD_GIS_2ND_RETRACT",
    ]


def test_mutual_exclusion_refuses_insert():
    api = make()
    api.set_gis_needle(2, NEEDLE_INSERTED)  # GIS2 already inserted
    b = backend(api)
    with pytest.raises(MutualExclusionError):
        b.insert_needle(1)
    # No insert command was issued.
    assert "CMD_GIS_GOTO_GAS1" not in api.executed


def test_mutual_exclusion_refuses_when_other_in_transit():
    api = make()
    api.execute("CMD_GIS_2ND_INSERT")  # GIS2 needle in transit (Unknown)
    b = backend(api)
    with pytest.raises(MutualExclusionError):
        b.insert_needle(1)


def test_open_valve_guarded_by_needle():
    api = make()
    b = backend(api)
    # Needle still parked -> refuse to open.
    with pytest.raises(GISActuationError):
        b.open_valve(1)
    assert "CMD_GIS_OPEN_CHANNEL1" not in api.executed


def test_insert_timeout_when_stuck():
    api = make()
    api.set_gis_command_stuck("CMD_GIS_GOTO_GAS1")
    b = backend(api)
    with pytest.raises(GISConfirmTimeout):
        b.insert_needle(1)


def test_close_timeout_when_stuck():
    api = make()
    b = backend(api)
    b.insert_needle(1)
    b.open_valve(1)
    api.set_gis_command_stuck("CMD_GIS_CLOSE_CHANNEL")
    with pytest.raises(GISConfirmTimeout):
        b.close_valve(1)


def test_safe_command_from_current_state_is_noop():
    # Commanding the state the hardware is already in must not animate a transition
    # (this is what the Stage-A safe-command check relies on). Already parked + closed:
    api = make()
    api.execute("CMD_GIS_GOTO_PARKED")     # already parked
    api.execute("CMD_GIS_CLOSE_CHANNEL")   # already closed
    # readbacks stay put immediately -- no transit value, no waiting
    assert api.get_digital("DP_GIS_LOCATION") == NEEDLE_PARKED
    assert api.get_digital("DP_GIS_VALVE_STATE1") == VALVE_CLOSED
    # and the commands were accepted (executed without error)
    assert "CMD_GIS_GOTO_PARKED" in api.executed
    assert "CMD_GIS_CLOSE_CHANNEL" in api.executed
