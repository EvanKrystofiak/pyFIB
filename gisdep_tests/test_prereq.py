from gisdep.api import params as P
from gisdep.api.mock import MockSmartSEM
from gisdep.clock import FakeClock
from gisdep.prereq.checks import (
    run_prerequisites,
    check_stage_idle,
    check_chamber_confirmed,
    check_fib_gun_valve_closed,
)
from gisdep.prereq.tilt import FLAT_STAGE_DEV_TILT, CRYO_GIS_TILT


def make(stage_state=P.STAGE_IDLE, tilt_deg=30.0):
    # Default the mock to a cryo-valid tilt so prerequisite tests focused on other
    # checks are not incidentally blocked by the tilt gate.
    api = MockSmartSEM(clock=FakeClock(), stage_state=stage_state, stage_tilt_deg=tilt_deg)
    api.initialise()
    return api


def test_chamber_confirmed_true_false():
    assert check_chamber_confirmed(True).ok
    assert not check_chamber_confirmed(False).ok


def test_stage_idle_pass_and_fail():
    assert check_stage_idle(make(P.STAGE_IDLE)).ok
    assert not check_stage_idle(make(P.STAGE_BUSY)).ok


def test_fib_valve_stub_is_nonblocking_and_flagged():
    r = check_fib_gun_valve_closed(make())
    assert r.ok  # non-blocking stub
    assert "NOT IMPLEMENTED" in r.detail


def test_run_prerequisites_all_ok():
    report = run_prerequisites(make(), chamber_confirmed=True)
    assert report.ok
    assert len(report.failures) == 0


def test_run_prerequisites_blocks_on_busy_stage():
    report = run_prerequisites(make(P.STAGE_BUSY), chamber_confirmed=True)
    assert not report.ok
    assert any(f.name == "stage_idle" for f in report.failures)


def test_run_prerequisites_blocks_on_chamber_not_confirmed():
    report = run_prerequisites(make(), chamber_confirmed=False)
    assert not report.ok
    assert any(f.name == "chamber_scope_position" for f in report.failures)


def test_tilt_row_present_and_blocks_flat_stage_by_default():
    # Default tilt gate is cryo; a flat stage (tilt 0) must be blocked.
    api = MockSmartSEM(clock=FakeClock(), stage_tilt_deg=0.0)
    api.initialise()
    report = run_prerequisites(api, chamber_confirmed=True)
    names = [r.name for r in report.results]
    assert "stage_tilt" in names
    assert not report.ok
    assert any(f.name == "stage_tilt" for f in report.failures)


def test_flat_stage_dev_mode_allows_flat_stage():
    api = MockSmartSEM(clock=FakeClock(), stage_tilt_deg=0.0)
    api.initialise()
    report = run_prerequisites(
        api, chamber_confirmed=True, tilt_cfg=FLAT_STAGE_DEV_TILT
    )
    assert report.ok


def test_cryo_mode_allows_tilted_stage():
    report = run_prerequisites(make(tilt_deg=30.0), chamber_confirmed=True,
                               tilt_cfg=CRYO_GIS_TILT)
    assert report.ok


def test_include_fib_valve_adds_row():
    report = run_prerequisites(make(), chamber_confirmed=True, include_fib_valve=True)
    names = [r.name for r in report.results]
    assert "fib_gun_valve_closed" in names
    assert report.ok  # stub is non-blocking


def test_summary_renders():
    report = run_prerequisites(make(P.STAGE_BUSY), chamber_confirmed=False)
    s = report.summary()
    assert "FAIL" in s
