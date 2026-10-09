"""Tests for the refine stage-sign calibration probe."""
from __future__ import annotations

from ..sources import SimulatedRefineScope
from ..calibrate_stage import calibrate_stage, REFINE_STAGE_MAX_M
from .. import geometry as g


class _FlippedStageSim(SimulatedRefineScope):
    """A stage whose image response is the OPPOSITE handedness of the default sim."""
    def move_stage_xy(self, x_m, y_m):
        dxm = x_m - self._stage[0]; dym = y_m - self._stage[1]
        self._stage = [float(x_m), float(y_m)]
        self.true_offset[1] -= dxm / self.px      # flipped
        self.true_offset[0] -= dym / self.px


class _SloppyStageSim(SimulatedRefineScope):
    """Mechanical error: the stage only achieves 85% of the commanded step."""
    def move_stage_xy(self, x_m, y_m):
        tx = self._stage[0] + 0.85 * (x_m - self._stage[0])
        ty = self._stage[1] + 0.85 * (y_m - self._stage[1])
        super().move_stage_xy(tx, ty)


def test_probe_recovers_default_convention_and_restores_stage():
    s = SimulatedRefineScope(magnification=2000.0, noise_sigma=4.0)
    start = s.get_stage_xy()
    cal = calibrate_stage(s, 2000.0, test_step_m=20e-6)
    assert cal.ok and cal.correction_signs() == (g.STAGE_SIGN_X, g.STAGE_SIGN_Y)
    assert s.get_stage_xy() == start


def test_probe_measures_a_flipped_stage_not_assumes():
    s = _FlippedStageSim(magnification=2000.0, noise_sigma=4.0)
    cal = calibrate_stage(s, 2000.0, test_step_m=20e-6)
    assert cal.ok and cal.correction_signs() == (+1.0, +1.0)   # opposite of placeholder


def test_probe_uses_achieved_step_under_mechanical_error():
    s = _SloppyStageSim(magnification=2000.0, noise_sigma=4.0)
    cal = calibrate_stage(s, 2000.0, test_step_m=20e-6)
    ax = cal.axes["x"]
    assert ax.ok
    assert abs(ax.achieved_step_m - 17e-6) < 0.5e-6          # measured what happened
    # The sim foreshortens by STAGE_SCALE_X (0.90). Computing from the ACHIEVED step
    # recovers exactly that; using the commanded 20 um would give 0.90*0.85 = 0.77.
    assert abs(ax.response_raw - g.STAGE_SCALE_X) < 0.05
    assert abs(ax.measured_pixel_size_m * g.STAGE_SCALE_X / cal.nominal_pixel_size_m - 1.0) < 0.1


def test_probe_refuses_step_above_refine_cap_without_moving():
    class _Rec(SimulatedRefineScope):
        def __init__(self, **k): super().__init__(**k); self.moves = 0
        def move_stage_xy(self, x, y): self.moves += 1; super().move_stage_xy(x, y)
    s = _Rec(magnification=2000.0, noise_sigma=4.0)
    cal = calibrate_stage(s, 2000.0, test_step_m=REFINE_STAGE_MAX_M * 1.5, axes=("x",))
    assert not cal.axes["x"].ok and "cap" in cal.axes["x"].note
    # only the restore-to-start moves (no test step was commanded)
    assert cal.correction_signs() is None


def test_probe_restores_stage_even_if_grab_fails():
    class _Bad(SimulatedRefineScope):
        def __init__(self, **k): super().__init__(**k); self.n = 0
        def grab_fib_image(self, mag):
            self.n += 1
            if self.n == 2: raise RuntimeError("grab failed")
            return super().grab_fib_image(mag)
    s = _Bad(magnification=2000.0, noise_sigma=4.0); start = s.get_stage_xy()
    try:
        calibrate_stage(s, 2000.0, test_step_m=20e-6)
    except RuntimeError:
        pass
    assert s.get_stage_xy() == start        # restored by the finally


