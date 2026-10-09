"""Tests for the SEM autofocus fork (no hardware).

    python -m smartfib_executor.sem_autofocus.tests

The REAL adapter (SemFocusScope) and search run against SimulatedSem, a fake SmartSEM
with fault knobs, under a virtual clock.
"""
from __future__ import annotations

import json
import os
import tempfile

import numpy as np

from .. import guards as G
from ..guards import GuardedSem, SemAutofocusSafetyError, frame_sanity
from ..scope import SemFocusScope, FakeClock
from ..search import sem_autofocus
from ..sim import SimulatedSem
from ..__main__ import main as cli_main

WD0 = 5.000e-3
TRUE = 5.030e-3


def _run(sim=None, scope_kw=None, **kw):
    sim = sim or SimulatedSem(true_wd_m=TRUE, wd_m=WD0)
    logs = []
    sc = SemFocusScope(sim, clock=FakeClock(), logger=logs.append, **(scope_kw or {}))
    res = sem_autofocus(sc, logger=logs.append, **kw)
    return sim, res, logs


def _wd_sets(sim):
    return [float(v) for m, v in sim.set_log if m == "AP_WD"]


def _nothing_changed(sim):
    assert sim.set_log == [], sim.set_log
    assert sim.cmd_log == [], sim.cmd_log
    assert sim.n_grabs == 0


# ----------------------------------------------------------------- happy path
def test_converges_and_touches_only_wd():
    sim, r, _ = _run()
    assert r.success, r.message
    assert abs(r.wd_best_m - TRUE) < 3e-6, r.wd_best_m
    assert abs(sim.wd_m - r.wd_best_m) < 1e-12        # left AT the new focus
    assert r.bracketed and r.refined and r.verify_ratio >= 0.6
    assert {m for m, _ in sim.set_log} <= {"AP_WD", "DP_FREEZE_ON"} | set(G.RASTER_MNS), \
        sim.set_log
    assert not any("AUTO_FOCUS" in c for c in sim.cmd_log)
    assert sim.moves == [] and sim.contrast == 32.0 and sim.brightness == 50.0
    assert sim.imaging == "SEM" and sim.frozen            # left in SEM, frozen


def test_start_on_the_other_side():
    sim, r, _ = _run(SimulatedSem(true_wd_m=4.95e-3, wd_m=5.02e-3), half_range_m=100e-6,
                     coarse_points=21)
    assert r.success and abs(r.wd_best_m - 4.95e-3) < 3e-6, (r.message, r.wd_best_m)


def test_dry_run_changes_nothing():
    sim = SimulatedSem()
    _, r, _ = _run(sim, plan_only=True)
    assert r.planned and not r.refused and not r.success
    assert len(r.coarse_plan_m) == G.COARSE_POINTS_DEFAULT
    _nothing_changed(sim)


def test_every_wd_set_stays_inside_the_window():
    for start, true in ((4.01e-3, 4.2e-3), (7.99e-3, 7.8e-3), (4.05e-3, 3.9e-3)):
        sim, r, _ = _run(SimulatedSem(true_wd_m=true, wd_m=start),
                         half_range_m=300e-6, coarse_points=9)
        for v in _wd_sets(sim):
            assert G.WD_MIN_M <= v <= G.WD_MAX_M, (start, v)


def test_max_frames_respected():
    _, r, _ = _run(max_frames=12)
    assert r.n_frames <= 12, r.n_frames


def test_mag_and_scan_rate_set_then_restored():
    sim = SimulatedSem()
    seen = {}
    orig = sim.grab_bmp

    def spy(path, **k):
        seen["mag"], seen["rate"] = sim.mag, sim.scan_rate
        return orig(path, **k)
    sim.grab_bmp = spy
    _, r, _ = _run(sim, scope_kw={"magnification": 3000.0, "scan_rate": 7})
    assert r.success, r.message
    assert seen == {"mag": 3000.0, "rate": 7}
    assert sim.mag == 2000.0 and sim.scan_rate == 5
    assert "CMD_SCANRATE7" in sim.cmd_log and sim.cmd_log[-1] != "CMD_SCANRATE7"


