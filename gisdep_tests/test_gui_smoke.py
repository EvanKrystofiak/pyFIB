"""Headless smoke test for the GUI. Skips cleanly if PyQt5/qtpy are not installed.

Covers window construction, a refresh, and that the three provided artworks are chosen
for the three defined GIS states. The slow threaded deposition is exercised manually,
not here, to keep the suite fast.
"""

import os

import pytest

pytest.importorskip("qtpy")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtWidgets import QApplication  # noqa: E402

from gisdep.gui.app import GisdepWindow  # noqa: E402
from gisdep.gui.state_images import NORMAL, GIS_INSERT, GIS_ON  # noqa: E402
from gisdep.gis import read_all  # noqa: E402
from gisdep.gui.state_images import active_port_state  # noqa: E402
from gisdep.gis.state import (  # noqa: E402
    NEEDLE_INSERTED,
    NEEDLE_PARKED,
    VALVE_OPEN,
    VALVE_CLOSED,
)


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


def _active_file(win):
    return active_port_state(read_all(win.api)).filename


def test_window_builds_and_refreshes(app):
    win = GisdepWindow()
    win._refresh()
    pm = win.image_label.pixmap()
    assert pm is not None and not pm.isNull()
    assert win.caption.text()


def test_three_states_select_three_artworks(app):
    win = GisdepWindow()
    assert _active_file(win) == NORMAL                       # ready
    win._simulate(1, "needle", NEEDLE_INSERTED)
    assert _active_file(win) == GIS_INSERT                   # inserted, closed
    win._simulate(1, "valve", VALVE_OPEN)
    assert _active_file(win) == GIS_ON                       # depositing
    win._simulate(1, "valve", VALVE_CLOSED)
    win._simulate(1, "needle", NEEDLE_PARKED)
    assert _active_file(win) == NORMAL                       # back to ready


def test_status_labels_populate(app):
    win = GisdepWindow()
    win._refresh()
    assert "Parked" in win._val["n1"].text()
    assert "mm" in win._val["z"].text()
    assert "mm" in win._val["wd"].text()


def test_reset_to_safe(app):
    win = GisdepWindow()
    win._simulate(1, "needle", NEEDLE_INSERTED)
    win._simulate(1, "valve", VALVE_OPEN)
    win._reset_safe()
    assert _active_file(win) == NORMAL


def test_workflow_controls_exist(app):
    win = GisdepWindow()
    # WD check + Z stage controls are present.
    assert win.wd_btn is not None and win.zplus_btn is not None and win.zminus_btn is not None
    assert win.zstep_spin.value() == 20
    assert "not checked" in win.wd_verdict.text()


def test_wd_check_runs_and_sets_verdict(app):
    import time
    win = GisdepWindow()
    win._check_wd()
    t0 = time.time()
    while win.wd_verdict.text() in ("not checked", "") and time.time() - t0 < 10:
        app.processEvents()
        time.sleep(0.03)
    # mock WD is 5.5 mm on a flat stage -> ideal
    assert "ideal" in win.wd_verdict.text()


def test_z_nudge_moves_stage(app):
    import time
    win = GisdepWindow()
    z0 = win.api.get_stage_position().z_m
    win._nudge_z(+1)  # +20 um default
    t0 = time.time()
    while not (win._worker is not None and win._worker.isFinished()) and time.time() - t0 < 10:
        app.processEvents()
        time.sleep(0.03)
    z1 = win.api.get_stage_position().z_m
    assert z1 > z0  # moved up in Z


def test_monitor_mode_disables_actuation(app):
    win = GisdepWindow(monitor=True)  # read-only monitor UI on the mock
    assert win._read_only
    # actuation controls disabled
    assert not win._sim_box.isEnabled()
    assert not win._depo_box.isEnabled()
    assert not win.zplus_btn.isEnabled()
    assert not win.zminus_btn.isEnabled()
    assert not win.gotowd_btn.isEnabled()
    assert not win.autofocus_check.isEnabled()
    # read-only WD check stays available
    assert win.wd_btn.isEnabled()


