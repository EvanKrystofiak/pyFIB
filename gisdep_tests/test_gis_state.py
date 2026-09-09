import pytest

from gisdep.api.interface import SmartSEMError
from gisdep.api.mock import MockSmartSEM, _MOCK_GIS_PARAM_MAP
from gisdep.clock import FakeClock
from gisdep.gis.state import (
    GIS1,
    GIS2,
    GIS_PARAM_MAP,
    read_port,
    read_all,
    interpret_valve,
    interpret_needle,
    VALVE_CLOSED,
    VALVE_OPEN,
    VALVE_TRANSIT,
    NEEDLE_UNKNOWN,
    NEEDLE_PARKED,
    NEEDLE_INSERTED,
    DP_GIS1_VALVE,
    DP_GIS2_VALVE,
    DP_GIS1_NEEDLE,
    DP_GIS2_NEEDLE,
)


def make(gis=None):
    api = MockSmartSEM(clock=FakeClock())
    api.initialise()
    for (port, field), value in (gis or {}).items():
        if field == "needle":
            api.set_gis_needle(port, value)
        else:
            api.set_gis_valve(port, value)
    return api


# --- exact mnemonics (guard against accidental "normalisation") ------------
def test_exact_mnemonic_strings():
    assert DP_GIS1_VALVE == "DP_GIS_VALVE_STATE1"
    assert DP_GIS2_VALVE == "DP_GIS_2ND_VALVE_STATE"
    assert DP_GIS1_NEEDLE == "DP_GIS_LOCATION"
    assert DP_GIS2_NEEDLE == "DP_GIS_2ND_STAGE_LOCATION"


def test_all_readback_mnemonics_are_uppercase():
    # DP read mnemonics are all-uppercase (instrument rule, same as CMD_ commands).
    for param in (DP_GIS1_VALVE, DP_GIS2_VALVE, DP_GIS1_NEEDLE, DP_GIS2_NEEDLE):
        assert param == param.upper(), f"readback not uppercase: {param}"
        assert param.startswith("DP_")


def test_mock_param_map_matches_state_module():
    # Drift guard: the mock's instrument-side params must match the tool's map.
    assert set(_MOCK_GIS_PARAM_MAP) == set(GIS_PARAM_MAP)
    assert _MOCK_GIS_PARAM_MAP == GIS_PARAM_MAP


# --- interpretation --------------------------------------------------------
def test_interpret_valve():
    assert interpret_valve(VALVE_CLOSED) == "Closed"
    assert interpret_valve(VALVE_OPEN) == "Open"
    assert "Transit" in interpret_valve(VALVE_TRANSIT)   # value 3, observed live
    assert "UNEXPECTED" in interpret_valve(7)
    assert interpret_valve(None) == "?"


def test_valve_transit_reading_is_neither_open_nor_closed():
    api = make({(1, "valve"): VALVE_TRANSIT})
    r = read_port(api, GIS1)
    assert r.valve_in_transit
    assert not r.valve_open and not r.valve_closed
    assert not r.is_safe


def test_interpret_needle():
    assert "Parked" in interpret_needle(NEEDLE_PARKED)
    assert "Inserted" in interpret_needle(NEEDLE_INSERTED)
    assert "Transit" in interpret_needle(NEEDLE_UNKNOWN)
    assert "UNEXPECTED" in interpret_needle(5)


# --- reader ----------------------------------------------------------------
def test_read_default_safe_state():
    api = make()
    r = read_port(api, GIS1)
    assert r.needle_parked and r.valve_closed
    assert r.is_safe
    assert r.needle_raw == NEEDLE_PARKED
    assert r.valve_raw == VALVE_CLOSED


def test_read_inserted_and_open():
    api = make({(1, "needle"): NEEDLE_INSERTED, (1, "valve"): VALVE_OPEN})
    r = read_port(api, GIS1)
    assert r.needle_inserted and r.valve_open
    assert not r.is_safe


def test_read_instrument_text_crosscheck():
    api = make({(1, "needle"): NEEDLE_INSERTED})
    r = read_port(api, GIS1, read_text=True)
    # Instrument reports "Gas 1" text for an inserted needle.
    assert r.needle_instrument_text == "Gas 1"
    # Our interpretation from the integer:
    assert "Inserted" in r.needle_meaning


def test_read_ports_are_independent():
    api = make({(1, "needle"): NEEDLE_INSERTED, (2, "needle"): NEEDLE_PARKED})
    r = read_all(api)
    assert r[1].needle_inserted
    assert r[2].needle_parked


def test_read_absent_param_is_flagged_not_raised():
    # If a mnemonic doesn't translate (rc=1000), the reader marks it absent instead of
    # raising, so the verification tool degrades gracefully.
    api = MockSmartSEM(clock=FakeClock())
    api.initialise()
    bad = GIS1.__class__(port=9, valve_param="DP_NOPE", needle_param="DP_ALSO_NOPE")
    r = read_port(api, bad)
    assert not r.valve_present
    assert not r.needle_present
    assert r.valve_meaning == "?"


def test_unknown_needle_value_zero():
    api = make({(1, "needle"): NEEDLE_UNKNOWN})
    r = read_port(api, GIS1)
    assert not r.needle_parked and not r.needle_inserted
    assert "Transit" in r.needle_meaning