def test_frames_saved():
    d = tempfile.mkdtemp()
    _, r, _ = _run(save_dir=d)
    assert r.success and len(r.frames) == r.n_frames
    assert all(os.path.exists(p) for p in r.frames)
    assert any("verify" in os.path.basename(p) for p in r.frames)


# ----------------------------------------------------------------- refusals
def test_refuses_wd_outside_window():
    for wd in (9.5e-3, 3.5e-3, float("nan")):
        sim = SimulatedSem(wd_m=wd)
        _, r, _ = _run(sim)
        assert r.refused and ("window" in r.message or "AP_WD" in r.message), r.message
        _nothing_changed(sim)


def test_refuses_contrast_zero_at_start():
    sim = SimulatedSem(contrast_pct=0.0)
    _, r, _ = _run(sim)
    assert r.refused and "contrast 0" in r.message, r.message
    _nothing_changed(sim)


def test_refuses_auto_brightness_contrast():
    sim = SimulatedSem(auto_video="BC")
    _, r, _ = _run(sim)
    assert r.refused and "DP_AUTO_VIDEO" in r.message
    _nothing_changed(sim)


def test_refuses_beam_off_and_milling():
    for kw, word in (({"eht_v": 0.0}, "OFF"), ({"imaging": "Milling"}, "milling"),
                     ({"imaging": "Mill + SEM"}, "milling")):
        sim = SimulatedSem(**kw)
        _, r, _ = _run(sim)
        assert r.refused and word in r.message, r.message
        _nothing_changed(sim)


def test_refuses_when_a_guard_cannot_be_read():
    for mn in ("DP_FIB_IMAGING", "AP_CONTRAST", "AP_BRIGHTNESS", "AP_STAGE_AT_Z", "AP_WD",
               "AP_ACTUALKV"):
        sim = SimulatedSem(unreadable=(mn,))
        _, r, _ = _run(sim)
        assert r.refused, (mn, r.message)
        _nothing_changed(sim)


def test_refuses_bad_sweep_settings():
    for kw in ({"half_range_m": 2e-3}, {"half_range_m": 0.0}, {"coarse_points": 2},
               {"coarse_points": 9, "max_frames": 9}):
        sim = SimulatedSem()
        _, r, _ = _run(sim, **kw)
        assert r.refused, (kw, r.message)
        _nothing_changed(sim)


# ----------------------------------------------------------------- mid-run guards
def _assert_restored(sim, r, why):
    assert not r.success and why in r.message, r.message
    assert r.restored and abs(sim.wd_m - WD0) < 1e-12, (r.restored, sim.wd_m)


def test_contrast_drops_to_zero_mid_run():
    sim = SimulatedSem(contrast_zero_after=3)
    _, r, _ = _run(sim)
    _assert_restored(sim, r, "blank frame")


def test_contrast_changed_mid_run_is_caught_even_if_image_ok():
    class _Nudge(SimulatedSem):
        def grab_bmp(self, path, **k):
            if self.n_grabs == 2:
                self.contrast = 33.0
            return super().grab_bmp(path, **k)
    sim = _Nudge()
    _, r, _ = _run(sim)
    _assert_restored(sim, r, "AP_CONTRAST changed")


def test_mag_changed_mid_run():
    class _Mag(SimulatedSem):
        def grab_bmp(self, path, **k):
            if self.n_grabs == 4:
                self.mag = 1000.0
            return super().grab_bmp(path, **k)
    sim = _Mag()
    _, r, _ = _run(sim)
    _assert_restored(sim, r, "AP_MAG changed")


def test_stale_framestore_aborts():
    sim = SimulatedSem(stale_after=2)
    _, r, _ = _run(sim)
    _assert_restored(sim, r, "identical to the previous")


def test_frame_never_frozen_aborts():
    sim = SimulatedSem(never_frozen=True)
    _, r, _ = _run(sim)
    _assert_restored(sim, r, "DP_FROZEN")


def test_readback_mismatch_caught_by_the_probe():
    sim = SimulatedSem(wd_readback_offset_m=8e-6)
    _, r, logs = _run(sim)
    assert not r.success and "read back" in r.message, r.message
    assert sim.n_grabs == 0                                   # caught before any image
    first = _wd_sets(sim)[0]
    assert abs(first - (WD0 + 8e-6)) <= G.PROBE_STEP_M + 1e-12  # the first move was tiny
    assert r.restore_failed                                   # cannot CONFIRM the restore
    assert any("CHECK SEM FOCUS BY HAND" in l for l in logs)