def test_go_to_wd_button_moves_toward_target(app):
    import time
    win = GisdepWindow()
    # nudge WD off target first, then use the button to correct one step
    win.api.set_gis_needle(1, NEEDLE_PARKED)  # ensure parked
    win.api._ap["AP_WD"] = 0.0057  # 5.7 mm
    win._go_to_wd()
    t0 = time.time()
    while not (win._worker is not None and win._worker.isFinished()) and time.time() - t0 < 10:
        app.processEvents()
        time.sleep(0.03)
    assert win.api.get_analogue("AP_WD") == pytest.approx(0.0055, abs=1e-6)


def test_monitor_mode_ignores_simulate(app):
    win = GisdepWindow(monitor=True)
    before = win.api.get_digital("DP_GIS_LOCATION")
    win._simulate(1, "needle", NEEDLE_INSERTED)  # must be a no-op in read-only mode
    assert win.api.get_digital("DP_GIS_LOCATION") == before


def test_default_mode_is_interactive(app):
    win = GisdepWindow()
    assert not win._read_only
    assert win._sim_box.isEnabled() and win._depo_box.isEnabled()


def test_cryo_buttons_present_and_move_stage(app):
    import time
    win = GisdepWindow()
    assert win.grid1_btn is not None and win.grid2_btn is not None
    before = win.api.get_stage_position()
    win._go_to_grid("GRID1")
    t0 = time.time()
    while not (win._worker is not None and win._worker.isFinished()) and time.time() - t0 < 20:
        app.processEvents()
        time.sleep(0.03)
    after = win.api.get_stage_position()
    # ended at the (placeholder) GRID1 tilt of 30 deg, and M is unchanged
    assert after.tilt_deg == pytest.approx(30.0, abs=1e-3)
    assert after.m_m == pytest.approx(before.m_m, abs=1e-9)


def test_cryo_disabled_in_monitor(app):
    win = GisdepWindow(monitor=True)
    assert not win._cryo_box.isEnabled()
    assert not win.grid1_btn.isEnabled()


def test_shows_start_image_on_load(app):
    win = GisdepWindow()
    assert win._display_state == "start"
    win._refresh()
    assert "ready to position" in win.caption.text().lower()


def test_start_clears_after_an_action(app):
    win = GisdepWindow()
    assert win._display_state == "start"
    win._simulate(1, "needle", NEEDLE_INSERTED)
    assert win._display_state is None


def test_blocked_image_when_insertion_refused(app):
    import time
    win = GisdepWindow()
    win.api.set_gis_needle(2, NEEDLE_INSERTED)     # other needle in -> insertion refused
    win.api._stage.tilt_deg = 30.0
    win.port_combo.setCurrentIndex(0)
    win._run_deposition()
    t0 = time.time()
    while not (win._worker is not None and win._worker.isFinished()) and time.time() - t0 < 10:
        app.processEvents()
        time.sleep(0.03)
    app.processEvents()
    assert win._display_state == "blocked"
    win._refresh()
    assert "not possible" in win.caption.text().lower()


def test_full_run_card_present_and_gated_by_arm(app):
    win = GisdepWindow(monitor=True)  # disarmed
    assert win.fr_btn is not None and win._full_box is not None
    assert not win._full_box.isEnabled()          # disabled when disarmed


def test_full_run_executes_end_to_end_on_mock(app):
    import time
    from gisdep.api.mock import MockAutoConfig
    win = GisdepWindow()                           # mock = armed
    win.api._stage_move_dur = 0.05                 # speed up the sim
    win.api.auto_cfg = MockAutoConfig(latency=0.1, active=0.7)
    win.api._ap["AP_WD"] = 0.0057                  # off-target so a Z step happens
    win._run_full()
    t0 = time.time()
    while not (win._worker is not None and win._worker.isFinished()) and time.time() - t0 < 60:
        app.processEvents()
        time.sleep(0.05)
    # full run completed and left a safe state
    assert win.api.get_digital("DP_GIS_LOCATION") == NEEDLE_PARKED
    assert win.api.get_digital("DP_GIS_VALVE_STATE1") == VALVE_CLOSED
    assert win.api.get_analogue("AP_WD") == pytest.approx(0.0055, abs=1e-6)
