import pytest

from gisdep.gis.commands import (
    GIS1_COMMANDS,
    GIS2_COMMANDS,
    GIS_COMMANDS,
    commands_for,
)
from gisdep.gis.state import GIS1, GIS2


# --- verbatim strings (pin every character, including inconsistent casing) --
def test_gis1_command_strings_exact():
    assert GIS1_COMMANDS.insert_needle == "CMD_GIS_GOTO_GAS1"
    assert GIS1_COMMANDS.retract_needle == "CMD_GIS_GOTO_PARKED"
    assert GIS1_COMMANDS.open_valve == "CMD_GIS_OPEN_CHANNEL1"
    assert GIS1_COMMANDS.close_valve == "CMD_GIS_CLOSE_CHANNEL"


def test_gis2_command_strings_exact():
    # All CMD_ commands are uppercase (instrument rule).
    assert GIS2_COMMANDS.insert_needle == "CMD_GIS_2ND_INSERT"
    assert GIS2_COMMANDS.retract_needle == "CMD_GIS_2ND_RETRACT"
    assert GIS2_COMMANDS.open_valve == "CMD_GIS_2ND_OPEN_VALVE"
    assert GIS2_COMMANDS.close_valve == "CMD_GIS_2ND_CLOSE_VALVE"


def test_all_command_strings_are_uppercase():
    # Enforce the instrument rule that CMD_ commands are ALL-UPPERCASE.
    for port, c in GIS_COMMANDS.items():
        for cmd in (c.insert_needle, c.retract_needle, c.open_valve, c.close_valve):
            assert cmd == cmd.upper(), f"GIS{port} command not uppercase: {cmd}"
            assert cmd.startswith("CMD_")


def test_ports_share_no_command_strings():
    g1 = {GIS1_COMMANDS.insert_needle, GIS1_COMMANDS.retract_needle,
          GIS1_COMMANDS.open_valve, GIS1_COMMANDS.close_valve}
    g2 = {GIS2_COMMANDS.insert_needle, GIS2_COMMANDS.retract_needle,
          GIS2_COMMANDS.open_valve, GIS2_COMMANDS.close_valve}
    assert g1.isdisjoint(g2)


def test_readbacks_wired_to_state_module():
    assert GIS1_COMMANDS.readbacks is GIS1
    assert GIS2_COMMANDS.readbacks is GIS2
    assert GIS1_COMMANDS.readbacks.needle_param == "DP_GIS_LOCATION"
    assert GIS2_COMMANDS.readbacks.valve_param == "DP_GIS_2ND_VALVE_STATE"


def test_commands_for_lookup():
    assert commands_for(1) is GIS1_COMMANDS
    assert commands_for(2) is GIS2_COMMANDS


def test_commands_for_unknown_port_raises():
    with pytest.raises(ValueError):
        commands_for(3)


def test_registry_ports():
    assert set(GIS_COMMANDS) == {1, 2}
    assert GIS_COMMANDS[1].port == 1
    assert GIS_COMMANDS[2].port == 2


def test_nominal_timings_are_measured_values():
    # Present for reference; MEASURED live per port (readback-confirmed, no gas).
    assert GIS1_COMMANDS.nominal_insert_s == 2.1
    assert GIS1_COMMANDS.nominal_retract_s == 2.1
    assert GIS2_COMMANDS.nominal_insert_s == 3.4
    assert GIS2_COMMANDS.nominal_retract_s == 1.8


def test_commands_are_frozen():
    # Registry is immutable data.
    with pytest.raises(Exception):
        GIS1_COMMANDS.insert_needle = "something else"
