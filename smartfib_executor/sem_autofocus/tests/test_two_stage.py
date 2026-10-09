"""Two-stage SEM autofocus (v0.48.0): coarse at 500x, fine at the working mag.

Design numbers come from the measured curves of 2026-10-05 (two_stage.py docstring).
Runs the real adapter + search against SimulatedSem under a virtual clock.
"""
from __future__ import annotations

import io
import json
import os
import tempfile
from contextlib import redirect_stdout

from .. import guards as G
from .. import two_stage as TS
from ..__main__ import main as cli_main
from ..scope import FakeClock
from ..sim import SimulatedSem

WD0 = 5.000e-3


def _run(sim, **kw):
    logs = []
    kw.setdefault("fine_mag", 2000.0)
    res = TS.sem_autofocus_two_stage(sim, clock=FakeClock(), logger=logs.append, **kw)
    return res, logs


def _clean(sim, *, mag=1000.0, rate=7):
    assert sim.mag == mag, sim.mag                         # operator's mag back
    assert sim.scan_rate == rate, sim.scan_rate             # operator's scan speed back
    assert sim.dp["DP_IMAGE_MODE"] == "Full"                # raster back
    assert sim.moves == []                                  # stage never moved
    assert {m for m, _v in sim.set_log} <= G.ALLOWED_SETS
    assert all(c in G.ALLOWED_CMDS for c in sim.cmd_log)
    assert all(G.WD_MIN_M <= float(v) <= G.WD_MAX_M for m, v in sim.set_log if m == "AP_WD")


def _sim(true_wd, wd=WD0, **kw):
    return SimulatedSem(true_wd_m=true_wd, wd_m=wd, mag=1000.0, scan_rate=7,
                        blur_per_um=0.02, **kw)


def test_converges_coarse_then_fine_and_restores_mag_speed_box():
    sim = _sim(5.30e-3)
    res, logs = _run(sim)
    assert res.success, res.message
    assert abs(res.wd_best_m - 5.30e-3) < 5e-6, res.wd_best_m          # fine: within 5 um
    assert abs(res.coarse_best_m - 5.30e-3) < 100e-6                   # coarse: inside +/-90
    assert abs(sim.wd_m - res.wd_best_m) < 1e-12 and res.coarse_frames == 7
    assert res.fine_frames == 9 and res.n_frames == 7 + 9 + 1           # + verify
    mags = [float(v) for m, v in sim.set_log if m == "AP_MAG"]
    assert mags[0] == 500.0 and 2000.0 in mags and mags[-1] == 1000.0   # coarse, fine, back
    assert "CMD_SCANRATE4" in sim.cmd_log
    _clean(sim)
    assert any("coarse result" in l for l in logs)


def test_extends_the_coarse_range_when_focus_is_further_away():
    sim = _sim(6.15e-3)                                     # 1.15 mm above the start
    res, logs = _run(sim)
    assert res.success, res.message
    assert res.coarse_extensions >= 1 and abs(res.wd_best_m - 6.15e-3) < 5e-6
    assert any("extending" in l for l in logs)
    _clean(sim)
    sim = _sim(4.10e-3, wd=4.9e-3)            # 100 um above the 4 mm limit: the limit is tried
    res, _ = _run(sim)
    assert res.success and abs(res.wd_best_m - 4.10e-3) < 5e-6, res.message
    assert any(abs(w - G.WD_MIN_M) < 1e-12 for w, _ in res.coarse_curve)


def test_refuses_to_chase_focus_past_the_window_and_puts_everything_back():
    sim = _sim(3.6e-3, wd=4.5e-3)                           # true focus below 4 mm
    res, _ = _run(sim)
    assert not res.success and "window limit" in res.message, res.message
    assert res.restored and abs(sim.wd_m - 4.5e-3) < 1e-12
    _clean(sim)


def test_flat_curve_and_faults_restore_the_start():
    for kw, want in (({"featureless": True}, "flat"),
                     ({"contrast_zero_after": 9}, ""),
                     ({"stale_after": 4}, "identical"),
                     ({"z_follows_wd": True}, "stage Z")):
        sim = _sim(5.3e-3, **kw)
        res, _ = _run(sim)
        assert not res.success and want in res.message, (kw, res.message)
        assert abs(sim.wd_m - WD0) < 1e-12 or res.restore_failed, (kw, sim.wd_m)
        assert sim.mag == 1000.0 and sim.scan_rate == 7 and sim.dp["DP_IMAGE_MODE"] == "Full"
    sim = _sim(5.3e-3, wd_readback_offset_m=8e-6)           # set lands but reads back wrong
    res, _ = _run(sim)
    assert not res.success and res.restore_failed            # restore was attempted