# ---- the REAL adapter's move_stage_xy (never exercised before the probe) ---- #
def test_real_adapter_move_stage_xy_holds_tilt_rot_masterz():
    """REGRESSION: move_stage_xy used p.r_deg / p.m_m (wrong names -> AttributeError)
    and getattr(p, 't_deg', 0.0) which silently DEFAULTED TILT TO 0. On a tilted stage
    that would have commanded tilt=0. It must move X/Y only and hold the rest from
    the real read-back."""
    from ..sources import ScopeRefineAdapter
    from ...smartsem_com import StagePosition

    class _Sem:
        def __init__(self):
            self.pos = StagePosition(48.9877e-3, 48.9570e-3, 38.988e-3, 30.0, 316.43, 5.052e-3)
            self.moves = []
        def get_stage_position(self): return self.pos
        def move_stage(self, x, y, z, t, r, m):
            self.moves.append((x, y, z, t, r, m))
            self.pos = StagePosition(x, y, z, t, r, m)
        def wait_stage_arrival(self, **k): pass
        def get_float(self, m): return 0.0
        def get_str(self, m): return ""
        def set_value(self, m, v): pass
        def execute(self, c): pass

    sem = _Sem()
    ad = ScopeRefineAdapter(sem, settle_s=0.0, frame_wait_s=0.0, imaging_scan_rate=None,
                            dry_run=False, logger=lambda m: None)
    ad.move_stage_xy(48.9877e-3 + 20e-6, 48.9570e-3)
    x, y, z, t, r, m = sem.moves[-1]
    assert abs(x - (48.9877e-3 + 20e-6)) < 1e-12 and abs(y - 48.9570e-3) < 1e-12
    assert t == 30.0            # tilt HELD (the old code would have sent 0.0)
    assert r == 316.43          # rotation held
    assert abs(m - 5.052e-3) < 1e-12 and abs(z - 38.988e-3) < 1e-12


def test_probe_restore_failure_is_reported_not_silent():
    logs = []
    class _RestoreFails(SimulatedRefineScope):
        def __init__(self, **k): super().__init__(**k); self.n = 0
        def move_stage_xy(self, x, y):
            self.n += 1
            if self.n >= 2: raise RuntimeError("stage fault")   # step ok, restore fails
            super().move_stage_xy(x, y)
    s = _RestoreFails(magnification=2000.0, noise_sigma=4.0)
    try:
        calibrate_stage(s, 2000.0, test_step_m=20e-6, axes=("x",), logger=logs.append)
    except RuntimeError:
        pass
    assert any("RESTORE FAILED" in l and "MANUALLY" in l for l in logs)



# ---- LOCKED to the instrument calibrations of 2026-09-22 (2000x, 20 um, R 12) ---- #
def _fixture(tag):
    import json, os
    return json.load(open(os.path.join(os.path.dirname(__file__),
                                       f"refine_stage_calibration_2026-09-22_{tag}.json")))


def test_measured_signs_are_the_same_at_both_tilts():
    for tag in ("T30", "T02"):
        d = _fixture(tag)
        # X -1, Y +1 (the placeholder had Y -1 -- WRONG)
        assert d["correction_signs"] == [g.STAGE_SIGN_X, g.STAGE_SIGN_Y] == [-1.0, 1.0]
        assert abs(d["rotation_hint_deg"]) < 2.0
        for ax in ("x", "y"):
            a = d["axes"][ax]
            assert abs(a["achieved_step_m"] / a["commanded_step_m"] - 1.0) < 5e-4


def test_tilt_model_reproduces_both_calibrations():
    for tag, tilt in (("T30", 30.0), ("T02", 1.972)):
        d = _fixture(tag)
        sx, sy = g.stage_scale(tilt)
        assert abs(abs(d["axes"]["y"]["response_raw"]) / sy - 1.0) < 0.005   # Y within 0.5%
        assert abs(abs(d["axes"]["x"]["response_raw"]) / sx - 1.0) < 0.015   # X within 1.5%
    assert abs(g.STAGE_FIB_ANGLE_DEG - 54.0) < 0.5      # fitted angle = Crossbeam 54 deg


def test_stage_correction_requires_tilt_and_uses_the_model():
    try:
        g.stage_correction(10e-6, 0.0)
        raise AssertionError("tilt must be required")
    except TypeError:
        pass
    dx, _ = g.stage_correction(10e-6, 0.0, tilt_deg=2.0)
    assert dx < 0 and abs(abs(dx) - 10e-6 / g.STAGE_SCALE_X) < 1e-12
    _, dy2 = g.stage_correction(0.0, 10e-6, tilt_deg=2.0)
    _, dy30 = g.stage_correction(0.0, 10e-6, tilt_deg=30.0)
    assert dy2 > 0 and dy30 > 0                 # Y sign +1 (measured)
    assert dy2 > dy30 > 10e-6                   # more stage needed at low tilt


def test_model_guards_tilt_range_and_rotation():
    assert g.stage_model_applies(0.0, 12.0)[0] and g.stage_model_applies(30.0, 12.0)[0]
    assert not g.stage_model_applies(45.0, 12.0)[0]
    assert not g.stage_model_applies(2.0, 316.4)[0]
    assert g.stage_model_applies(2.0, 372.5)[0]      # wraps: 372.5 == 12.5


