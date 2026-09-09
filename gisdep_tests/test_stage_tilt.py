import pytest

from gisdep.api.mock import MockSmartSEM
from gisdep.clock import FakeClock
from gisdep.prereq.checks import check_stage_tilt
from gisdep.prereq.tilt import (
    TiltGateConfig,
    CRYO_GIS_TILT,
    FLAT_STAGE_DEV_TILT,
)


def make(tilt_deg):
    api = MockSmartSEM(clock=FakeClock(), stage_tilt_deg=tilt_deg)
    api.initialise()
    return api


# --- config ----------------------------------------------------------------
def test_cryo_window_values():
    assert CRYO_GIS_TILT.min_deg == 29.0
    assert CRYO_GIS_TILT.max_deg == 31.0


def test_flat_window_accepts_zero():
    assert FLAT_STAGE_DEV_TILT.contains(0.0)
    assert not FLAT_STAGE_DEV_TILT.contains(30.0)


def test_config_rejects_inverted_range():
    with pytest.raises(ValueError):
        TiltGateConfig("bad", min_deg=31.0, max_deg=29.0)


# --- cryo gate --------------------------------------------------------------
@pytest.mark.parametrize("tilt", [29.0, 30.0, 31.0, 29.5, 30.7])
def test_cryo_in_range_ok(tilt):
    assert check_stage_tilt(make(tilt), CRYO_GIS_TILT).ok


@pytest.mark.parametrize("tilt", [0.0, 28.9, 31.1, 45.0, -30.0])
def test_cryo_out_of_range_blocks(tilt):
    r = check_stage_tilt(make(tilt), CRYO_GIS_TILT)
    assert not r.ok
    assert "OUT OF RANGE" in r.detail


def test_flat_dev_stage_passes_at_zero():
    r = check_stage_tilt(make(0.0), FLAT_STAGE_DEV_TILT)
    assert r.ok
    # The non-cryo mode is surfaced loudly in the detail.
    assert "flat_stage_dev" in r.detail
    assert "not the cryo window" in r.detail


def test_flat_dev_stage_still_blocks_a_tilted_stage():
    # Even in dev mode, a stage that is actually at 30 deg is out of the flat window.
    assert not check_stage_tilt(make(30.0), FLAT_STAGE_DEV_TILT).ok


def test_default_gate_is_cryo_and_blocks_flat_stage():
    # Safety: the default (no config passed) is the strict cryo window, so a flat
    # dev stage at 0 is blocked unless the caller explicitly opts into flat mode.
    r = check_stage_tilt(make(0.0))
    assert not r.ok
    assert "cryo_gis" in r.detail


def test_units_mismatch_would_fail_safe():
    # If the instrument ever returned radians (~0.52 for a 30-deg stage), the cryo
    # window [29,31] would NOT contain it -> blocked. Fail-safe against the unit
    # ambiguity rather than silently proceeding.
    assert not check_stage_tilt(make(0.5236), CRYO_GIS_TILT).ok