def test_a_stage_that_fails_while_preparing_is_still_put_back():
    class _MagFails(SimulatedSem):
        def set_value(self, m, v):
            if m == "AP_MAG" and float(v) == 2000.0:        # fine stage: mag won't take
                self.set_log.append((m, v))
                self.mag = 1900.0
                return
            super().set_value(m, v)
    sim = _MagFails(true_wd_m=5.3e-3, wd_m=WD0, mag=1000.0, scan_rate=7, blur_per_um=0.02)
    res, _ = _run(sim)
    assert not res.success and "magnification" in res.message, res.message
    assert abs(sim.wd_m - WD0) < 1e-12 and res.restored
    assert sim.mag == 1000.0 and sim.scan_rate == 7 and sim.dp["DP_IMAGE_MODE"] == "Full"


def test_dry_run_changes_nothing_and_refusals():
    sim = _sim(5.3e-3)
    res, logs = _run(sim, plan_only=True)
    assert res.planned and sim.set_log == [] and sim.cmd_log == [] and sim.n_grabs == 0
    assert any("coarse: 500x, scan speed 4, 7 WDs" in l for l in logs), logs
    assert any("estimated ~" in l for l in logs)
    sim = _sim(5.3e-3, auto_video="BC")
    res, _ = _run(sim)
    assert res.refused and sim.set_log == [] and sim.n_grabs == 0


def test_cli_default_is_two_stage_and_single_stage_still_works():
    d = tempfile.mkdtemp()
    with redirect_stdout(io.StringIO()) as out:
        rc = cli_main(["--simulate", "--out", d, "--sim-true-wd-mm", "5.25"])
    assert rc == 0, out.getvalue()
    data = json.load(open(os.path.join(d, "results.json")))
    assert data["success"] and data["coarse_mag"] == 500.0 and data["coarse_curve"]
    assert os.path.exists(os.path.join(d, "coarse_curve.csv"))
    assert any(f.startswith("af2_") and "_coarse_" in f for f in os.listdir(d))
    assert "coarse (500x)" in out.getvalue()
    with redirect_stdout(io.StringIO()):
        assert cli_main(["--simulate", "--single-stage"]) == 0
        assert cli_main(["--simulate", "--sim-fault", "contrast0"]) == 1
        assert cli_main(["--simulate", "--coarse-speed", "20"]) == 2


def test_replay_on_the_measured_curves_lands_on_the_measured_focus():
    """Instrument data (2026-10-05): the real two-stage logic, with sharpness taken from the
    measured curves (coarse 500x / fine 2000x, speed 4) at whatever WD the stage sets.
    Measured focus: 6.942-6.945 mm (all seven passes). Several starting offsets, including
    one that needs the coarse range extended."""
    import numpy as np
    path = os.path.join(os.path.dirname(__file__), "data", "af_curve_20261005.json")
    fx = json.load(open(path))
    m0 = fx["wd_start_mm"] * 1e-3
    cx, cy = np.array(fx["curves"]["coarse_500x_s4"]).T
    fxx, fyy = np.array(fx["curves"]["fine_2000x_s4"]).T
    focus = m0 - 47.5e-6                                     # centre of 6.942-6.945 mm
    real = TS._metrics

    for start_um in (0.0, 250.0, -400.0, 700.0):
        sim = _sim(5.0e-3, wd=m0 + start_um * 1e-6)

        class _Replay:                                       # stands in for autofocus.metrics
            METRICS = real.METRICS
            DEFAULT_METRIC = real.DEFAULT_METRIC
            DEFAULT_PRESMOOTH_FRAC = real.DEFAULT_PRESMOOTH_FRAC

            @staticmethod
            def sharpness(img, metric, **kw):
                off = (sim.wd_m - m0) * 1e6
                if sim.mag < 1000:
                    return float(np.interp(off, cx, cy))
                return float(np.interp(off, fxx, fyy))
        TS._metrics = _Replay
        try:
            res, _ = _run(sim)
        finally:
            TS._metrics = real
        assert res.success, (start_um, res.message)
        assert abs(res.coarse_best_m - focus) < 60e-6, (start_um, res.coarse_best_m)
        assert abs(res.wd_best_m - focus) < 6e-6, (start_um, (res.wd_best_m - focus) * 1e6)
        if start_um == 700.0:
            assert res.coarse_extensions >= 1