def test_12deg_run_validates_the_model_out_of_sample():
    """The model was fitted on the 2 and 30 deg runs ONLY; it predicted the 12 deg
    run (tilt 12.044, from the log) before it was measured. Y within 1%."""
    d = _fixture("T12")
    assert d["correction_signs"] == [-1.0, 1.0]
    _, sy = g.stage_scale(12.044)
    assert abs(abs(d["axes"]["y"]["response_raw"]) / sy - 1.0) < 0.01
    # X in that run (0.980) was contaminated by a ~1.2 um image offset after the
    # baseline (the Y step shows 25 px of X cross-talk vs ~5 px in the other runs),
    # so it is NOT used for STAGE_SCALE_X; removing the offset gives ~0.92.
    assert d["axes"]["y"]["crosstalk_px"] > 4 * _fixture("T02")["axes"]["y"]["crosstalk_px"]


class _JumpAfterBaseline(SimulatedRefineScope):
    """Reproduces the 12-deg run: a one-time +22 px image jump in X right after the
    first (baseline) frame."""
    def __init__(self, **k):
        super().__init__(**k); self._n = 0
    def grab_fib_image(self, mag):
        img = super().grab_fib_image(mag)
        self._n += 1
        if self._n == 1:
            self.true_offset[1] += 22.0
        return img


def test_probe_flags_drift_and_fresh_baseline_keeps_y_clean():
    logs = []
    s = _JumpAfterBaseline(magnification=2000.0, noise_sigma=4.0, stage_tilt_deg=12.0)
    cal = calibrate_stage(s, 2000.0, test_step_m=20e-6, logger=logs.append)
    ax, ay = cal.axes["x"], cal.axes["y"]
    assert ax.return_offset_m > 1.0e-6 and "contaminated" in ax.note      # X flagged
    assert any("QUALITY" in l and "X" in l for l in logs)
    _, sy = g.stage_scale(12.0)
    assert abs(ay.crosstalk_px) < 2.0                  # fresh baseline: no 22 px carry-over
    assert abs(abs(ay.response_raw) / sy - 1.0) < 0.02  # Y scale clean
    assert ay.return_offset_m < 0.5e-6 and "contaminated" not in ay.note
    assert cal.correction_signs() == (-1.0, 1.0)       # signs still right


def test_clean_run_reports_no_drift():
    s = SimulatedRefineScope(magnification=2000.0, noise_sigma=4.0)
    cal = calibrate_stage(s, 2000.0, test_step_m=20e-6)
    assert all(a.return_offset_m < 0.2e-6 and "contaminated" not in a.note
               for a in cal.axes.values())


def test_clean_12deg_repeat_with_drift_check():
    """Repeat at 12.04 deg with the v0.32.1 probe: return-to-start offsets 0.09 / 0.03 um
    (no drift), X 0.924 (confirms the earlier 0.980 was contaminated), Y within 0.5% of
    the model and within 0.5% of the first 12-deg run."""
    d = _fixture("T12_clean")
    assert abs(d["tilt_deg"] - 12.04) < 0.05 and abs(d["rot_deg"] - 12.0) < 0.01
    assert d["correction_signs"] == [-1.0, 1.0]
    sx, sy = g.stage_scale(d["tilt_deg"])
    assert abs(abs(d["axes"]["y"]["response_raw"]) / sy - 1.0) < 0.005
    assert abs(abs(d["axes"]["x"]["response_raw"]) / sx - 1.0) < 0.015
    y1 = abs(_fixture("T12")["axes"]["y"]["response_raw"])
    assert abs(abs(d["axes"]["y"]["response_raw"]) / y1 - 1.0) < 0.005   # repeatable
    for ax in ("x", "y"):
        assert d["axes"][ax]["return_offset_m"] < 0.2e-6                  # stage returns
        assert d["axes"][ax]["note"] == ""


def test_real_adapter_waits_for_arrival_to_1um_not_the_default_0p3mm():
    """REGRESSION: refine X/Y moves are <= 100 um; with wait_stage_arrival's default
    0.3 mm tolerance the start position already counted as 'arrived' (premature Idle
    -> next frame grabbed mid-travel)."""
    from ..sources import ScopeRefineAdapter
    from ...smartsem_com import StagePosition
    seen = {}
    class _Sem:
        pos = StagePosition(1e-3, 2e-3, 3e-2, 12.0, 12.0, 5e-3)
        def get_stage_position(self): return self.pos
        def move_stage(self, *a): pass
        def wait_stage_arrival(self, **k): seen.update(k)
    ad = ScopeRefineAdapter(_Sem(), logger=lambda m: None)
    ad.move_stage_xy(1e-3 + 14e-6, 2e-3)
    assert seen.get("tol_m") == 1.0e-6


def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
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
