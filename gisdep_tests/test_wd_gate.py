import pytest

from gisdep.focus.wd_gate import WDGateConfig, WDVerdict, evaluate_wd


def test_ideal_at_target():
    r = evaluate_wd(0.0055)
    assert r.verdict is WDVerdict.IDEAL
    assert r.is_ideal
    assert not r.blocks_insertion


def test_too_close_below_floor_blocks():
    # 5.4 mm is below the 5.5 mm floor -> collision risk -> BLOCK
    r = evaluate_wd(0.0054)
    assert r.verdict is WDVerdict.TOO_CLOSE
    assert r.blocks_insertion


def test_safe_suboptimal_above_ideal_band():
    # 6.0 mm: safe but deposition differs.
    r = evaluate_wd(0.0060)
    assert r.verdict is WDVerdict.SAFE_SUBOPTIMAL
    assert not r.blocks_insertion
    assert not r.is_ideal


def test_ideal_band_upper_edge():
    cfg = WDGateConfig(target_m=0.0055, floor_m=0.0055, ideal_tol_m=0.0001)
    # exactly at target + ideal_tol -> still ideal
    assert evaluate_wd(0.0056, cfg).verdict is WDVerdict.IDEAL
    # just above -> suboptimal
    assert evaluate_wd(0.005601, cfg).verdict is WDVerdict.SAFE_SUBOPTIMAL


def test_floor_measurement_slack():
    cfg = WDGateConfig(floor_m=0.0055, measure_tol_m=2e-5)
    # 5.499 mm is within 20 um of the floor -> not blocked (measurement slack)
    assert evaluate_wd(0.005499, cfg).verdict is not WDVerdict.TOO_CLOSE
    # 5.47 mm is clearly below -> blocked
    assert evaluate_wd(0.00547, cfg).verdict is WDVerdict.TOO_CLOSE


def test_mm_property_and_message():
    r = evaluate_wd(0.0055)
    assert r.wd_mm == pytest.approx(5.5)
    assert "insert" in r.message().lower()


def test_bad_config_floor_above_ideal_band():
    with pytest.raises(ValueError):
        WDGateConfig(target_m=0.0055, floor_m=0.010, ideal_tol_m=0.0001)


def test_bad_config_negative():
    with pytest.raises(ValueError):
        WDGateConfig(ideal_tol_m=-1.0)