def test_stage_following_wd_caught_by_the_probe():
    sim = SimulatedSem(z_follows_wd=True)
    z0 = sim.stage_z_m
    _, r, _ = _run(sim)
    _assert_restored(sim, r, "stage Z changed")
    assert sim.n_grabs == 0 and abs(sim.stage_z_m - z0) < 1e-12
    assert abs(_wd_sets(sim)[0] - WD0) <= G.PROBE_STEP_M + 1e-12   # caught on the tiny probe


def test_imaging_switch_timeout_touches_nothing_else():
    sim = SimulatedSem(mode_switch_works=False)
    _, r, _ = _run(sim)
    assert not r.success and "'SEM'" in r.message, r.message
    assert _wd_sets(sim) == [] and sim.n_grabs == 0 and not r.restored


# ----------------------------------------------------------------- acceptance guards
def test_edge_peak_is_not_extrapolated():
    sim = SimulatedSem(true_wd_m=5.3e-3)
    _, r, _ = _run(sim)
    _assert_restored(sim, r, "sweep edge")
    assert not r.bracketed and "wider" in r.message


def test_edge_at_window_limit_says_so():
    sim = SimulatedSem(true_wd_m=8.2e-3, wd_m=7.95e-3)
    _, r, _ = _run(sim)
    assert not r.success and "window limit" in r.message, r.message
    assert abs(sim.wd_m - 7.95e-3) < 1e-12
    assert max(_wd_sets(sim)) <= G.WD_MAX_M


def test_flat_curve_rejected():
    sim = SimulatedSem(featureless=True)
    _, r, _ = _run(sim)
    _assert_restored(sim, r, "flat")


def test_focus_that_does_not_reproduce_is_rejected():
    class _Drift(SimulatedSem):
        def grab_bmp(self, path, **k):
            if self.n_grabs == 13:              # probe has no image; 9 coarse + 4 fine
                self.true_wd_m += 80e-6
            return super().grab_bmp(path, **k)
    sim = _Drift()
    _, r, _ = _run(sim)
    _assert_restored(sim, r, "did not reproduce")
    assert r.verify_ratio is not None and r.verify_ratio < 0.6


def test_com_error_mid_run_restores_and_shows_the_error():
    class _Boom(SimulatedSem):
        def grab_bmp(self, path, **k):
            if self.n_grabs == 3:
                raise OSError("(-2147352567, 'Exception occurred.', API_E_GRAB_FAIL)")
            return super().grab_bmp(path, **k)
    sim = _Boom()
    _, r, _ = _run(sim, scope_kw={"magnification": 3000.0})
    _assert_restored(sim, r, "API_E_GRAB_FAIL")
    assert sim.mag == 2000.0                    # restore() still ran


def test_ctrl_c_still_restores_wd():
    class _Int(SimulatedSem):
        def grab_bmp(self, path, **k):
            if self.n_grabs == 2:
                raise KeyboardInterrupt
            return super().grab_bmp(path, **k)
    sim = _Int()
    try:
        _run(sim)
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("KeyboardInterrupt swallowed")
    assert abs(sim.wd_m - WD0) < 1e-12


# ----------------------------------------------------------------- structural safety
def test_guard_blocks_everything_but_wd_mag_and_imaging():
    sim = SimulatedSem()
    g = GuardedSem(sim)
    for fn in (lambda: g.execute("CMD_AUTO_FOCUS_COARSE"),
               lambda: g.execute("CMD_AUTO_FOCUS_FINE"),
               lambda: g.execute("CMD_FIB_MODE_FIB"),
               lambda: g.set_value("AP_CONTRAST", 30.0),
               lambda: g.set_value("AP_BRIGHTNESS", 50.0),
               lambda: g.set_value("AP_WD", 8.5e-3),
               lambda: g.set_value("AP_WD", 3.9e-3),
               lambda: g.set_value("AP_WD", float("nan")),
               lambda: g.set_value("AP_MAG", 0.0),
               lambda: g.move_stage(0, 0, 0, 0, 0, 0)):
        try:
            fn()
        except SemAutofocusSafetyError:
            continue
        raise AssertionError("guard let a forbidden call through")
    assert sim.contrast == 32.0 and sim.moves == [] and sim.set_log == []
    g.set_value("AP_WD", 5.1e-3)
    g.execute("CMD_SCANRATE5")
    assert sim.wd_m == 5.1e-3


