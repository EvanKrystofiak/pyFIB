"""Focus-curve measurement (v0.47.0): runs on the simulator, measures only.

Evan 2026-10-05: coarse at low mag / fast scan, fine at 2000x -- measure the curves first,
and record every frame so the run can be reviewed.
"""
from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from contextlib import redirect_stdout

from .. import guards as G
from ..curve import (CurvePass, curve_stats, default_passes, estimate_frame_s, main,
                     measure_curves, plan_wds)
from ..scope import FakeClock
from ..sim import SimulatedSem

WD0, TRUE = 5.000e-3, 5.030e-3


def _small_passes(frac=0.5):
    return [CurvePass("coarse_250x_s4", 250.0, 4, 300e-6, 100e-6, frac),
            CurvePass("fine_2000x_s5", 2000.0, 5, 60e-6, 15e-6, frac)]


def test_plan_and_stats():
    p = plan_wds(5.0e-3, 1e-3, 100e-6, (G.WD_MIN_M, G.WD_MAX_M))
    assert len(p) == 21 and abs(p[0] - 4.0e-3) < 1e-12 and abs(p[-1] - 6.0e-3) < 1e-12
    q = plan_wds(4.3e-3, 1e-3, 100e-6, (G.WD_MIN_M, G.WD_MAX_M))   # clamped, never below 4 mm
    assert min(q) >= G.WD_MIN_M and len(q) == 14
    s = curve_stats([1e-3, 2e-3, 3e-3, 4e-3, 5e-3], [1, 2, 10, 4, 1], 3e-3)
    assert s["peak_offset_um"] == 0.0 and s["contrast"] == 0.9 and not s["edge_peak"]
    assert s["half_band_um"] == 500.0                      # only the peak point is >= half
    assert abs(estimate_frame_s(20.9, 8, 4, 0.5) - 20.9 / 16 * 5.5 / 20.9) < 1e-9
    names = [p.label for p in default_passes()]
    assert names == ["coarse_250x_s4", "coarse_250x_s5", "coarse_500x_s4", "coarse_500x_s5",
                     "fine_2000x_s4", "fine_2000x_s5", "fine_2000x_s6"]


def test_measures_saves_everything_and_puts_it_all_back():
    sim = SimulatedSem(true_wd_m=TRUE, wd_m=WD0, mag=1000.0, scan_rate=7, blur_per_um=0.02)
    d = tempfile.mkdtemp()
    res = measure_curves(sim, _small_passes(), d, clock=FakeClock(), logger=lambda m: None)
    assert res.stopped is None and not res.restore_failed, res.stopped
    # nothing applied: WD, mag, scan speed and raster are back; the stage never moved
    assert abs(sim.wd_m - WD0) < 1e-12 and sim.mag == 1000.0 and sim.scan_rate == 7
    assert sim.dp["DP_IMAGE_MODE"] == "Full" and sim.moves == []
    assert {m for m, _v in sim.set_log} <= G.ALLOWED_SETS
    assert all(c in G.ALLOWED_CMDS for c in sim.cmd_log)
    assert all(G.WD_MIN_M <= float(v) <= G.WD_MAX_M for m, v in sim.set_log if m == "AP_WD")
    rows = list(csv.DictReader(open(res.csv_path)))
    # per pass: settle + sweep + return
    assert len(rows) == (1 + 7 + 1) + (1 + 9 + 1), len(rows)
    for r in rows:
        assert os.path.exists(os.path.join(d, r["file"])), r["file"]
    fine = res.passes[1]
    assert fine.stats["tenengrad"]["peak_offset_um"] == 30.0          # the simulator's focus
    assert fine.stats["tenengrad"]["return_ratio"] is not None
    assert os.path.exists(res.plot_path) and all(os.path.exists(p.contact_sheet)
                                                 for p in res.passes)
    summ = json.load(open(os.path.join(d, "summary.json")))
    assert summ["wd_start_m"] == WD0 and len(summ["passes"]) == 2


def test_a_guard_stops_the_run_and_the_wd_goes_back():
    sim = SimulatedSem(true_wd_m=TRUE, wd_m=WD0, contrast_zero_after=4, blur_per_um=0.02)
    d = tempfile.mkdtemp()
    res = measure_curves(sim, _small_passes(), d, clock=FakeClock(), logger=lambda m: None)
    assert res.stopped and "AP_CONTRAST" in res.stopped and len(res.passes) == 1
    assert abs(sim.wd_m - WD0) < 1e-12 and not res.restore_failed
    assert len(list(csv.DictReader(open(res.csv_path)))) >= 3          # partial data kept
    sim = SimulatedSem(true_wd_m=TRUE, wd_m=WD0, stale_after=3)
    res = measure_curves(sim, _small_passes(), tempfile.mkdtemp(), clock=FakeClock(),
                         logger=lambda m: None)
    assert res.stopped and "identical" in res.stopped and abs(sim.wd_m - WD0) < 1e-12
    sim = SimulatedSem(true_wd_m=TRUE, wd_m=WD0, z_follows_wd=True)
    res = measure_curves(sim, _small_passes(), tempfile.mkdtemp(), clock=FakeClock(),
                         logger=lambda m: None)
    assert res.stopped and "stage Z" in res.stopped


def test_dry_run_changes_nothing_and_cli_simulate_runs():
    out = io.StringIO()
    import smartfib_executor.sem_autofocus.curve as C
    sim = SimulatedSem(true_wd_m=TRUE, wd_m=WD0)

    class _S:                              # stand in for SmartSEM()
        def __new__(cls):
            sim.connect_remoting = lambda: None
            sim.close = lambda: None
            return sim
    import smartfib_executor.smartsem_com as SC
    old = SC.SmartSEM
    SC.SmartSEM = _S
    try:
        with redirect_stdout(out):
            rc = main([])
    finally:
        SC.SmartSEM = old
    assert rc == 0 and "Add --arm to measure" in out.getvalue(), out.getvalue()
    assert sim.set_log == [] and sim.cmd_log == [] and sim.n_grabs == 0
    assert "total:" in out.getvalue() and "coarse_250x_s4" in out.getvalue()
    d = tempfile.mkdtemp()
    with redirect_stdout(io.StringIO()) as o2:
        rc = main(["--simulate", "--out", d, "--low-mags", "250", "--low-speeds", "4",
                   "--low-half-range-um", "300", "--fine-speeds", "5",
                   "--fine-half-range-um", "60"])
    assert rc == 0, o2.getvalue()
    assert os.path.exists(os.path.join(d, "curves.png"))
    with redirect_stdout(io.StringIO()) as o3:
        assert main(["--low-half-range-um", "1500"]) == 2              # > 1 mm refused


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
