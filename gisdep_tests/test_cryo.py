import pytest

from gisdep.api.mock import MockSmartSEM
from gisdep.clock import FakeClock
from gisdep.gis.state import NEEDLE_INSERTED
from gisdep.stage.cryo import CryoPositioner, CryoWaypoint, DEFAULT_WAYPOINTS
from gisdep.stage.mover import StageSafetyError, StageMoveConfig


# waypoints within the mock's stage limits (X/Y +-0.05, Z [0.020,0.040])
WP = {
    "CLEM_SAFE": CryoWaypoint("CLEM_SAFE", 0.000, 0.000, 0.022, 0.0, 0.0),
    "GRID1": CryoWaypoint("GRID1", 0.001, 0.002, 0.030, 30.0, 0.0),
    "GRID2": CryoWaypoint("GRID2", -0.001, 0.002, 0.030, 30.0, 180.0),
}


def make(x=0.003, y=0.003, z=0.035, t=10.0, r=45.0, m=0.007):
    api = MockSmartSEM(clock=FakeClock(), stage_z_m=z)
    api.initialise()
    # set a distinct starting pose (incl. a non-zero master-z to prove M never moves)
    api.move_stage(x, y, z, t, r, m)
    api.clock.sleep(api._stage_move_dur)  # settle
    api.get_stage_position()
    api.stage_moves.clear()               # ignore the setup move
    return api


def positioner(api):
    cfg = StageMoveConfig(verify_tol_m=1e-5, verify_tol_deg=0.1, idle_timeout_s=120.0)
    return CryoPositioner(api, waypoints=WP, clock=api.clock, cfg=cfg)


def _changed_axes(prev, cur, tol=1e-9):
    names = ["x", "y", "z", "t", "r", "m"]
    return {names[i] for i in range(6) if abs(cur[i] - prev[i]) > tol}


def test_reaches_grid_position():
    api = make()
    p = positioner(api)
    rep = p.go_to_grid("GRID1")
    assert rep.reached
    pos = api.get_stage_position()
    assert pos.x_m == pytest.approx(WP["GRID1"].x_m, abs=1e-6)
    assert pos.z_m == pytest.approx(WP["GRID1"].z_m, abs=1e-6)
    assert pos.tilt_deg == pytest.approx(30.0, abs=1e-3)


def test_move_order_z_first_to_clem_then_xytr_then_xytr_then_z():
    api = make()
    p = positioner(api)
    p.go_to_grid("GRID1")
    moves = api.stage_moves
    assert len(moves) == 4  # Z, XYTR (to CLEM), XYTR, Z (to grid)

    start = (0.003, 0.003, 0.035, 10.0, 45.0, 0.007)
    # move 1 -> only Z changed (to CLEM SAFE Z): Z isolated before any lateral/tilt move
    assert _changed_axes(start, moves[0]) == {"z"}
    assert moves[0][2] == pytest.approx(WP["CLEM_SAFE"].z_m)
    # move 2 -> lateral/tilt/rotation only, Z stays at CLEM SAFE
    ch2 = _changed_axes(moves[0], moves[1])
    assert "z" not in ch2 and ch2 and ch2 <= {"x", "y", "t", "r"}
    assert moves[1][2] == pytest.approx(WP["CLEM_SAFE"].z_m)
    # move 3 -> lateral/tilt/rotation only (to grid), Z still at CLEM SAFE
    ch3 = _changed_axes(moves[1], moves[2])
    assert "z" not in ch3 and ch3 and ch3 <= {"x", "y", "t", "r"}
    assert moves[2][2] == pytest.approx(WP["CLEM_SAFE"].z_m)
    # move 4 -> only Z changed (to grid Z): the approach, done last
    assert _changed_axes(moves[2], moves[3]) == {"z"}
    assert moves[3][2] == pytest.approx(WP["GRID1"].z_m)


def test_master_z_never_moves():
    api = make(m=0.007)
    p = positioner(api)
    p.go_to_grid("GRID2")
    # every commanded move kept M at the starting 0.007
    for mv in api.stage_moves:
        assert mv[5] == pytest.approx(0.007)
    assert api.get_stage_position().m_m == pytest.approx(0.007)