def test_frame_sanity():
    rng = np.random.default_rng(1)
    ok = np.clip(100 + 40 * rng.standard_normal((200, 300)), 0, 255)
    assert frame_sanity(ok) is None
    blank = np.full((200, 300), 90.0)
    blank[181, :] = 255.0                       # a databar must not rescue a blank frame
    blank[183:, :] = 20.0
    blank[185:190, 10:80] = 230.0
    assert "blank" in frame_sanity(blank)
    assert "black" in frame_sanity(np.clip(rng.normal(1, 3, (200, 300)), 0, 255))
    assert "saturated" in frame_sanity(np.clip(rng.normal(254, 3, (200, 300)), 0, 255))


# ----------------------------------------------------------------- CLI
def test_cli_simulate_and_faults():
    d = tempfile.mkdtemp()
    assert cli_main(["--simulate", "--out", d]) == 0
    data = json.load(open(os.path.join(d, "results.json")))
    assert data["success"] and data["simulated"]
    assert os.path.exists(os.path.join(d, "curve.csv"))
    assert cli_main(["--simulate", "--sim-fault", "outside"]) == 2
    assert cli_main(["--simulate", "--sim-fault", "contrast0"]) == 1
    assert cli_main(["--simulate", "--single-stage", "--half-range-um", "5000"]) == 2
    assert cli_main(["--simulate", "--coarse-half-range-um", "5000"]) == 2    # two-stage
    assert cli_main(["--simulate", "--sim-fault", "readback"]) == 3   # restore unconfirmed


# ----------------------------------------------------------------- review fixes (v0.37.0)
def test_window_can_be_narrowed_but_never_widened():
    sim = SimulatedSem(wd_m=9.5e-3, true_wd_m=9.6e-3)
    _, r, _ = _run(sim, scope_kw={"window": (1e-3, 20e-3)})
    assert r.refused, r.message
    _nothing_changed(sim)
    g = GuardedSem(SimulatedSem(), wd_window=(1e-3, 20e-3))
    try:
        g.set_value("AP_WD", 9.6e-3)
    except SemAutofocusSafetyError:
        pass
    else:
        raise AssertionError("widened window accepted")
    sc = SemFocusScope(SimulatedSem(), window=(4.5e-3, 5.5e-3), clock=FakeClock())
    assert sc.window == (4.5e-3, 5.5e-3)
    for bad in ((6e-3, 5e-3), (9e-3, 10e-3)):
        try:
            GuardedSem(SimulatedSem(), wd_window=bad)
        except SemAutofocusSafetyError:
            continue
        raise AssertionError(f"bad window {bad} accepted")


def test_only_known_imaging_states_may_start():
    for mode in ("", "Drift SEM", "Ext", "None", "2", "Deposition"):
        sim = SimulatedSem(imaging=mode)
        _, r, _ = _run(sim)
        assert r.refused and "DP_FIB_IMAGING" in r.message, (mode, r.message)
        _nothing_changed(sim)
    for mode in ("SEM", "FIB", "SEM + FIB"):
        _, r, _ = _run(SimulatedSem(imaging=mode), plan_only=True)
        assert r.planned, (mode, r.message)


def test_fib_busy_refused():
    for fm in ("Milling", "Deposition", "To Milling", "To Imaging"):
        sim = SimulatedSem(fib_mode=fm)
        _, r, _ = _run(sim)
        assert r.refused and "DP_FIB_MODE" in r.message, (fm, r.message)
        _nothing_changed(sim)