class _RestartsScan(SimulatedSem):
    """Like the instrument (Evan 2026-10-06): changing mag, scan speed or the raster mode
    restarts live scanning, so a freeze sent BEFORE those changes does not hold."""
    def set_value(self, m, v):
        super().set_value(m, v)
        if m == "AP_MAG":
            self.frozen = False

    def execute(self, c):
        super().execute(c)
        if c.startswith("CMD_SCANRATE") or c == "CMD_MODE_NORMAL":
            self.frozen = False


def _rs(true_wd=5.3e-3, **kw):
    return _RestartsScan(true_wd_m=true_wd, wd_m=WD0, mag=1000.0, scan_rate=7,
                         blur_per_um=0.02, **kw)


def test_v049_straight_to_the_fine_mag_and_scan_left_paused():
    sim = _rs()
    res, logs = _run(sim)
    assert res.success, res.message
    mags = [float(v) for m, v in sim.set_log if m == "AP_MAG"]
    assert mags == [500.0, 2000.0, 1000.0], mags              # no detour back to 1000x
    assert sim.frozen, "scan left rastering"                 # paused at hand-back
    assert any("scan paused" in l for l in logs)
    _clean(sim)
    sim = _rs(contrast_zero_after=9)                          # failure path: still paused
    res, _ = _run(sim)
    assert not res.success and sim.frozen and sim.mag == 1000.0
    # the scope's own pause, and that the old order (freeze before the mag restore) fails
    from ..scope import SemFocusScope
    for pause, want in ((True, True), (False, False)):
        sim = _rs()
        sc = SemFocusScope(sim, magnification=500.0, scan_rate=4, clock=FakeClock(),
                           pause_at_end=pause)
        sc.prepare(); sc.restore()
        assert sim.frozen is want and sim.mag == 1000.0 and sim.scan_rate == 7, pause


def test_v049_fine_stage_contrast_floor_is_0p2_coarse_keeps_0p3():
    """Replay the measured curves with the fine curve flattened so its +/-90 um window has
    contrast ~0.25 (between the two floors): accepted at 0.2, rejected at 0.3."""
    import numpy as np
    fx = json.load(open(os.path.join(os.path.dirname(__file__), "data",
                                     "af_curve_20261005.json")))
    m0 = fx["wd_start_mm"] * 1e-3
    cx, cy = np.array(fx["curves"]["coarse_500x_s4"]).T
    fxx, fyy = np.array(fx["curves"]["fine_2000x_s4"]).T
    fyy = fyy.max() - (fyy.max() - fyy) * 0.55            # same peak, flatter curve
    real = TS._metrics
    out = {}
    for floor in (TS.FINE_MIN_CURVE_CONTRAST, 0.3):
        sim = _sim(5.0e-3, wd=m0)

        class _Replay:
            METRICS = real.METRICS
            DEFAULT_METRIC = real.DEFAULT_METRIC
            DEFAULT_PRESMOOTH_FRAC = real.DEFAULT_PRESMOOTH_FRAC

            @staticmethod
            def sharpness(img, metric, **kw):
                off = (sim.wd_m - m0) * 1e6
                return float(np.interp(off, cx, cy) if sim.mag < 1000 else np.interp(off, fxx, fyy))
        TS._metrics = _Replay
        try:
            out[floor], _ = _run(sim, fine_min_curve_contrast=floor)
        finally:
            TS._metrics = real
    ok, strict = out[TS.FINE_MIN_CURVE_CONTRAST], out[0.3]
    assert TS.FINE_MIN_CURVE_CONTRAST == 0.2
    assert ok.success and 0.2 <= ok.curve_contrast < 0.3, (ok.message, ok.curve_contrast)
    assert abs(ok.wd_best_m - (m0 - 47.5e-6)) < 6e-6
    assert not strict.success and "fine focus curve is flat" in strict.message


def test_v049_cli_logs_the_plan_once():
    with redirect_stdout(io.StringIO()) as out:
        assert cli_main(["--simulate", "--sim-true-wd-mm", "5.2"]) == 0
    txt = out.getvalue()
    assert txt.count("[sem-af2] start: WD") == 1 and txt.count("[sem-af2] coarse: 500x") == 1


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
