import os

import pytest

from gisdep.gui.state_images import (
    image_for_gis,
    active_port_state,
    asset_path,
    ALL_ASSETS,
    NORMAL,
    GIS_INSERT,
    GIS_ON,
    START,
    BLOCKED,
)
from gisdep.gis.state import (
    NEEDLE_PARKED,
    NEEDLE_INSERTED,
    NEEDLE_UNKNOWN,
    VALVE_CLOSED,
    VALVE_OPEN,
    VALVE_TRANSIT,
    GIS1,
    GIS2,
    read_all,
)
from gisdep.api.mock import MockSmartSEM
from gisdep.clock import FakeClock


# --- the three provided artworks map to the three defined states -----------
def test_ready_state_uses_normal():
    s = image_for_gis(NEEDLE_PARKED, VALVE_CLOSED)
    assert s.filename == NORMAL and not s.depositing and not s.transit


def test_inserted_closed_uses_insert():
    s = image_for_gis(NEEDLE_INSERTED, VALVE_CLOSED)
    assert s.filename == GIS_INSERT and not s.depositing


def test_inserted_open_uses_on_and_flags_depositing():
    s = image_for_gis(NEEDLE_INSERTED, VALVE_OPEN)
    assert s.filename == GIS_ON and s.depositing


# --- transit states are flagged (so the GUI can keep the last stable image) -
def test_valve_transit_flagged():
    s = image_for_gis(NEEDLE_INSERTED, VALVE_TRANSIT)
    assert s.transit and "moving" in s.caption.lower()


def test_needle_transit_flagged():
    s = image_for_gis(NEEDLE_UNKNOWN, VALVE_CLOSED)
    assert s.transit and "moving" in s.caption.lower()


def test_port_appears_in_caption():
    assert "GIS2" in image_for_gis(NEEDLE_INSERTED, VALVE_CLOSED, port=2).caption


# --- active port selection -------------------------------------------------
def make(gis=None):
    api = MockSmartSEM(clock=FakeClock())
    api.initialise()
    for (port, field), value in (gis or {}).items():
        (api.set_gis_needle if field == "needle" else api.set_gis_valve)(port, value)
    return api


def test_active_port_defaults_to_gis1_when_both_parked():
    api = make()
    s = active_port_state(read_all(api))
    assert "GIS1" in s.caption or s.filename == NORMAL


def test_active_port_follows_gis2_when_gis2_inserted():
    api = make({(2, "needle"): NEEDLE_INSERTED})
    s = active_port_state(read_all(api))
    assert "GIS2" in s.caption


# --- assets exist on disk --------------------------------------------------
def test_all_assets_present():
    for name in ALL_ASSETS:
        p = asset_path(name)
        assert os.path.isfile(p), f"missing asset: {p}"
        assert os.path.getsize(p) > 0
