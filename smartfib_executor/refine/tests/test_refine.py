"""Offline tests for the position-refinement fork.

No hardware, no COM: registration is pure numpy and the closed loop runs against a
simulator. Run:
    python -m smartfib_executor.refine.tests.test_refine
"""

from __future__ import annotations

import numpy as np

from .. import geometry as G
from ..registration import phase_cross_correlation, crop_databar, detect_databar_row
from ..refine import refine_position
from ..sources import SimulatedRefineScope, fourier_shift
from ..calibrate import (calibrate_beam_shift, BeamShiftCalibration,
                         load_calibrated_signs)


def _scene(shape=(256, 256), seed=3):
    rng = np.random.default_rng(seed)
    h, w = shape
    img = np.full((h, w), 60.0)
    for _ in range(70):
        cy, cx = rng.integers(8, h - 8), rng.integers(8, w - 8)
        r = int(rng.integers(3, 8))
        img[cy - r:cy + r, cx - r:cx + r] = float(rng.integers(120, 240))
    return img


# ------------------------- registration ------------------------- #
def test_registration_sign():
    """moving = roll(reference, (dy, dx)) -> returns (dy, dx). Regression-locked."""
    ref = np.random.default_rng(1).normal(0, 1, (256, 256))
    for sy, sx in [(7, 11), (-5, 9), (0, -13), (12, -4)]:
        mov = np.roll(ref, (sy, sx), axis=(0, 1))
        r = phase_cross_correlation(ref, mov, crop_bar=False, window=False,
                                    subpixel=False)
        assert abs(r.dy_px - sy) < 0.5 and abs(r.dx_px - sx) < 0.5, \
            (sy, sx, r.dy_px, r.dx_px)


def test_registration_subpixel():
    base = _scene()
    mov = fourier_shift(base, 3.4, -6.7)
    r = phase_cross_correlation(base, mov, crop_bar=False, window=True)
    assert abs(r.dy_px - 3.4) < 0.4, r.dy_px
    assert abs(r.dx_px + 6.7) < 0.4, r.dx_px


def test_registration_survives_noise():
    rng = np.random.default_rng(7)
    base = _scene()
    mov = fourier_shift(base, -5.0, 8.0) + rng.normal(0, 12.0, base.shape)
    r = phase_cross_correlation(base + rng.normal(0, 12.0, base.shape), mov,
                                crop_bar=False, window=True)
    assert abs(r.dy_px + 5.0) < 1.0 and abs(r.dx_px - 8.0) < 1.0, (r.dy_px, r.dx_px)


