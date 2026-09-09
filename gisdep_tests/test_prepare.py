from gisdep.api import params as P
from gisdep.api.mock import MockSmartSEM, MockAutoConfig
from gisdep.clock import FakeClock
from gisdep.focus.autofocus import AutoFocusConfig
from gisdep.orchestrate.prepare import prepare_for_deposition, PrepStatus
from gisdep.prereq.tilt import FLAT_STAGE_DEV_TILT, CRYO_GIS_TILT

def make(clock, wd_m=0.0055, stage_state=P.STAGE_IDLE, tilt_deg=0.0):
    # Default to the flat development stage (tilt 0); tests that want cryo pass 30.
    api = MockSmartSEM(
        clock=clock, wd_m=wd_m, stage_state=stage_state, stage_tilt_deg=tilt_deg,
        auto_config=MockAutoConfig(latency=0.3, active=1.0),
    )
    api.initialise()
    return api


def af_cfg():
    return AutoFocusConfig(
        sustained_idle_s=10.0, startup_timeout_s=30.0,
        overall_timeout_s=120.0, poll_interval_s=0.5,
    )


def test_ready_at_ideal_wd():
    clock = FakeClock()
    api = make(clock, wd_m=0.0055)  # flat dev stage
    res = prepare_for_deposition(
        api, chamber_confirmed=True, tilt_cfg=FLAT_STAGE_DEV_TILT,
        clock=clock, af_cfg=af_cfg(),
    )
    assert res.status is PrepStatus.READY
    assert res.clear_to_insert
    assert res.autofocus is not None
    assert api.executed == [P.CMD_AUTO_FOCUS_COARSE, P.CMD_AUTO_FOCUS_FINE]


def test_blocked_when_chamber_not_confirmed_skips_autofocus():
    clock = FakeClock()
    api = make(clock)
    res = prepare_for_deposition(
        api, chamber_confirmed=False, clock=clock, af_cfg=af_cfg()
    )
    assert res.status is PrepStatus.BLOCKED_PREREQ
    assert not res.clear_to_insert
    assert res.autofocus is None
    assert api.executed == []  # fail fast, before any auto routine


def test_blocked_when_stage_busy():
    clock = FakeClock()
    api = make(clock, stage_state=P.STAGE_BUSY)
    res = prepare_for_deposition(
        api, chamber_confirmed=True, clock=clock, af_cfg=af_cfg()
    )
    assert res.status is PrepStatus.BLOCKED_PREREQ
    assert api.executed == []


def test_blocked_wd_too_close():
    clock = FakeClock()
    api = make(clock, wd_m=0.0050)  # 5.0 mm, below floor; flat dev stage
    res = prepare_for_deposition(
        api, chamber_confirmed=True, tilt_cfg=FLAT_STAGE_DEV_TILT,
        clock=clock, af_cfg=af_cfg(),
    )
    assert res.status is PrepStatus.BLOCKED_WD
    assert not res.clear_to_insert
    # autofocus still ran (WD is measured after focus)
    assert res.autofocus is not None


def test_ready_with_warning_when_wd_high():
    clock = FakeClock()
    api = make(clock, wd_m=0.0065)  # 6.5 mm: safe, suboptimal; flat dev stage
    res = prepare_for_deposition(
        api, chamber_confirmed=True, tilt_cfg=FLAT_STAGE_DEV_TILT,
        clock=clock, af_cfg=af_cfg(),
    )
    assert res.status is PrepStatus.READY_WITH_WARNING
    assert res.clear_to_insert


def test_default_cryo_tilt_blocks_flat_stage_before_autofocus():
    # No tilt_cfg -> strict cryo default -> flat dev stage (tilt 0) is blocked, and we
    # fail fast before running any auto routine.
    clock = FakeClock()
    api = make(clock, wd_m=0.0055, tilt_deg=0.0)
    res = prepare_for_deposition(
        api, chamber_confirmed=True, clock=clock, af_cfg=af_cfg()
    )
    assert res.status is PrepStatus.BLOCKED_PREREQ
    assert res.autofocus is None
    assert api.executed == []
    assert any(f.name == "stage_tilt" for f in res.prereqs.failures)


def test_cryo_mode_ready_on_tilted_stage():
    clock = FakeClock()
    api = make(clock, wd_m=0.0055, tilt_deg=30.0)  # real cryo geometry
    res = prepare_for_deposition(
        api, chamber_confirmed=True, tilt_cfg=CRYO_GIS_TILT,
        clock=clock, af_cfg=af_cfg(),
    )
    assert res.status is PrepStatus.READY
    assert res.clear_to_insert


def test_prepare_never_touches_gis():
    # There is no GIS backend argument at all in v0.1.0 -- prepare cannot actuate.
    import inspect
    sig = inspect.signature(prepare_for_deposition)
    assert "backend" not in sig.parameters
    assert "gis" not in sig.parameters


def test_no_autofocus_is_read_only_and_gates_current_wd():
    # do_coarse=do_fine=False -> no autofocus command is executed; the WD gate uses the
    # CURRENT working distance. Fully read-only.
    clock = FakeClock()
    api = make(clock, wd_m=0.0055, tilt_deg=30.0)
    res = prepare_for_deposition(
        api, chamber_confirmed=True, tilt_cfg=CRYO_GIS_TILT,
        do_coarse=False, do_fine=False, clock=clock, af_cfg=af_cfg(),
    )
    assert res.status is PrepStatus.READY
    assert res.autofocus is None          # autofocus was skipped
    assert api.executed == []             # nothing actuated


def test_no_autofocus_still_blocks_bad_wd():
    clock = FakeClock()
    api = make(clock, wd_m=0.0050, tilt_deg=30.0)  # 5.0 mm, below floor
    res = prepare_for_deposition(
        api, chamber_confirmed=True, tilt_cfg=CRYO_GIS_TILT,
        do_coarse=False, do_fine=False, clock=clock,
    )
    assert res.status is PrepStatus.BLOCKED_WD
    assert api.executed == []
