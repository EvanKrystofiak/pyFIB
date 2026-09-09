import pytest

from gisdep.api.mock import MockSmartSEM
from gisdep.clock import FakeClock
from gisdep.gis.state import NEEDLE_INSERTED
from gisdep.stage.mover import (
    StageMover,
    StageMoveConfig,
    StageSafetyError,
    StageVerifyError,
    StageMoveTimeout,
    StageMoveDeclined,
)


def make(**kw):
    api = MockSmartSEM(clock=FakeClock(), **kw)
    api.initialise()
    return api


def mover(api, **cfg):
    base = dict(poll_interval_s=0.25, idle_timeout_s=60.0)
    base.update(cfg)
    return StageMover(api, clock=api.clock, cfg=StageMoveConfig(**base))


# --- happy path ------------------------------------------------------------
def test_relative_move_updates_position_and_verifies():
    api = make()
    m = mover(api)
    before = m.read()
    res = m.move_axis_relative("x", 20e-6)  # 20 um
    assert res.measured_delta == pytest.approx(20e-6, abs=1e-9)
    assert m.read().x_m == pytest.approx(before.x_m + 20e-6, abs=1e-9)


# --- confirmed instrument findings (dev-stage nudge, +200 um Z -> -200 um WD) ------
def test_z_sign_positive_command_gives_positive_motion():
    # CONFIRMED on the instrument: a +Z command produces +Z motion (no inversion).
    api = make()
    m = mover(api)
    z0 = m.read().z_m
    res = m.move_axis_relative("z", 200e-6)
    assert res.measured_delta > 0
    assert m.read().z_m == pytest.approx(z0 + 200e-6, abs=1e-9)


def test_z_scale_measured_matches_requested():
    # CONFIRMED: measured move == requested (metres), to well under a micron.
    api = make()
    m = mover(api)
    res = m.move_axis_relative("z", 200e-6)
    assert res.measured_delta == pytest.approx(200e-6, abs=5e-9)


def test_plus_z_shortens_wd_one_to_one():
    # CONFIRMED: dWD/dZ = -1. +200 um Z -> -200 um WD.
    api = make()
    m = mover(api)
    wd0 = api.get_analogue("AP_WD")
    m.move_axis_relative("z", 200e-6)
    wd1 = api.get_analogue("AP_WD")
    assert wd1 == pytest.approx(wd0 - 200e-6, abs=1e-9)


def test_minus_z_lengthens_wd_one_to_one():
    api = make()
    m = mover(api)
    wd0 = api.get_analogue("AP_WD")
    m.move_axis_relative("z", -150e-6)
    assert api.get_analogue("AP_WD") == pytest.approx(wd0 + 150e-6, abs=1e-9)


def test_z_nudge_round_trip_restores_wd():
    api = make()
    m = mover(api)
    wd0 = api.get_analogue("AP_WD")
    m.nudge_and_verify("z", 200e-6)  # out then back
    assert api.get_analogue("AP_WD") == pytest.approx(wd0, abs=1e-9)


# --- move_to_wd (single bounded step) --------------------------------------
def test_move_to_wd_reaches_target_in_one_step():
    # WD 5.7 mm, target 5.5 mm -> needs +0.2 mm Z (within cap and limits) -> reaches.
    api = make(wd_m=0.0057)  # stage Z default 0.030
    m = mover(api)
    r = m.move_to_wd(0.0055)
    assert r.moved and r.reached and not r.clamped
    assert api.get_analogue("AP_WD") == pytest.approx(0.0055, abs=1e-6)
    assert r.applied_dz_m == pytest.approx(0.0002, abs=1e-9)  # +0.2 mm Z


def test_move_to_wd_already_at_target_no_move():
    api = make(wd_m=0.0055)
    m = mover(api)
    r = m.move_to_wd(0.0055)
    assert not r.moved and r.reached


def test_move_to_wd_single_step_capped_and_under_max():
    # WD far off (21 mm), target 5.5 mm -> full correction ~15.5 mm, but a single step
    # must stay under the 1 mm cap. Start Z mid-range with wide limits.
    api = make(wd_m=0.021, stage_z_m=0.030)
    m = mover(api, max_delta_m=0.001, z_min_m=0.020, z_max_m=0.040)
    r = m.move_to_wd(0.0055)
    assert r.moved and r.clamped and not r.reached
    assert abs(r.applied_dz_m) <= 0.001 + 1e-12  # under the maximum allowed movement
    # one step reduced WD by exactly the applied Z (1:1)
    assert api.get_analogue("AP_WD") == pytest.approx(0.021 - r.applied_dz_m, abs=1e-9)


def test_move_to_wd_clamped_by_z_limit():
    # Near the Z ceiling: a +Z step to shorten WD is limited by z_max.
    api = make(wd_m=0.0070, stage_z_m=0.0399)  # 39.9 mm, ceiling 40 mm
    m = mover(api, z_min_m=0.020, z_max_m=0.040)
    r = m.move_to_wd(0.0055)  # wants +1.5 mm Z, but only 0.1 mm headroom
    assert r.moved and r.clamped and not r.reached
    assert r.applied_dz_m == pytest.approx(0.0001, abs=1e-6)  # clamped to the ceiling


def test_move_to_wd_respects_gis_interlock():
    api = make(wd_m=0.0057)
    api.set_gis_needle(1, NEEDLE_INSERTED)
    m = mover(api)
    with pytest.raises(StageSafetyError):
        m.move_to_wd(0.0055)


def test_wd_move_result_message():
    api = make(wd_m=0.0057)
    m = mover(api)
    r = m.move_to_wd(0.0055)
    assert "reached" in r.message().lower()