def test_scan_settings_that_spoil_frames_refused():
    for kw, mn in (({"noise_reduction": "Frame Avg"}, "DP_NOISE_REDUCTION"),
                   ({"noise_reduction": "Continuous Avg."}, "DP_NOISE_REDUCTION"),
                   ({"noise_reduction": "Frame Int. Busy"}, "DP_NOISE_REDUCTION"),
                   ({"noise_reduction": "Line Int. Done"}, "DP_NOISE_REDUCTION"),
                   ({"spot": "On"}, "DP_SPOT"), ({"line_scan": "On"}, "DP_LINE_SCAN"),
                   ({"operating_mode": "Split"}, "DP_OPERATING_MODE"),
                   ({"image_mode": "Reduced"}, "DP_IMAGE_MODE")):
        sim = SimulatedSem(**kw)
        _, r, _ = _run(sim)
        assert r.refused and mn in r.message, (kw, r.message)
        _nothing_changed(sim)
    _, r, _ = _run(SimulatedSem(noise_reduction="Pixel Avg."), plan_only=True)
    assert r.planned, r.message


def test_unreadable_secondary_modes_warn_but_do_not_refuse():
    sim = SimulatedSem(unreadable=("DP_NOISE_REDUCTION", "DP_SPOT", "DP_AUTO_VIDEO",
                                   "DP_FIB_MODE"))
    _, r, _ = _run(sim)
    assert r.success, r.message
    assert len(r.warnings) == 4 and any("DP_SPOT" in w for w in r.warnings), r.warnings


def test_failed_unfreeze_aborts_before_any_score():
    sim = SimulatedSem(unfreeze_fails=True)
    _, r, _ = _run(sim)
    _assert_restored(sim, r, "CMD_UNFREEZE_ZONE failed")   # the command error itself is shown
    assert r.curve == [] and sim.n_grabs == 0


def test_scan_that_never_goes_live_aborts():
    sim = SimulatedSem(stuck_frozen=True)
    _, r, _ = _run(sim)
    _assert_restored(sim, r, "did not go Live")
    assert sim.n_grabs == 0


def test_frozen_state_unreadable_aborts():
    sim = SimulatedSem(unreadable=("DP_FROZEN",))
    _, r, _ = _run(sim)
    _assert_restored(sim, r, "DP_FROZEN is unreadable")
    assert sim.n_grabs == 0


def test_frozen_numeric_fallback_works():
    class _NumOnly(SimulatedSem):
        def get_str(self, m):
            if m == "DP_FROZEN":
                raise RuntimeError("no string")
            return super().get_str(m)
    _, r, _ = _run(_NumOnly())
    assert r.success, r.message


def test_mag_not_changed_if_it_cannot_be_restored():
    sim = SimulatedSem()
    real = sim.get_float

    def gf(m):
        if m == "AP_MAG" and not any(k == "DP_FREEZE_ON" for k, _ in sim.set_log):
            return real(m)                      # readable in preflight ...
        if m == "AP_MAG":
            raise RuntimeError("sim: AP_MAG unreadable")   # ... not once prepare starts
        return real(m)
    sim.get_float = gf
    _, r, _ = _run(sim, scope_kw={"magnification": 3000.0})
    assert not r.success and "AP_MAG" in r.message, r.message
    assert not any(m == "AP_MAG" for m, _ in sim.set_log) and sim.mag == 2000.0


def test_scan_rate_not_changed_if_it_cannot_be_restored():
    sim = SimulatedSem(scan_rate=18)
    _, r, _ = _run(sim, scope_kw={"scan_rate": 7})
    assert not r.success and "could not be restored" in r.message, r.message
    assert not any(c.startswith("CMD_SCANRATE") for c in sim.cmd_log) and sim.scan_rate == 18


def test_wd_changed_by_the_mode_switch_aborts():
    class _Jump(SimulatedSem):
        def execute(self, c):
            super().execute(c)
            if c == "CMD_FIB_MODE_SEM":
                self.wd_m = 6.0e-3
    sim = _Jump()
    _, r, _ = _run(sim)
    assert not r.success and "after switching to SEM" in r.message, r.message
    assert _wd_sets(sim) == [] and sim.n_grabs == 0


def test_parameter_limits():
    for kw in ({"fine_fraction": 5.0}, {"fine_fraction": 0.0}, {"probe_step_m": 1e-3},
               {"probe_step_m": -1e-6}):
        try:
            _run(**kw)
        except ValueError:
            continue
        raise AssertionError(f"{kw} accepted")


