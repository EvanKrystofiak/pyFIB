import json

import pytest

from gisdep.api.mock import MockSmartSEM
from gisdep.clock import FakeClock
from gisdep.gis.state import NEEDLE_PARKED, VALVE_CLOSED, NEEDLE_INSERTED
from gisdep.gis.deposition import deposit
from gisdep.runlog import RunLog


class _Abort:
    def __init__(self):
        self._set = False

    def set(self):
        self._set = True

    def is_set(self):
        return self._set


def make(tilt_deg=30.0):
    api = MockSmartSEM(clock=FakeClock(), stage_tilt_deg=tilt_deg)
    api.initialise()
    return api


# --- run logging -----------------------------------------------------------
def test_runlog_writes_json_and_csv(tmp_path):
    api = make()
    with RunLog("deposition", {"port": 1, "dwell": 2}, api=api, logdir=tmp_path) as rl:
        rl.set_result({"completed": True})
    assert rl.path is not None and rl.path.exists()
    rec = json.loads(rl.path.read_text())
    assert rec["operation"] == "deposition"
    assert rec["outcome"] == "completed"
    assert rec["params"] == {"port": 1, "dwell": 2}
    assert rec["result"]["completed"] is True
    # state snapshot captured WD + stage tilt + GIS state
    assert rec["state_start"]["wd_mm"] == pytest.approx(5.5, abs=1e-3)
    assert rec["state_start"]["stage"]["tilt_deg"] == pytest.approx(30.0, abs=1e-3)
    assert "gis" in rec["state_start"]
    assert (tmp_path / "runs.csv").exists()


def test_runlog_records_error_and_reraises(tmp_path):
    api = make()
    with pytest.raises(ValueError):
        with RunLog("deposition", {"port": 1}, api=api, logdir=tmp_path) as rl:
            raise ValueError("boom")
    rec = json.loads(rl.path.read_text())
    assert rec["outcome"] == "error"
    assert "boom" in rec["error"]


def test_runlog_classifies_abort(tmp_path):
    from gisdep.gis.actuate import GISAborted
    api = make()
    with pytest.raises(GISAborted):
        with RunLog("deposition", {}, api=api, logdir=tmp_path) as rl:
            raise GISAborted("stopped")
    rec = json.loads(rl.path.read_text())
    assert rec["outcome"] == "aborted"


def test_runlog_captures_log_lines(tmp_path):
    import logging
    api = make()
    with RunLog("test", {}, api=api, logdir=tmp_path) as rl:
        logging.getLogger("gisdep.demo").info("hello from the run")
    rec = json.loads(rl.path.read_text())
    assert any("hello from the run" in ln for ln in rec["log"])


# --- abort / safe teardown -------------------------------------------------
def test_deposition_abort_during_dwell_tears_down_safely():
    api = make()
    abort = _Abort()
    abort.set()  # request stop up front -> aborts at the dwell check
    from gisdep.gis.actuate import GISAborted
    with pytest.raises(GISAborted):
        deposit(api, 1, 5.0, clock=api.clock, abort=abort)
    # teardown ran: needle back to Parked, valve Closed despite the abort
    assert api.get_digital("DP_GIS_LOCATION") == NEEDLE_PARKED
    assert api.get_digital("DP_GIS_VALVE_STATE1") == VALVE_CLOSED


def test_deposition_no_abort_completes_normally():
    api = make()
    abort = _Abort()  # never set
    res = deposit(api, 1, 2.0, clock=api.clock, abort=abort)
    assert res.completed


def test_stage_mover_abort_raises():
    from gisdep.stage.mover import StageMover, StageAborted, StageMoveConfig
    api = make()
    m = StageMover(api, clock=api.clock,
                   cfg=StageMoveConfig(z_min_m=0.020, z_max_m=0.040))
    m.abort = _Abort()
    m.abort.set()
    with pytest.raises(StageAborted):
        m.move_axis_relative("z", 50e-6)
