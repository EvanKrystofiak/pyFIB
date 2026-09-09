import pytest

from gisdep.api.mock import MockSmartSEM
from gisdep.clock import FakeClock
from gisdep.gis.backend import StubGISBackend, MockGISBackend
from gisdep.gis.probe import GISProbe


# --- backend scaffold ------------------------------------------------------
def test_stub_backend_refuses_everything():
    b = StubGISBackend()
    for fn in (b.insert_needle, b.retract_needle, b.open_valve, b.close_valve):
        with pytest.raises(NotImplementedError):
            fn(1)


def test_mock_backend_tracks_state_and_calls():
    b = MockGISBackend(ports=(1, 2))
    assert b.needle_inserted(1) is False
    assert b.valve_open(1) is False
    b.insert_needle(1)
    b.open_valve(1)
    b.close_valve(1)
    b.retract_needle(1)
    assert b.needle_inserted(1) is False
    assert b.calls == [
        ("insert_needle", 1),
        ("open_valve", 1),
        ("close_valve", 1),
        ("retract_needle", 1),
    ]


def test_mock_backend_rejects_unknown_port():
    b = MockGISBackend(ports=(1,))
    with pytest.raises(ValueError):
        b.insert_needle(2)


# --- read-only probe -------------------------------------------------------
def make_api():
    api = MockSmartSEM(clock=FakeClock())
    api.initialise()
    return api


def test_probe_finds_confirmed_gis_params():
    # The mock now models the four confirmed GIS readbacks, and the probe lists them
    # among its candidates, so a probe run re-confirms them as present.
    report = GISProbe().probe(make_api())
    present = {e.mnemonic for e in report.present}
    assert "DP_GIS_VALVE_STATE1" in present
    assert "DP_GIS_2ND_VALVE_STATE" in present
    assert "DP_GIS_LOCATION" in present
    assert "DP_GIS_2ND_STAGE_LOCATION" in present
    # Purely speculative guesses remain absent.
    assert "DP_GIS_NEEDLE" not in present
    assert "DP_GIS_VALVE" not in present
    # Command candidates are carried through for manual inspection, never executed.
    assert report.command_candidates
    assert make_api().executed == []  # probe never executes anything


def test_probe_does_not_execute_commands():
    api = make_api()
    GISProbe().probe(api)
    assert api.executed == []  # strictly read-only


def test_probe_detects_present_parameter():
    api = make_api()
    # Inject a hidden parameter to simulate discovery on a real instrument.
    api._dp["DP_GIS_NEEDLE_1"] = 1
    report = GISProbe(digital=["DP_GIS_NEEDLE_1", "DP_GIS_NOPE"]).probe(api)
    present = {e.mnemonic for e in report.present}
    assert "DP_GIS_NEEDLE_1" in present
    assert "DP_GIS_NOPE" not in present


def test_probe_summary_renders():
    report = GISProbe().probe(make_api())
    s = report.summary()
    assert "READ-ONLY" in s
    assert "CMD_" in s