def test_saved_frames_are_copies_of_fresh_grabs():
    """Grab() always writes an absolute temp file (the proven path); saved frames are
    copies of it, each one different (fresh)."""
    class _Rec(SimulatedSem):
        def __init__(self, **k):
            super().__init__(**k); self.grab_paths = []
        def grab_bmp(self, path, **k):
            self.grab_paths.append(str(path)); return super().grab_bmp(path, **k)
    d = tempfile.mkdtemp()
    sim, r, _ = _run(_Rec(), save_dir=d)
    assert r.success and len(r.frames) == r.n_frames == len(sim.grab_paths)
    assert all(os.path.isabs(p) and not p.startswith(d) for p in sim.grab_paths)
    blobs = [open(p, "rb").read() for p in r.frames]
    assert len(set(blobs)) == len(blobs)
    assert len(r.frame_timing_s) == r.n_frames

# ----------------------------------------------------------------- reduced raster (v0.37.1)
BOX = (256, 192, 512, 384)          # default: centred half-width x half-height box


def test_reduced_raster_used_then_restored():
    sim = SimulatedSem()
    orig = dict(sim.raster)
    seen = []
    real = sim.grab_bmp

    def spy(path, **k):
        seen.append(sim.dp["DP_IMAGE_MODE"]); return real(path, **k)
    sim.grab_bmp = spy
    _, r, _ = _run(sim)
    assert r.success and r.raster_box == BOX, (r.message, r.raster_box)
    assert set(seen) == {"Reduced"}                        # every frame in the box
    assert set(sim.grab_rects) == {BOX}                    # grab only the refreshed box
    assert sim.dp["DP_IMAGE_MODE"] == "Full" and sim.raster == orig
    assert sim.cmd_log.index("CMD_MODE_REDUCED") < sim.cmd_log.index("CMD_UNFREEZE_ZONE")
    assert abs(r.wd_best_m - TRUE) < 3e-6


def test_reduced_raster_restored_on_every_exit():
    for sim in (SimulatedSem(contrast_zero_after=3), SimulatedSem(true_wd_m=5.3e-3)):
        orig = dict(sim.raster)
        _, r, _ = _run(sim)
        assert not r.success and sim.dp["DP_IMAGE_MODE"] == "Full" and sim.raster == orig

    class _Int(SimulatedSem):
        def grab_bmp(self, path, **k):
            if self.n_grabs == 2:
                raise KeyboardInterrupt
            return super().grab_bmp(path, **k)
    sim = _Int()
    try:
        _run(sim)
    except KeyboardInterrupt:
        pass
    assert sim.dp["DP_IMAGE_MODE"] == "Full" and abs(sim.wd_m - WD0) < 1e-12


def test_reduced_raster_is_faster():
    def run_time(frac):
        clk = FakeClock()
        sc = SemFocusScope(SimulatedSem(), clock=clk, raster_frac=frac)
        r = sem_autofocus(sc)
        assert r.success, r.message
        return clk.t
    full, box = run_time(None), run_time(0.5)
    assert box < 0.6 * full, (box, full)


def test_store_not_on_screen_grid_uses_full_frame():
    sim = SimulatedSem(store=(2048, 1536))
    _, r, _ = _run(sim)
    assert r.success and r.raster_box is None, r.message
    assert "CMD_MODE_REDUCED" not in sim.cmd_log
    assert any("reduced raster not used" in w for w in r.warnings)
    assert set(sim.grab_rects) == {(0, 0, 2048, 1536)}


def test_dp_image_mode_full_in_sem_mode_is_a_note_not_a_warning():
    # MEASURED 2026-10-01: SmartSEM reads DP_IMAGE_MODE 'Full' in SEM imaging even with
    # the reduced raster on (frame times proved the box). Log note only.
    sim = SimulatedSem(reduced_works=False)
    _, r, logs = _run(sim)
    assert r.success, r.message
    assert not any("DP_IMAGE_MODE" in w for w in r.warnings), r.warnings
    assert any("DP_IMAGE_MODE reads" in l for l in logs)


