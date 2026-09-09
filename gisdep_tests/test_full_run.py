import pytest

from gisdep.api.mock import MockSmartSEM, MockAutoConfig
from gisdep.clock import FakeClock
from gisdep.focus.autofocus import AutoFocusConfig
from gisdep.gis.state import NEEDLE_INSERTED, NEEDLE_PARKED, VALVE_CLOSED
from gisdep.orchestrate.full_run import (
    focus_and_set_wd,
    run_full_workflow,
    FullRunAborted,
)
from gisdep.stage.cryo import CryoWaypoint
from gisdep.stage.mover import StageMoveConfig

# waypoints inside the mock's stage limits
WP = {
    "CLEM_SAFE": CryoWaypoint("CLEM_SAFE", 0.000, 0.000, 0.022, 0.0, 0.0),
    "GRID1": CryoWaypoint("GRID1", 0.001, 0.002, 0.030, 30.0, 0.0),
    "GRID2": CryoWaypoint("GRID2", -0.001, 0.002, 0.030, 30.0, 180.0),
}


def make(wd_m=0.0057):
    api = MockSmartSEM(
        clock=FakeClock(), wd_m=wd_m, stage_z_m=0.030,
        auto_config=MockAutoConfig(latency=0.2, active=0.5),
    )
    api.initialise()
    return api


def fast_af():
    return AutoFocusConfig(sustained_idle_s=1.0, startup_timeout_s=10.0,
                           overall_timeout_s=60.0, poll_interval_s=0.25)


# --- focus_and_set_wd ------------------------------------------------------
def test_focus_and_set_wd_reaches_target_and_reconfirms():
    api = make(wd_m=0.0057)
    res = focus_and_set_wd(api, 0.0055, clock=api.clock, af_cfg=fast_af())
    # one Z step brought WD to target (mock: autofocus does not change WD)
    assert res.wd_move.reached
    assert api.get_analogue("AP_WD") == pytest.approx(0.0055, abs=1e-6)
    # coarse+fine (2) then a confirming fine (1) -> 3 auto-focus commands issued
    assert api.executed.count("CMD_AUTO_FOCUS_COARSE") == 1
    assert api.executed.count("CMD_AUTO_FOCUS_FINE") == 2


def test_focus_and_set_wd_confirm_gate_aborts():
    # Autofocus no longer prompts (beam-only, safe); the Z move is the confirmed step.
    api = make()
    with pytest.raises(Exception):
        focus_and_set_wd(api, 0.0055, clock=api.clock, af_cfg=fast_af(),
                         confirm=lambda step: "stage" not in step.lower())


# --- full workflow ---------------------------------------------------------
def run(api, grid="GRID1", port=1, dwell=2.0, confirm=None):
    return run_full_workflow(
        api, grid, port, dwell, waypoints=WP, clock=api.clock,
        confirm=confirm, af_cfg=fast_af(),
        wd_cfg=StageMoveConfig(z_min_m=0.020, z_max_m=0.040,
                               verify_tol_m=1e-5, verify_tol_deg=0.1),
    )


def test_full_workflow_runs_all_three_steps():
    api = make(wd_m=0.0057)
    res = run(api)
    # Step 1: ended at the grid
    assert res.cryo.reached
    # Step 3: deposition completed and left a safe state
    assert res.deposition.completed
    assert api.get_digital("DP_GIS_LOCATION") == NEEDLE_PARKED
    assert api.get_digital("DP_GIS_VALVE_STATE1") == VALVE_CLOSED
    # the GIS deposition commands were issued in order
    depo = [c for c in api.executed if c.startswith("CMD_GIS_")]
    assert depo == [
        "CMD_GIS_GOTO_GAS1", "CMD_GIS_OPEN_CHANNEL1",
        "CMD_GIS_CLOSE_CHANNEL", "CMD_GIS_GOTO_PARKED",
    ]


def test_full_workflow_confirm_structure():
    api = make(wd_m=0.0057)
    steps = []
    run(api, confirm=lambda s: steps.append(s) or True)
    joined = " | ".join(steps).lower()
    # cryo: ONE confirm per phase, with coordinates (not per sub-move)
    assert "move to clem safe:" in joined
    assert "move to grid1:" in joined
    # the Z-to-WD move is confirmed
    assert "move stage z" in joined
    # deposition: ONE gate before it, and NO per-step (insert/open/close) prompts
    assert "start deposition" in joined
    assert "insert gis" not in joined and "open gis" not in joined
    # autofocus no longer prompts
    assert "autofocus" not in joined


def test_full_workflow_aborts_if_cryo_needle_inserted():
    api = make()
    api.set_gis_needle(1, NEEDLE_INSERTED)  # can't move stage with needle inserted
    with pytest.raises(Exception):
        run(api)


def test_full_workflow_decline_aborts_cleanly():
    api = make(wd_m=0.0057)
    # decline the very first cryo sub-move
    with pytest.raises(Exception):
        run(api, confirm=lambda s: False)