def test_nudge_round_trip_returns_to_start():
    api = make()
    m = mover(api)
    start = m.read()
    report = m.nudge_and_verify("x", 30e-6)
    assert report.returned_ok
    assert m.read().x_m == pytest.approx(start.x_m, abs=1e-9)


def test_move_waits_for_arrival_not_just_idle():
    # The mock reports Busy during the move; the mover must wait for arrival.
    api = make()
    m = mover(api)
    t0 = api.clock.now()
    m.move_axis_relative("x", 10e-6)
    # virtual time advanced by at least the mock's stage-move duration
    assert api.clock.now() - t0 >= api._stage_move_dur


# --- GIS interlock ---------------------------------------------------------
def test_refuses_move_when_gis1_needle_inserted():
    api = make()
    api.set_gis_needle(1, NEEDLE_INSERTED)
    m = mover(api)
    with pytest.raises(StageSafetyError):
        m.move_axis_relative("x", 10e-6)


def test_refuses_move_when_gis2_needle_inserted():
    api = make()
    api.set_gis_needle(2, NEEDLE_INSERTED)
    m = mover(api)
    with pytest.raises(StageSafetyError):
        m.move_axis_relative("x", 10e-6)


def test_gis_interlock_can_be_disabled():
    api = make()
    api.set_gis_needle(1, NEEDLE_INSERTED)
    m = mover(api, require_gis_parked=False)
    # still moves (interlock off) -- for completeness; not recommended in practice
    res = m.move_axis_relative("x", 10e-6)
    assert res.measured_delta == pytest.approx(10e-6, abs=1e-9)


# --- other safety ----------------------------------------------------------
def test_refuses_move_when_stage_busy():
    api = make()
    api.set_stage_busy(True)
    m = mover(api)
    with pytest.raises(StageSafetyError):
        m.move_axis_relative("x", 10e-6)


def test_delta_cap_enforced():
    api = make()
    m = mover(api, max_delta_m=0.001)  # 1 mm cap
    with pytest.raises(StageSafetyError):
        m.move_axis_relative("x", 0.002)  # 2 mm -> refused before moving


def test_z_move_within_hard_limits_works():
    api = make()  # default stage Z = 30 mm, within [20, 40]
    m = mover(api)
    before = m.read()
    res = m.move_axis_relative("z", 50e-6)  # +50 um
    assert res.measured_delta == pytest.approx(50e-6, abs=1e-9)
    assert m.read().z_m == pytest.approx(before.z_m + 50e-6, abs=1e-9)


def test_z_move_blocked_above_hard_limit():
    api = make(stage_z_m=0.0399)  # 39.9 mm, just under the 40 mm hard ceiling
    api.clock.sleep(0)  # no-op; stage already settled
    m = mover(api)
    with pytest.raises(StageSafetyError) as exc:
        m.move_axis_relative("z", 500e-6)  # would reach 40.4 mm
    assert "hard Z" in str(exc.value)


def test_z_move_blocked_below_hard_limit():
    api = make(stage_z_m=0.0201)  # 20.1 mm, just above the 20 mm floor
    m = mover(api)
    with pytest.raises(StageSafetyError) as exc:
        m.move_axis_relative("z", -500e-6)  # would reach 19.6 mm
    assert "hard Z" in str(exc.value)


def test_custom_z_limits_config():
    api = make(stage_z_m=0.030)
    m = mover(api, z_min_m=0.028, z_max_m=0.032)
    # +3 mm would exceed the tightened 32 mm ceiling
    with pytest.raises(StageSafetyError):
        m.move_axis_relative("z", 0.003)


def test_limit_check_blocks_out_of_range_target():
    api = make()
    # X high limit is 0.05 in the mock; start near it and push past.
    api.move_stage(0.0499, 0.0, 0.030, 0.0, 0.0, 0.0)
    api.get_stage_position()  # let the mock settle
    # advance virtual clock so the setup move completes
    api.clock.sleep(api._stage_move_dur)
    m = mover(api)
    with pytest.raises(StageSafetyError):
        m.move_axis_relative("x", 500e-6)  # would exceed 0.05


def test_unknown_axis_rejected():
    api = make()
    m = mover(api)
    with pytest.raises(ValueError):
        m.move_axis_relative("q", 10e-6)


def test_confirm_decline_prevents_move():
    api = make()
    m = mover(api)
    before = m.read()
    with pytest.raises(StageMoveDeclined):
        m.move_axis_relative("x", 10e-6, confirm=lambda step: False)
    # position unchanged
    assert m.read().x_m == pytest.approx(before.x_m)


def test_confirm_accept_allows_move():
    api = make()
    m = mover(api)
    steps = []
    res = m.move_axis_relative("x", 10e-6, confirm=lambda s: steps.append(s) or True)
    assert res.measured_delta == pytest.approx(10e-6, abs=1e-9)
    assert steps and "move stage x" in steps[0]


# --- tilt/rotation use the degree cap/tol ----------------------------------
def test_tilt_nudge_uses_degree_units():
    api = make(stage_tilt_deg=30.0)
    m = mover(api)
    res = m.move_axis_relative("t", 0.5)  # 0.5 degrees
    assert res.measured_delta == pytest.approx(0.5, abs=1e-6)


def test_tilt_cap_enforced():
    api = make(stage_tilt_deg=30.0)
    m = mover(api, max_delta_deg=2.0)
    with pytest.raises(StageSafetyError):
        m.move_axis_relative("t", 5.0)  # exceeds degree cap