def test_databar_detected_and_cropped_before_registration():
    base = _scene((300, 400))
    # add a databar to BOTH; its static content must not pin the shift to zero.
    def bar(img):
        h, w = img.shape
        sep = np.full((1, w), 255.0)
        b = np.zeros((28, w)); b[3:9, 5:w // 3] = 255.0
        return np.vstack([img, sep, b])
    ref = bar(base)
    mov = bar(fourier_shift(base, 6.0, -9.0))
    assert detect_databar_row(ref) == 300
    r = phase_cross_correlation(ref, mov, crop_bar=True, window=True)
    assert abs(r.dy_px - 6.0) < 1.0 and abs(r.dx_px + 9.0) < 1.0, (r.dy_px, r.dx_px)


# ------------------------- geometry ------------------------- #
def test_pixel_size_matches_measured():
    assert abs(G.pixel_size_m(2000.0, 1024) - 55.82e-9) < 1.5e-9


def test_beam_shift_correction_sign():
    """MEASURED on this instrument (v0.7.2): R = +1, so BEAM_SIGN = -1.

    calibrate.py applied +2 um and the image moved RIGHT (X) / DOWN (Y), i.e.
    R = +1 on both axes. A drift of +dx is therefore nulled by -dx of beam shift.
    These are the instrument defaults; a saved calibration overrides them.
    """
    assert G.BEAM_SIGN_X == -1.0 and G.BEAM_SIGN_Y == -1.0
    cx, cy = G.beam_shift_correction(3.0e-6, -2.0e-6)
    assert cx == -3.0e-6 and cy == 2.0e-6


def test_thresholds():
    assert G.BEAM_SHIFT_THRESHOLD_M == 1.0e-5
    assert G.BEAM_SHIFT_MAX_M == 1.5e-5


# ------------------------- closed loop ------------------------- #
def test_beam_shift_refine_converges():
    for oy, ox in [(40.0, -70.0), (-30.0, 55.0), (10.0, 10.0)]:
        scope = SimulatedRefineScope(magnification=2000.0,
                                     true_offset_px=(oy, ox), seed=5)
        res = refine_position(scope, scope.reference_image(), 2000.0)
        assert res.success, res.message
        assert res.method == "beam_shift"
        assert res.residual_drift_m < 5e-7, res.residual_drift_m  # < 0.5 um


def test_already_aligned_needs_no_correction():
    scope = SimulatedRefineScope(magnification=2000.0, true_offset_px=(0.0, 0.0),
                                 noise_sigma=2.0, seed=1)
    res = refine_position(scope, scope.reference_image(), 2000.0)
    assert res.success and res.method == "none"
    assert res.applied_beam_shift_delta_m == (0.0, 0.0)


def test_wrong_sign_is_detected_and_undone():
    """A mis-signed correction must be caught and reverted, not left applied."""
    scope = SimulatedRefineScope(magnification=2000.0, true_offset_px=(0.0, 60.0),
                                 seed=6)
    res = refine_position(scope, scope.reference_image(), 2000.0,
                          beam_sign_x=+1.0)  # deliberately wrong (correct is -1)
    assert not res.success
    assert "sign" in res.message.lower()
    bx, by = scope.get_beam_shift()
    assert abs(bx) < 1e-9 and abs(by) < 1e-9, "beam shift left applied after undo"


def test_large_drift_routes_to_stage_and_stage_is_disabled_by_default():
    # ~15 um drift (> 10 um threshold). ox px = 15um / 55.8nm ~ 269 px.
    scope = SimulatedRefineScope(magnification=2000.0, true_offset_px=(0.0, 269.0),
                                 seed=2)
    res = refine_position(scope, scope.reference_image(), 2000.0)
    assert res.method == "stage"
    assert res.stage_required_but_disabled
    assert not res.success
    # Nothing was moved: beam and stage untouched.
    assert scope.get_beam_shift() == (0.0, 0.0)
    assert scope.get_stage_xy() == (0.0, 0.0)


def test_stage_path_runs_only_when_explicitly_enabled():
    scope = SimulatedRefineScope(magnification=2000.0, true_offset_px=(0.0, 269.0),
                                 seed=2)
    before = scope.get_stage_xy()
    res = refine_position(scope, scope.reference_image(), 2000.0, enable_stage=True)
    # The stage path executed (moved) this time, and converged in the sim.
    assert res.method == "stage"
    assert scope.get_stage_xy() != before, "stage should have moved when enabled"
    assert res.applied_stage_delta_m != (0.0, 0.0)
    # Beam shift must NOT have been used for the large drift.
    assert scope.get_beam_shift() == (0.0, 0.0)


def test_low_confidence_blocks_correction():
    """Uncorrelated reference vs image -> no correction."""
    scope = SimulatedRefineScope(magnification=2000.0, true_offset_px=(0.0, 50.0),
                                 seed=9)
    junk = np.random.default_rng(0).normal(128, 30, (768, 1024))
    res = refine_position(scope, junk, 2000.0, min_snr=50.0)
    assert res.low_confidence and not res.success
    assert scope.get_beam_shift() == (0.0, 0.0)


def test_frame_count_is_reasonable():
    """Dose sanity: one initial frame + one verify per correction iteration."""
    scope = SimulatedRefineScope(magnification=2000.0, true_offset_px=(20.0, -40.0),
                                 seed=4)
    res = refine_position(scope, scope.reference_image(), 2000.0)
    assert res.frames <= 3, res.frames  # initial + 1-2 verifies


# ------------------------- beam-shift calibration ------------------------- #
def test_calibration_recovers_sign_for_any_handedness():
    """The whole point: measure R instead of assuming it."""
    for rx, ry in [(-1.0, -1.0), (+1.0, +1.0), (-1.0, +1.0), (+1.0, -1.0)]:
        scope = SimulatedRefineScope(magnification=2000.0, beam_sign_x=rx,
                                     beam_sign_y=ry, noise_sigma=6.0, seed=11)
        cal = calibrate_beam_shift(scope, 2000.0, test_shift_m=2.0e-6)
        assert cal.axes["x"].ok and cal.axes["y"].ok, cal.summary()
        assert cal.axes["x"].response == rx, (rx, cal.axes["x"].response)
        assert cal.axes["y"].response == ry, (ry, cal.axes["y"].response)
        # correction sign is the negation of the response
        assert cal.beam_sign_x == -rx and cal.beam_sign_y == -ry


def test_calibration_measures_pixel_size():
    """Independently checks the magnification -> pixel-size calibration."""
    scope = SimulatedRefineScope(magnification=2000.0, noise_sigma=6.0, seed=11)
    cal = calibrate_beam_shift(scope, 2000.0, test_shift_m=2.0e-6)
    assert abs(cal.scale_error_pct) < 3.0, cal.scale_error_pct
    assert abs(cal.axes["x"].pixel_size_m - 55.82e-9) < 3e-9


def test_calibration_restores_beam_shift():
    scope = SimulatedRefineScope(magnification=2000.0, seed=11)
    scope.set_beam_shift(1.0e-6, -2.0e-6)
    before = scope.get_beam_shift()
    calibrate_beam_shift(scope, 2000.0, test_shift_m=2.0e-6)
    after = scope.get_beam_shift()
    assert abs(after[0] - before[0]) < 1e-12 and abs(after[1] - before[1]) < 1e-12


def test_calibration_reports_autofocus_raster_side():
    """One run resolves the convention for the autofocus fork too."""
    neg = calibrate_beam_shift(
        SimulatedRefineScope(magnification=2000.0, beam_sign_x=-1.0, seed=11),
        2000.0, test_shift_m=2.0e-6)
    pos = calibrate_beam_shift(
        SimulatedRefineScope(magnification=2000.0, beam_sign_x=+1.0, seed=11),
        2000.0, test_shift_m=2.0e-6)
    # R=-1 -> target appears LEFT -> raster RIGHT; R=+1 -> raster LEFT.
    assert neg.safe_raster_side_for_positive_x_park == "right"
    assert pos.safe_raster_side_for_positive_x_park == "left"


def test_calibration_refuses_to_exceed_beam_shift_limit():
    scope = SimulatedRefineScope(magnification=2000.0, seed=11)
    cal = calibrate_beam_shift(scope, 2000.0, test_shift_m=20.0e-6)  # > 15 um limit
    assert not cal.axes["x"].ok
    assert "limit" in cal.axes["x"].note
    assert scope.get_beam_shift() == (0.0, 0.0)


def test_calibration_flags_a_shift_that_did_not_take():
    """If the beam never moved, say so rather than inventing a sign."""
    class DeadBeam(SimulatedRefineScope):
        def set_beam_shift(self, x_m, y_m):
            pass  # command accepted, nothing happens

    cal = calibrate_beam_shift(DeadBeam(magnification=2000.0, seed=11), 2000.0,
                               test_shift_m=2.0e-6)
    assert not cal.axes["x"].ok
    assert "barely moved" in cal.axes["x"].note


def test_calibration_roundtrips_to_json(tmp_path=None):
    import tempfile, os
    scope = SimulatedRefineScope(magnification=2000.0, seed=11)
    cal = calibrate_beam_shift(scope, 2000.0, test_shift_m=2.0e-6)
    d = tempfile.mkdtemp()
    path = os.path.join(d, "refine_calibration.json")
    cal.save(path)
    back = BeamShiftCalibration.load(path)
    assert back.beam_sign_x == cal.beam_sign_x
    assert back.beam_sign_y == cal.beam_sign_y
    assert load_calibrated_signs(path) == (cal.beam_sign_x, cal.beam_sign_y)


def test_refine_converges_using_calibrated_signs():
    """End-to-end: calibrate, then use the measured signs to refine."""
    for rx, ry in [(-1.0, -1.0), (+1.0, +1.0)]:
        scope = SimulatedRefineScope(magnification=2000.0, beam_sign_x=rx,
                                     beam_sign_y=ry, true_offset_px=(30.0, -50.0),
                                     seed=12)
        cal = calibrate_beam_shift(scope, 2000.0, test_shift_m=2.0e-6)
        res = refine_position(scope, scope.reference_image(), 2000.0,
                              beam_sign_x=cal.beam_sign_x,
                              beam_sign_y=cal.beam_sign_y)
        assert res.success, (rx, ry, res.message)
        assert res.residual_drift_m < 5e-7


# --------------- instrument defaults pinned to the measurement --------------- #
def test_defaults_match_the_shipped_instrument_calibration():
    """The built-in signs MUST equal what was measured on this instrument.

    data/instrument_calibration_crossbeam550.json is the real calibrate.py output
    from the Crossbeam 550. If someone edits geometry.BEAM_SIGN_* without a new
    measurement, this fails.
    """
    from pathlib import Path as _P
    data = _P(__file__).resolve().parent.parent / "data" / \
        "instrument_calibration_crossbeam550.json"
    cal = BeamShiftCalibration.load(data)
    assert cal.beam_sign_x == G.BEAM_SIGN_X, (cal.beam_sign_x, G.BEAM_SIGN_X)
    assert cal.beam_sign_y == G.BEAM_SIGN_Y, (cal.beam_sign_y, G.BEAM_SIGN_Y)
    # And the measured scale must stay close to the nominal calibration.
    assert abs(cal.scale_error_pct) < 5.0, cal.scale_error_pct


def test_shipped_calibration_agrees_with_autofocus_raster_side():
    """The measurement also validates the autofocus fork's safe raster side."""
    from pathlib import Path as _P
    data = _P(__file__).resolve().parent.parent / "data" / \
        "instrument_calibration_crossbeam550.json"
    cal = BeamShiftCalibration.load(data)
    assert cal.safe_raster_side_for_positive_x_park == "left"


# ------------- live pixel size + scan-rotation guard (v0.9.0) ------------- #
def test_store_resolution_mismatch_does_not_shrink_corrections():
    """REGRESSION (v0.9.0): a store-tied live pixel size must NOT scale corrections.

    v0.9.0 drove the drift->metre conversion from AP_FIB_PIXEL_SIZE, which is tied to
    the STORE grid. When the store width differs from the grabbed width, every
    correction shrank by grab_w/store_w ("very small movements"). Here the scope
    reports a pixel size 3x too small (store 3072 vs grab 1024); the refinement must
    still converge because it derives pixel size from the correlated image width.
    """
    scope = SimulatedRefineScope(magnification=2000.0, true_offset_px=(0.0, 60.0),
                                 seed=5)
    # Simulate a wrong (store-tied) live pixel size, 1/3 of the true image px.
    scope.get_pixel_size_m = lambda: scope.px / 3.0
    res = refine_position(scope, scope.reference_image(), 2000.0)
    assert res.success, res.message
    assert res.residual_drift_m < 5e-7, res.residual_drift_m


def test_mismatched_pixel_size_is_flagged():
    """The live-vs-image cross-check should warn (it would have caught the bug)."""
    msgs = []
    scope = SimulatedRefineScope(magnification=2000.0, true_offset_px=(0.0, 60.0),
                                 seed=5)
    scope.get_pixel_size_m = lambda: scope.px / 3.0
    refine_position(scope, scope.reference_image(), 2000.0, logger=msgs.append)
    assert any("pixel size" in m.lower() for m in msgs), msgs


def test_corrections_scaled_from_image_width_not_store():
    """Convergence must not depend on the live pixel-size reading at all."""
    for live in (None, lambda: 1e-12, lambda: 9.9):
        scope = SimulatedRefineScope(magnification=2000.0, true_offset_px=(0.0, 55.0),
                                     seed=7)
        if live is not None:
            scope.get_pixel_size_m = live
        res = refine_position(scope, scope.reference_image(), 2000.0)
        assert res.success and res.residual_drift_m < 5e-7


def test_scan_rotation_warns_but_proceeds_by_default():
    msgs = []
    scope = SimulatedRefineScope(magnification=2000.0, true_offset_px=(0.0, 50.0),
                                 scan_rotation_deg=5.0, seed=5)
    res = refine_position(scope, scope.reference_image(), 2000.0,
                          logger=msgs.append)
    assert any("scan rotation" in m.lower() for m in msgs), msgs


def test_scan_rotation_strict_refuses():
    scope = SimulatedRefineScope(magnification=2000.0, true_offset_px=(0.0, 50.0),
                                 scan_rotation_deg=5.0, seed=5)
    res = refine_position(scope, scope.reference_image(), 2000.0,
                          strict_scan_rotation=True)
    assert not res.success and "refused" in res.message.lower()
    # Nothing moved.
    assert scope.get_beam_shift() == (0.0, 0.0)


def test_zero_scan_rotation_does_not_warn():
    msgs = []
    scope = SimulatedRefineScope(magnification=2000.0, true_offset_px=(0.0, 50.0),
                                 scan_rotation_deg=0.0, seed=5)
    refine_position(scope, scope.reference_image(), 2000.0, logger=msgs.append)
    assert not any("scan rotation" in m.lower() for m in msgs)


# --------- noise-floor vs wrong-sign discrimination (v0.10.2) --------- #
def test_low_snr_correct_correction_is_kept_not_blamed_on_sign():
    """A correct correction that lands near the noise floor must be KEPT.

    Previously the verify undid any correction that did not measurably improve and
    printed 'possible sign error', which at low SNR falsely implicated the sign.
    """
    import numpy as np
    scope = SimulatedRefineScope(magnification=2000.0, true_offset_px=(0.0, 8.0),
                                 noise_sigma=45.0, seed=3)  # small drift, heavy noise
    msgs = []
    res = refine_position(scope, scope.reference_image(), 2000.0,
                          residual_tolerance_m=1e-8,  # unreachably tight
                          logger=msgs.append)
    joined = " ".join(msgs).lower()
    # Must NOT claim a sign error, and must not have undone to zero beam shift.
    assert "wrong-sign" not in joined and "sign error" not in joined, joined
    bx, by = scope.get_beam_shift()
    assert abs(bx) > 0 or abs(by) > 0, "correction was undone despite correct sign"


def test_true_wrong_sign_still_flagged_and_undone():
    """A genuinely wrong sign (~doubles the error) is still caught and undone."""
    scope = SimulatedRefineScope(magnification=2000.0, true_offset_px=(0.0, 60.0),
                                 seed=6)
    res = refine_position(scope, scope.reference_image(), 2000.0, beam_sign_x=+1.0)
    assert not res.success
    assert "sign" in res.message.lower()
    assert scope.get_beam_shift() == (0.0, 0.0)


# --------------- ScopeRefineAdapter: scan rate + frame-safe grab --------------- #
class _Err(RuntimeError):
    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code


class _FakeGrabSem:
    """Minimal fake SmartSEM for exercising the real adapter's grab path offline.

    Faithful to the instrument: DP_SCANRATE is READ-ONLY (set_value raises rc=1019);
    only CMD_SCANRATE# changes it. Records the scan rate in effect at grab time so a
    test can prove the rate was raised for the grab and restored afterwards.
    """
    def __init__(self):
        self.scan_rate = 4
        self.frozen = True
        self.freeze_on = "Command"
        self.calls = []
        self.rate_at_grab = None
    def get_float(self, m):
        if m == "DP_SCANRATE":
            return float(self.scan_rate)
        if m == "DP_FROZEN":
            return 1.0 if self.frozen else 0.0
        raise KeyError(m)
    def get_str(self, m):
        if m == "DP_FIB_IMAGING":
            return "FIB"
        if m == "DP_FROZEN":
            return "Frozen" if self.frozen else "Live"
        raise KeyError(m)
    def set_value(self, m, v):
        self.calls.append(("set", m, v))
        if m == "DP_SCANRATE":
            raise _Err("Set failed with rc=1019", code=1019)  # read-only
        if m == "DP_FREEZE_ON":
            self.freeze_on = str(v)
    def execute(self, c):
        self.calls.append(("exec", c))
        if c == "CMD_UNFREEZE_ZONE":
            self.frozen = False
        elif c in ("CMD_FREEZE_ZONE", "CMD_FREEZE_ALL"):
            self.frozen = True          # frame completes immediately in the fake
        elif c.startswith("CMD_SCANRATE"):
            self.scan_rate = int(c.replace("CMD_SCANRATE", ""))
    def grab_bmp(self, path, *, xoff=0, yoff=0, width=64, height=48, reduction=0):
        self.rate_at_grab = self.scan_rate      # capture the rate in effect now
        try:
            from PIL import Image
            Image.new("L", (width, height), 128).save(path)
        except Exception:
            pass


def _have_pil():
    try:
        import PIL  # noqa: F401
        return True
    except Exception:
        return False


def test_adapter_raises_scan_rate_for_grab_then_restores():
    if not _have_pil():
        return
    from ..sources import ScopeRefineAdapter
    sem = _FakeGrabSem()  # DP_SCANRATE is read-only; only CMD_SCANRATE# works
    ad = ScopeRefineAdapter(sem, settle_s=0.0, frame_wait_s=0.0,
                            grab_size=(64, 48), imaging_scan_rate=8,
                            scan_rate_method="cmd", frozen_timeout_s=1.0)
    ad.grab_fib_image(2000.0)
    # The rate was 8 AT THE MOMENT OF THE GRAB, and restored to 4 afterwards.
    assert sem.rate_at_grab == 8, sem.rate_at_grab
    assert sem.scan_rate == 4, sem.scan_rate
    # Used CMD_SCANRATE (not a direct set) and the end-of-frame freeze sequence.
    assert any(c == ("exec", "CMD_SCANRATE8") for c in sem.calls)
    assert ("exec", "CMD_UNFREEZE_ZONE") in sem.calls
    assert any(c == ("set", "DP_FREEZE_ON", "End Frame") for c in sem.calls)


def test_adapter_scan_rate_set_then_cmd_recovers_from_readonly():
    """'set_then_cmd' tries the (failing) direct set, then recovers via CMD."""
    if not _have_pil():
        return
    from ..sources import ScopeRefineAdapter
    sem = _FakeGrabSem()
    ad = ScopeRefineAdapter(sem, settle_s=0.0, frame_wait_s=0.0,
                            grab_size=(64, 48), imaging_scan_rate=8,
                            scan_rate_method="set_then_cmd", frozen_timeout_s=1.0)
    ad.grab_fib_image(2000.0)
    assert sem.rate_at_grab == 8
    assert any(c == ("exec", "CMD_SCANRATE8") for c in sem.calls)
    assert sem.scan_rate == 4  # restored


# ------------------- mag ensured + verified before the correlation grab ------------------- #
class _PostMillFIB:
    """Mag lowered by milling; a set only sticks once in FIB imaging mode."""
    def __init__(self):
        self.mag = 500.0; self.imaging = False; self.mag_at_grab = []
    def set_value(self, m, v):
        if m == "AP_FIB_MAGNIFICATION" and self.imaging: self.mag = float(v)
    def get_float(self, m):
        return {"AP_FIB_MAGNIFICATION": self.mag,
                "DP_FIB_IMAGING": 1.0 if self.imaging else 0.0,
                "DP_FROZEN": 1.0, "DP_SCANRATE": 8.0, "AP_FIB_SCAN_ROTATION": 0.0}.get(m, 0.0)
    def get_str(self, m):
        if m == "DP_FIB_IMAGING": return "FIB" if self.imaging else "SEM"
        if m == "DP_FROZEN": return "Frozen"
        return ""
    def execute(self, c):
        if c == "CMD_FIB_MODE_FIB": self.imaging = True
    def grab_bmp(self, path, **k):
        self.mag_at_grab.append(self.mag)
        from PIL import Image; Image.new("L", (64, 48), 100).save(path)


def test_refine_ensures_imaging_mode_then_verifies_mag_before_grab():
    from ..sources import ScopeRefineAdapter
    sem = _PostMillFIB()
    src = ScopeRefineAdapter(sem, settle_s=0.0, frame_wait_s=0.0, imaging_scan_rate=None,
                             dry_run=False, logger=lambda m: None)
    src.grab_fib_image(2000.0)
    assert sem.mag_at_grab[-1] == 2000.0      # not the post-mill 500


# ---------------- Stage 2b: guarded, capped, one-move stage correction ---------------- #
_PX = None
def _px():
    from ..geometry import pixel_size_m
    return pixel_size_m(2000.0, 1024)


class _ShortStage(SimulatedRefineScope):
    """Mechanical under-travel: the stage achieves only `frac` of each move."""
    def __init__(self, frac=0.85, **k):
        super().__init__(**k); self.frac = frac
    def move_stage_xy(self, x, y):
        cx, cy = self.get_stage_xy()
        super().move_stage_xy(cx + self.frac * (x - cx), cy + self.frac * (y - cy))


class _FlippedStage(SimulatedRefineScope):
    """Wrong-handed IMAGE response: the stage goes where commanded, but the image
    moves the opposite way, so a correction makes the drift WORSE (must be undone)."""
    def move_stage_xy(self, x, y):
        b0, b1 = self.true_offset
        super().move_stage_xy(x, y)
        d0, d1 = self.true_offset[0] - b0, self.true_offset[1] - b1
        self.true_offset[0], self.true_offset[1] = b0 - d0, b1 - d1


def _run_stage(scope, **kw):
    return refine_position(scope, scope.reference_image(), 2000.0, enable_stage=True,
                           logger=lambda m: None, **kw)


def test_stage_then_beam_handoff_finishes_the_residual():
    s = _ShortStage(frac=0.85, magnification=2000.0, noise_sigma=4.0,
                    true_offset_px=(0.0, 15e-6 / _px()))
    r = _run_stage(s)
    assert r.method == "stage+beam_shift" and r.success
    assert r.applied_stage_delta_m != (0.0, 0.0)
    assert s.get_beam_shift() != (0.0, 0.0)          # beam shift took the residual
    assert abs(r.initial_drift_mag_m - 15e-6) < 0.5e-6


def test_stage_move_that_makes_things_worse_is_undone():
    s = _FlippedStage(magnification=2000.0, noise_sigma=4.0,
                      true_offset_px=(0.0, 12e-6 / _px()))
    st0 = s.get_stage_xy()
    r = _run_stage(s)
    assert not r.success and "undone" in r.message
    assert s.get_stage_xy() == st0                    # restored
    assert r.applied_stage_delta_m == (0.0, 0.0)


def test_stage_refused_outside_tilt_model_range():
    s = SimulatedRefineScope(magnification=2000.0, noise_sigma=4.0, stage_tilt_deg=45.0,
                             true_offset_px=(0.0, 15e-6 / _px()))
    st0 = s.get_stage_xy(); r = _run_stage(s)
    assert r.stage_refused and not r.success and s.get_stage_xy() == st0


def test_stage_refused_at_a_different_rotation():
    s = SimulatedRefineScope(magnification=2000.0, noise_sigma=4.0, stage_rot_deg=316.4,
                             true_offset_px=(0.0, 15e-6 / _px()))
    st0 = s.get_stage_xy(); r = _run_stage(s)
    assert r.stage_refused and "rotation" in r.message and s.get_stage_xy() == st0


def test_stage_refused_above_the_cap_no_partial_move():
    s = SimulatedRefineScope(magnification=2000.0, noise_sigma=4.0,
                             true_offset_px=(0.0, 15e-6 / _px()))
    st0 = s.get_stage_xy()
    r = _run_stage(s, stage_max_m=5e-6)               # cap below the needed ~16.5 um
    assert r.stage_refused and "exceeds" in r.message and s.get_stage_xy() == st0


def test_stage_refused_if_tilt_unreadable():
    class _NoPose(SimulatedRefineScope):
        def get_stage_tilt_rot(self): raise RuntimeError("read failed")
    s = _NoPose(magnification=2000.0, noise_sigma=4.0, true_offset_px=(0.0, 15e-6 / _px()))
    st0 = s.get_stage_xy(); r = _run_stage(s)
    assert r.stage_refused and s.get_stage_xy() == st0


def test_stage_correction_uses_live_tilt_y_at_2_and_30_deg():
    for tilt in (2.0, 12.0, 30.0):
        s = SimulatedRefineScope(magnification=2000.0, noise_sigma=4.0, stage_tilt_deg=tilt,
                                 true_offset_px=(12e-6 / _px(), 0.0))
        r = _run_stage(s)
        assert r.success, (tilt, r.message)


def test_plan_only_moves_nothing_in_any_branch():
    cases = [
        dict(off=(0.0, 4e-6 / _px())),                          # beam-shift range
        dict(off=(0.0, 15e-6 / _px())),                         # stage range
        dict(off=(0.0, 15e-6 / _px()), stage_tilt_deg=45.0),    # refused: tilt
        dict(off=(0.0, 15e-6 / _px()), stage_rot_deg=316.4),    # refused: rotation
    ]
    for c in cases:
        off = c.pop("off")
        s = SimulatedRefineScope(magnification=2000.0, noise_sigma=4.0,
                                 true_offset_px=off, **c)
        st0, b0 = s.get_stage_xy(), s.get_beam_shift()
        r = _run_stage(s, plan_only=True)
        assert r.method == "plan" and "Nothing moved" in r.message
        assert s.get_stage_xy() == st0 and s.get_beam_shift() == b0
        assert s.grabs >= 1                                       # it DID measure


def test_plan_only_reports_the_stage_move_it_would_make():
    s = SimulatedRefineScope(magnification=2000.0, noise_sigma=4.0,
                             true_offset_px=(0.0, 15e-6 / _px()))
    r = _run_stage(s, plan_only=True)
    assert "PLAN: stage X/Y" in r.message and "tilt 2.00" in r.message


# ---------------- two-stage (coarse 800x -> fine 2000x) ---------------- #
from ..refine import refine_two_stage

def _sim_um(off_um_x, **k):
    return SimulatedRefineScope(magnification=2000.0, noise_sigma=4.0,
                                true_offset_px=(0.0, off_um_x * 1e-6 / _px()), **k)


def test_sim_renders_800x_consistently_with_2000x():
    from ..registration import phase_cross_correlation
    from ..geometry import drift_px_to_m
    s = _sim_um(30.0)
    reg = phase_cross_correlation(s.reference_image(800.0), s.grab_fib_image(800.0),
                                  crop_bar=True)
    dx, _ = drift_px_to_m(reg.dy_px, reg.dx_px, 800.0, reg.shape[1])
    assert abs(dx - 30e-6) < 1e-6 and reg.snr > 4
    # the 2000x rendering is unchanged (deterministic and identical between instances)
    a = SimulatedRefineScope(magnification=2000.0, noise_sigma=0.0, add_databar=False)
    b = SimulatedRefineScope(magnification=2000.0, noise_sigma=0.0, add_databar=False)
    assert np.array_equal(a.reference_image(), b.reference_image())


def test_fine_only_refuses_aliased_out_of_reach_drift():
    """A 30 um offset at 2000x wraps to a confident WRONG -27 um; the guard refuses."""
    s = _sim_um(30.0)
    r = refine_position(s, s.reference_image(), 2000.0, enable_stage=True)
    assert r.low_confidence and "aliased" in r.message
    assert s.get_stage_xy() == (0.0, 0.0) and s.get_beam_shift() == (0.0, 0.0)


def test_two_stage_recovers_30um_with_stage_then_fine():
    s = _sim_um(30.0)
    t = refine_two_stage(s, s.reference_image(800.0), 800.0, s.reference_image(), 2000.0,
                         enable_stage=True)
    assert t.success and t.stage_moved and t.fine is not None
    assert t.fine.residual_drift_m < 0.3e-6
    assert all(r == 5 for m, r in s.grab_log if m == 800.0)       # coarse at rate 5
    assert all(r is None for m, r in s.grab_log if m == 2000.0)   # fine untouched
    assert s.mag_set_log[-1] == 2000.0                            # left at fine mag


def test_two_stage_small_drift_uses_beam_only():
    s = _sim_um(5.0)
    t = refine_two_stage(s, s.reference_image(800.0), 800.0, s.reference_image(), 2000.0,
                         enable_stage=True)
    assert t.success and not t.stage_moved


def test_two_stage_stops_when_stage_needed_but_disabled():
    s = _sim_um(30.0)
    t = refine_two_stage(s, s.reference_image(800.0), 800.0, s.reference_image(), 2000.0,
                         enable_stage=False)
    assert not t.success and t.fine is None
    assert s.get_stage_xy() == (0.0, 0.0) and s.mag_set_log[-1] == 2000.0


def test_two_stage_plan_only_moves_nothing():
    s = _sim_um(30.0)
    t = refine_two_stage(s, s.reference_image(800.0), 800.0, s.reference_image(), 2000.0,
                         enable_stage=True, plan_only=True)
    assert "Nothing moved" in t.message and t.fine is None
    assert s.get_stage_xy() == (0.0, 0.0) and s.get_beam_shift() == (0.0, 0.0)


def test_two_stage_restores_fine_mag_even_if_coarse_raises():
    class _Boom(SimulatedRefineScope):
        def grab_fib_image(self, mag, scan_rate=None):
            if mag == 800.0: raise RuntimeError("grab failed")
            return super().grab_fib_image(mag, scan_rate=scan_rate)
    s = _Boom(magnification=2000.0, noise_sigma=4.0)
    try:
        refine_two_stage(s, s.reference_image(800.0), 800.0, s.reference_image(), 2000.0)
    except RuntimeError:
        pass
    assert s.mag_set_log[-1] == 2000.0


def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