def test_always_goes_via_clem_safe():
    api = make()
    p = positioner(api)
    p.go_to_grid("GRID1")
    # the 2nd commanded move (end of phase A) sits at CLEM SAFE X,Y,T,R,Z
    clem_move = api.stage_moves[1]
    assert clem_move[0] == pytest.approx(WP["CLEM_SAFE"].x_m)
    assert clem_move[2] == pytest.approx(WP["CLEM_SAFE"].z_m)
    assert clem_move[4] == pytest.approx(WP["CLEM_SAFE"].rot_deg)


def test_refuses_when_gis_needle_inserted():
    api = make()
    api.set_gis_needle(1, NEEDLE_INSERTED)
    p = positioner(api)
    with pytest.raises(StageSafetyError):
        p.go_to_grid("GRID1")
    assert api.stage_moves == []  # nothing moved


def test_unknown_grid_rejected():
    api = make()
    p = positioner(api)
    with pytest.raises(ValueError):
        p.go_to_grid("GRID9")


def test_default_waypoints_are_real_coordinates():
    from gisdep.stage.cryo import DEFAULT_WAYPOINTS as W
    assert all(not w.placeholder for w in W.values())
    # values match the provided files (mm -> m; degrees)
    assert W["CLEM_SAFE"].x_m == pytest.approx(0.040561)
    assert W["CLEM_SAFE"].y_m == pytest.approx(0.057164)
    assert W["CLEM_SAFE"].z_m == pytest.approx(0.02300)
    assert W["CLEM_SAFE"].tilt_deg == 0.0 and W["CLEM_SAFE"].rot_deg == 12.0
    assert W["GRID1"].x_m == pytest.approx(0.044691)
    assert W["GRID1"].z_m == pytest.approx(0.029497)
    assert W["GRID1"].tilt_deg == 30.0
    assert W["GRID2"].x_m == pytest.approx(0.048534)
    assert W["GRID2"].tilt_deg == 30.0
    # M recorded (12.99 mm) on every waypoint, for the mismatch warning
    assert all(w.m_ref_m == pytest.approx(0.01299) for w in W.values())


def test_real_waypoints_run_via_clem_safe_in_mock():
    # A plain mock must accommodate the real coordinates (Y up to 57 mm).
    api = MockSmartSEM(clock=FakeClock(), stage_z_m=0.030)
    api.initialise()
    # start at recorded M so no mismatch warning
    api.move_stage(0.0, 0.0, 0.030, 0.0, 0.0, 0.01299)
    api.clock.sleep(api._stage_move_dur)
    api.get_stage_position()
    p = CryoPositioner(api, clock=api.clock,
                       cfg=StageMoveConfig(verify_tol_m=1e-5, verify_tol_deg=0.1))
    rep = p.go_to_grid("GRID1")
    assert rep.reached and not rep.used_placeholders
    pos = api.get_stage_position()
    assert pos.x_m == pytest.approx(0.044691, abs=1e-6)
    assert pos.y_m == pytest.approx(0.051183, abs=1e-6)
    assert pos.z_m == pytest.approx(0.029497, abs=1e-6)
    assert pos.tilt_deg == pytest.approx(30.0, abs=1e-3)
    assert pos.m_m == pytest.approx(0.01299, abs=1e-9)  # M unchanged at recorded value


def test_m_mismatch_warns_but_does_not_move_m():
    api = MockSmartSEM(clock=FakeClock(), stage_z_m=0.030)
    api.initialise()
    api.move_stage(0.0, 0.0, 0.030, 0.0, 0.0, 0.010)  # M = 10 mm, not the recorded 12.99
    api.clock.sleep(api._stage_move_dur)
    api.get_stage_position()
    p = CryoPositioner(api, clock=api.clock,
                       cfg=StageMoveConfig(verify_tol_m=1e-5, verify_tol_deg=0.1))
    rep = p.go_to_grid("GRID2")
    assert not rep.m_matches
    assert any("WARNING" in s and "M" in s for s in rep.steps)
    # M is preserved at the current 10 mm -- never moved to 12.99
    assert api.get_stage_position().m_m == pytest.approx(0.010, abs=1e-9)
