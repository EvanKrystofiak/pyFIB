import json

import pytest

from gisdep.config import load_config, DEFAULTS


def test_defaults_are_real_coordinates():
    cfg = load_config(path="/nonexistent/does-not-exist.json")
    assert cfg.source == "defaults"
    assert cfg.wd_target_m == pytest.approx(0.0055)
    assert cfg.z_min_m == pytest.approx(0.020) and cfg.z_max_m == pytest.approx(0.040)
    assert cfg.tilt_window_deg() == (29.0, 31.0)
    assert cfg.m_ref_m == pytest.approx(0.01299)
    wp = cfg.waypoints_si()
    assert wp["CLEM_SAFE"]["x_m"] == pytest.approx(0.040561)
    assert wp["GRID1"]["z_m"] == pytest.approx(0.029497)
    assert wp["GRID1"]["tilt_deg"] == 30.0
    assert wp["GRID2"]["x_m"] == pytest.approx(0.048534)


def test_file_overrides_and_merges(tmp_path):
    p = tmp_path / "gisdep_config.json"
    # override just the WD target and one waypoint; the rest must fall back to defaults
    p.write_text(json.dumps({
        "wd_target_mm": 5.4,
        "waypoints": {"GRID1": {"x_mm": 50.0, "y_mm": 50.0, "z_mm": 29.0, "t_deg": 30, "r_deg": 12}},
    }))
    cfg = load_config(path=str(p))
    assert cfg.source == str(p)
    assert cfg.wd_target_m == pytest.approx(0.0054)           # overridden
    assert cfg.z_min_m == pytest.approx(0.020)                # default kept
    wp = cfg.waypoints_si()
    assert wp["GRID1"]["x_m"] == pytest.approx(0.050)         # overridden
    assert wp["CLEM_SAFE"]["x_m"] == pytest.approx(0.040561)  # default kept


def test_bad_file_falls_back_to_defaults(tmp_path):
    p = tmp_path / "gisdep_config.json"
    p.write_text("{ this is not valid json ]")
    cfg = load_config(path=str(p))
    assert cfg.source == "defaults"
    assert cfg.wd_target_m == pytest.approx(0.0055)


def test_env_var_path(tmp_path, monkeypatch):
    p = tmp_path / "custom.json"
    p.write_text(json.dumps({"dwell_default_s": 7}))
    monkeypatch.setenv("GISDEP_CONFIG", str(p))
    cfg = load_config()
    assert cfg.dwell_default_s == 7.0


def test_cryo_default_waypoints_come_from_config():
    # gisdep.stage.cryo builds DEFAULT_WAYPOINTS from the config defaults
    from gisdep.stage.cryo import DEFAULT_WAYPOINTS
    assert DEFAULT_WAYPOINTS["GRID1"].x_m == pytest.approx(0.044691)
    assert DEFAULT_WAYPOINTS["GRID1"].tilt_deg == 30.0
    assert all(not w.placeholder for w in DEFAULT_WAYPOINTS.values())


def test_cryo_tilt_window_comes_from_config():
    from gisdep.prereq.tilt import CRYO_GIS_TILT
    assert (CRYO_GIS_TILT.min_deg, CRYO_GIS_TILT.max_deg) == (29.0, 31.0)