def test_full_frame_option():
    sim = SimulatedSem()
    _, r, _ = _run(sim, scope_kw={"raster_frac": None})
    assert r.success and r.raster_box is None and "CMD_MODE_REDUCED" not in sim.cmd_log


def test_raster_limits():
    for frac in (0.05, 0.0, -1.0):
        try:
            SemFocusScope(SimulatedSem(), raster_frac=frac)
        except ValueError:
            continue
        raise AssertionError(f"raster_frac {frac} accepted")
    g = GuardedSem(SimulatedSem())
    for mn, v in (("AP_RED_RASTER_W", 2000.0), ("AP_RED_RASTER_POSN_Y", -1.0),
                  ("AP_RED_RASTER_H", float("nan"))):
        try:
            g.set_value(mn, v)
        except SemAutofocusSafetyError:
            continue
        raise AssertionError(f"{mn}={v} accepted")
    sc = SemFocusScope(SimulatedSem(), clock=FakeClock())
    sc.prepare()
    assert sc.box == BOX and abs(sc.presmooth_scale - 2.0) < 1e-12 and not sc.frame_has_databar
    sc.restore()


def test_featureless_never_passes_with_any_box():
    for frac in (1.0 / 3.0, 0.5, None):
        for seed in range(4):
            sim = SimulatedSem(featureless=True, seed=seed)
            _, r, _ = _run(sim, scope_kw={"raster_frac": frac})
            assert not r.success, (frac, seed, r.curve_contrast)
            assert abs(sim.wd_m - WD0) < 1e-12



# ----------------------------------------------------------------- v0.39.0 (instrument data)
def test_instrument_state_names_of_2026_10_01_pass_preflight():
    # Exactly what the Crossbeam 550 reported on 2026-10-01 (results.json state_before).
    sim = SimulatedSem(imaging="SEM", auto_video="Off", noise_reduction="Pixel Avg.",
                       fib_mode="Imaging", operating_mode="Normal", image_mode="Full",
                       spot="Off", line_scan="Off", store=(1024, 768), scan_rate=7,
                       frame_time_ms=5268.36474609375, contrast_pct=63.0044,
                       brightness_pct=49.9012, mag=3656.33)
    _, r, _ = _run(sim, plan_only=True)
    assert r.planned and not r.refused and r.warnings == [], (r.message, r.warnings)
    _, r, _ = _run(SimulatedSem(imaging="SEM", noise_reduction="Pixel Avg."))
    assert r.success, r.message


def test_defaults_scan_speed_8_and_10um_steps():
    assert G.SCAN_RATE_DEFAULT == 8 and G.COARSE_POINTS_DEFAULT == 13
    assert abs(G.HALF_RANGE_DEFAULT_M - 60e-6) < 1e-15
    seen = []
    sim = SimulatedSem(scan_rate=5)
    real = sim.grab_bmp
    def spy(path, **k):
        seen.append(sim.scan_rate); return real(path, **k)
    sim.grab_bmp = spy
    _, r, _ = _run(sim)
    assert r.success and set(seen) == {8} and sim.scan_rate == 5     # used 8, restored 5
    steps = sorted(set(round(b - a, 9) for a, b in zip(r.coarse_plan_m, r.coarse_plan_m[1:])))
    assert steps == [10e-6], steps


def test_fine_points_fall_between_coarse_points():
    sim, r, _ = _run()
    coarse = r.coarse_plan_m
    fine = [w for w, _ in r.curve if all(abs(w - c) > 0.1e-6 for c in coarse)]
    assert len(fine) == 2, fine                       # +/- half a step around the best
    for w in fine:
        d = min(abs(w - c) for c in coarse)
        assert abs(d - 5e-6) < 1e-9, d                # exactly mid-way
    assert r.n_frames == len(coarse) + 2 + 1          # + verification frame


def test_estimate_scales_with_scan_speed():
    from ..search import _estimate_s
    sc = SemFocusScope(SimulatedSem(), clock=FakeClock())
    st = {"frame_time_s": 1.3, "scan_rate": 5.0}
    t8 = _estimate_s(sc, 10, st)
    sc.scan_rate = 5
    t5 = _estimate_s(sc, 10, st)
    assert t8 > 2.5 * t5, (t5, t8)


def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t(); print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1; print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1; print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
