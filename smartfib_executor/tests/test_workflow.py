"""Offline tests for the per-position autofocus/refine milling integration.

These assert the ORDER and seriality of operations without any hardware:
    recall -> autofocus (if selected) -> refine (if selected) -> mill,
    and no next position is started until the previous mill completes.
"""

from __future__ import annotations

from pathlib import Path

from ..batch import mill_positions_sequential
from ..positions import PositionDatabase, PositionRecord, StageAt
from ..milling_workflow import AutofocusConfig, RefineConfig


def _rec(name, v=16400.0):
    return PositionRecord(
        name=name, captured_unix_s=1.0, fib_magnification_x=2000.0,
        fib_objective_potential_v=v,
        stage_at=StageAt(x_m=1e-3, y_m=2e-3, z_m=3e-3, r_deg=0.0, m_m=0.0, t_deg=None),
        image_bmp=f"{name}.bmp",
    )


class FakeSem:
    """Records the global order of operations across all positions."""
    def get_float(self, m):
        return self.beam.get(m, 0.0)
    def get_str(self, m):
        # Report "not milling" so the safety interlock never blocks.
        return "Imaging"
    def __init__(self, events):
        self.events = events
        self.beam = {"AP_FIB_BEAM_SHIFT_X": 7.0e-6, "AP_FIB_BEAM_SHIFT_Y": -4.0e-6}
    def set_value(self, m, v):
        if m == "AP_FIB_OBJECTIVE_POTENTIAL":
            self.events.append(("objective", float(v)))
        if m in ("AP_FIB_BEAM_SHIFT_X", "AP_FIB_BEAM_SHIFT_Y"):
            self.beam[m] = float(v)
            self.events.append(("beamzero" if v == 0.0 else "beamset", m, float(v)))
    def execute(self, *a, **k):
        pass


class FakeRunner:
    """Stand-in PatternRunner that records milling and asserts seriality."""
    def __init__(self, events):
        self.events = events
        self.active = False
    def run(self, job):
        assert not self.active, "milling started before previous mill finished!"
        self.active = True
        self.events.append(("mill_start",))
        # ... milling happens ...
        self.active = False
        self.events.append(("mill_done",))


def _run(names, af=None, rf=None, monkeypatch_runner=True):
    events = []
    sem = FakeSem(events)
    db = PositionDatabase()
    for n in names:
        db.positions[n] = _rec(n)

    def fake_recall(sem, rec, **kw):
        events.append(("recall", rec.name))

    def fake_af(sem, rec, cfg, *, dry_run=False, logger=None):
        events.append(("autofocus", rec.name))

    def fake_rf(sem, rec, cfg, *, ref_base_dir=None, dry_run=False, logger=None):
        events.append(("refine", rec.name))

    import smartfib_executor.batch as batch
    real_runner = batch.PatternRunner
    real_recall = batch.recall_position
    batch.PatternRunner = lambda sem: FakeRunner(events)
    batch.recall_position = fake_recall
    try:
        mill_positions_sequential(
            sem, db=db, position_names=names, ely_local_path=Path("x.ely"),
            move_method="ap_goto", wait_stage_idle=False, settle_s=0.0,
            load_method="none", wait_strategy="none", done_confirm_s=0.0,
            autofocus_names=af or [], refine_names=rf or [],
            _autofocus_fn=fake_af, _refine_fn=fake_rf,
        )
    finally:
        batch.PatternRunner = real_runner
        batch.recall_position = real_recall
    return events


def test_order_recall_autofocus_refine_mill_for_one_position():
    ev = _run(["p1"], af=["p1"], rf=["p1"])
    seq = [e[0] for e in ev if e[0] in ("recall", "autofocus", "refine", "mill_start")]
    assert seq == ["recall", "autofocus", "refine", "mill_start"], seq


def test_autofocus_only():
    ev = _run(["p1"], af=["p1"], rf=[])
    seq = [e[0] for e in ev if e[0] in ("recall", "autofocus", "refine", "mill_start")]
    assert seq == ["recall", "autofocus", "mill_start"], seq


def test_refine_only():
    ev = _run(["p1"], af=[], rf=["p1"])
    seq = [e[0] for e in ev if e[0] in ("recall", "autofocus", "refine", "mill_start")]
    assert seq == ["recall", "refine", "mill_start"], seq


def test_neither_selected_is_plain_recall_mill():
    ev = _run(["p1"], af=[], rf=[])
    seq = [e[0] for e in ev if e[0] in ("recall", "autofocus", "refine", "mill_start")]
    assert seq == ["recall", "mill_start"], seq


def test_per_position_selection_is_independent():
    """p1 gets autofocus only, p2 gets refine only, p3 gets both."""
    ev = _run(["p1", "p2", "p3"], af=["p1", "p3"], rf=["p2", "p3"])
    by = {}
    cur = None
    for e in ev:
        if e[0] == "recall":
            cur = e[1]; by[cur] = []
        elif e[0] in ("autofocus", "refine", "mill_start"):
            by[cur].append(e[0])
    assert by["p1"] == ["autofocus", "mill_start"], by["p1"]
    assert by["p2"] == ["refine", "mill_start"], by["p2"]
    assert by["p3"] == ["autofocus", "refine", "mill_start"], by["p3"]


def test_serial_no_overlap_between_positions():
    """Every position must fully finish (mill_done) before the next recall."""
    ev = _run(["p1", "p2", "p3"], af=["p1", "p2", "p3"], rf=["p1", "p2", "p3"])
    # Between two consecutive recalls there must be exactly one mill_done.
    recalls = [i for i, e in enumerate(ev) if e[0] == "recall"]
    for a, b in zip(recalls, recalls[1:]):
        between = [e[0] for e in ev[a:b]]
        assert between.count("mill_start") == 1 and between.count("mill_done") == 1, between
    # And the whole run ends with a completed mill.
    assert ev[-1][0] in ("mill_done",) or any(e[0] == "mill_done" for e in ev[-3:])


def test_autofocus_precedes_refine_always():
    """Refine must never run before autofocus for the same position."""
    ev = _run(["p1"], af=["p1"], rf=["p1"])
    ai = next(i for i, e in enumerate(ev) if e[0] == "autofocus")
    ri = next(i for i, e in enumerate(ev) if e[0] == "refine")
    assert ai < ri, ev


# --------------- helper-level behavior (dry-run / failure policy) --------------- #
def _dummy_sem():
    class DummySem:
        def get_float(self, m): return 16400.0 if "OBJECTIVE" in m else 0.0
        def get_str(self, m): return "FIB"
        def set_value(self, *a, **k): pass
        def execute(self, *a, **k): pass
    return DummySem()


def test_autofocus_bounds_are_clamped_around_saved_voltage():
    from ..milling_workflow import run_autofocus_at_position, AutofocusConfig
    logs = []
    rec = _rec("p1", v=16400.0)
    run_autofocus_at_position(_dummy_sem(), rec,
                              AutofocusConfig(half_range_v=150.0, bound_margin_v=400.0),
                              dry_run=True, logger=logs.append)
    joined = " ".join(logs)
    assert "16000..16800 V" in joined, joined  # clamp = saved +/- max(400, 2*150)


def test_autofocus_failure_defaults_to_proceed():
    """Low-confidence autofocus must NOT raise by default (mills at recalled V)."""
    from ..milling_workflow import run_autofocus_at_position, AutofocusConfig
    # dry-run yields a flat curve -> low confidence; must return, not raise.
    res = run_autofocus_at_position(_dummy_sem(), _rec("p1"), AutofocusConfig(),
                                    dry_run=True, logger=lambda m: None)
    assert res is not None and not res.success


def test_autofocus_abort_on_failure_raises():
    from ..milling_workflow import run_autofocus_at_position, AutofocusConfig
    try:
        run_autofocus_at_position(_dummy_sem(), _rec("p1"),
                                  AutofocusConfig(abort_on_failure=True),
                                  dry_run=True, logger=lambda m: None)
        raise AssertionError("should have raised")
    except RuntimeError:
        pass


def test_refine_without_reference_skips_gracefully():
    from ..milling_workflow import run_refine_at_position, RefineConfig
    rec = _rec("p1"); rec = rec.__class__(**{**rec.__dict__, "image_bmp": None})
    out = run_refine_at_position(_dummy_sem(), rec, RefineConfig(),
                                 logger=lambda m: None)
    assert out is None


def test_refine_without_reference_aborts_when_configured():
    from ..milling_workflow import run_refine_at_position, RefineConfig
    rec = _rec("p1"); rec = rec.__class__(**{**rec.__dict__, "image_bmp": None})
    try:
        run_refine_at_position(_dummy_sem(), rec,
                               RefineConfig(abort_on_failure=True),
                               logger=lambda m: None)
        raise AssertionError("should have raised")
    except RuntimeError:
        pass


# -------- refine stage correction: pipeline opt-in (v0.36.0) -------- #
def _capture_refine_kwargs(cfg, stage_moved=False):
    """Run run_refine_at_position with refine_position stubbed; return its kwargs."""
    import tempfile
    import numpy as np
    from PIL import Image
    import smartfib_executor.refine as R
    from ..milling_workflow import run_refine_at_position
    from ..refine.refine import RefineResult
    d = Path(tempfile.mkdtemp())
    Image.fromarray(np.full((48, 64), 100, dtype=np.uint8)).save(d / "p1.bmp")
    rec = _rec("p1"); rec = rec.__class__(**{**rec.__dict__, "image_bmp": "p1.bmp"})
    seen, logs = {}, []
    old = R.refine_position
    def fake(scope, ref, mag, **kw):
        seen.update(kw)
        return RefineResult(success=True, method="stage+beam_shift" if stage_moved else "beam_shift",
                            initial_drift_x_m=12e-6, initial_drift_y_m=0.0,
                            initial_drift_mag_m=12e-6, residual_drift_m=1e-7,
                            applied_stage_delta_m=(-13.2e-6, 0.0) if stage_moved else (0.0, 0.0))
    R.refine_position = fake
    try:
        run_refine_at_position(_dummy_sem(), rec, cfg, ref_base_dir=d, logger=logs.append)
    finally:
        R.refine_position = old
    return seen, logs


def test_refine_stage_is_off_by_default_in_the_pipeline():
    seen, _ = _capture_refine_kwargs(RefineConfig())
    assert seen["enable_stage"] is False


def test_refine_stage_opt_in_reaches_refine_with_the_100um_cap():
    seen, logs = _capture_refine_kwargs(RefineConfig(enable_stage=True), stage_moved=True)
    assert seen["enable_stage"] is True and abs(seen["stage_max_m"] - 100e-6) < 1e-12
    assert any("STAGE moved" in l for l in logs)                 # visible in the log


def test_mill_positions_cli_flag_sets_enable_stage():
    import smartfib_executor.cli as C
    captured = {}
    old = C.mill_positions_sequential
    class _Sem:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def connect_local(self, *a): pass
        def connect_remoting(self): pass
        def get_float(self, m): return 2.0   # AP_STAGE_AT_T for the v0.46.0 tilt check
    import tempfile
    d = Path(tempfile.mkdtemp()); pf = d / "positions.json"
    db = PositionDatabase(); db.positions["p1"] = _rec("p1"); db.save(pf)
    ely = d / "x.ely"; ely.write_text("<x/>")
    old_sem = C.SmartSEM
    try:
        C.mill_positions_sequential = lambda sem, **kw: captured.update(kw)
        C.SmartSEM = _Sem
        for flag, want in ((["--refine-enable-stage"], True), ([], False)):
            captured.clear()
            C.main(["mill-positions", str(ely), "p1", "--positions-file", str(pf),
                    "--refine", "p1", "--arm", *flag])
            assert captured["refine_config"].enable_stage is want
    finally:
        C.mill_positions_sequential = old; C.SmartSEM = old_sem



# ------------- 800x fallback when 2000x cannot locate the site (v0.39.0) ------------- #
def _rr(success, *, low_conf=False, disabled=False, refused=False, stage=(0.0, 0.0),
        msg=""):
    from ..refine.refine import RefineResult
    return RefineResult(success=success, method="none" if not success else "beam_shift",
                        initial_drift_x_m=0.0, initial_drift_y_m=0.0,
                        initial_drift_mag_m=0.0, residual_drift_m=5e-8 if success else 1e-5,
                        applied_stage_delta_m=stage, low_confidence=low_conf,
                        stage_required_but_disabled=disabled, stage_refused=refused,
                        message=msg)


class _BeamSem:
    """Dummy SEM with a readable/settable beam shift (records every set)."""
    def __init__(self):
        self.beam = {"AP_FIB_BEAM_SHIFT_X": 2e-6, "AP_FIB_BEAM_SHIFT_Y": -1e-6}
        self.sets = []
    def get_float(self, m):
        if m in self.beam:
            return self.beam[m]
        return 16400.0 if "OBJECTIVE" in m else 0.0
    def get_str(self, m): return "FIB"
    def set_value(self, m, v):
        self.sets.append((m, float(v)))
        if m in self.beam:
            self.beam[m] = float(v)
    def execute(self, *a, **k): pass


def _fallback(cfg, first, two=None, *, coarse=True):
    """run_refine_at_position with refine_position / refine_two_stage stubbed."""
    import tempfile
    import numpy as np
    from PIL import Image
    import smartfib_executor.refine as R
    import smartfib_executor.refine.refine as RR
    from ..milling_workflow import run_refine_at_position
    d = Path(tempfile.mkdtemp())
    Image.fromarray(np.full((48, 64), 100, dtype=np.uint8)).save(d / "p1.bmp")
    Image.fromarray(np.full((48, 64), 90, dtype=np.uint8)).save(d / "p1_coarse.bmp")
    rec = _rec("p1")
    extra = {"image_bmp": "p1.bmp"}
    if coarse:
        extra.update(image_bmp_coarse="p1_coarse.bmp", coarse_magnification_x=800.0)
    rec = rec.__class__(**{**rec.__dict__, **extra})
    calls, logs, sem = [], [], _BeamSem()
    o1, o2 = R.refine_position, RR.refine_two_stage
    def fake_fine(scope, ref, mag, **kw):
        sem.beam["AP_FIB_BEAM_SHIFT_X"] = 9e-6          # a failed pass left a shift behind
        return first
    def fake_two(scope, cref, cmag, fref, fmag, **kw):
        calls.append({"cmag": cmag, "fmag": fmag, "cref_mean": float(cref.mean()),
                      "beam_at_start": (sem.beam["AP_FIB_BEAM_SHIFT_X"],
                                        sem.beam["AP_FIB_BEAM_SHIFT_Y"]), **kw})
        return two
    R.refine_position, RR.refine_two_stage = fake_fine, fake_two
    try:
        out = run_refine_at_position(sem, rec, cfg, ref_base_dir=d, logger=logs.append)
    finally:
        R.refine_position, RR.refine_two_stage = o1, o2
    return out, calls, logs


def _two(success, coarse_stage=(-30e-6, -30e-6)):
    from ..refine.refine import TwoStageResult
    coarse = _rr(True, stage=coarse_stage, msg="Stage-refined to 0.58 um.")
    fine = _rr(success, msg="Refined to 0.05 um with beam shift." if success else "x")
    return TwoStageResult(coarse, fine, success,
                          "coarse: ok | fine: " + ("ok" if success else "failed"),
                          coarse_stage != (0.0, 0.0))


def test_800x_fallback_runs_when_2000x_cannot_locate_the_site():
    out, calls, logs = _fallback(RefineConfig(enable_stage=True),
                                 _rr(False, low_conf=True, msg="Unreliable: aliased"),
                                 _two(True))
    assert len(calls) == 1 and calls[0]["cmag"] == 800.0 and calls[0]["fmag"] == 2000.0
    assert abs(calls[0]["cref_mean"] - 90.0) < 1e-9          # the 800x image was used
    assert calls[0]["enable_stage"] is True and abs(calls[0]["stage_max_m"] - 100e-6) < 1e-12
    assert calls[0]["beam_at_start"] == (2e-6, -1e-6)        # beam shift put back first
    assert out.success
    assert any("retrying with the 800x image" in l for l in logs)
    assert any("STAGE moved" in l and "800x pass" in l for l in logs)


def test_800x_fallback_never_chains_a_second_stage_move():
    # The failed 2000x pass already KEPT a stage move -> the 800x pass gets beam shift only.
    first = _rr(False, stage=(-12e-6, 0.0), msg="still beyond the beam-shift window")
    _, calls, logs = _fallback(RefineConfig(enable_stage=True), first, _two(True))
    assert calls and calls[0]["enable_stage"] is False
    assert any("one stage move per site" in l for l in logs)


def test_800x_fallback_respects_stage_opt_out():
    _, calls, _ = _fallback(RefineConfig(enable_stage=False),
                            _rr(False, low_conf=True), _two(True, coarse_stage=(0.0, 0.0)))
    assert calls and calls[0]["enable_stage"] is False


def test_no_800x_fallback_when_2000x_succeeds_or_located_the_site():
    for first in (_rr(True),                                   # aligned at 2000x
                  _rr(False, disabled=True),                   # located; stage not allowed
                  _rr(False, refused=True)):                   # located; stage guard refused
        _, calls, _ = _fallback(RefineConfig(), first, _two(True))
        assert calls == [], first


def test_no_800x_fallback_without_an_800x_image_or_when_disabled():
    out, calls, logs = _fallback(RefineConfig(), _rr(False, low_conf=True), _two(True),
                                 coarse=False)
    assert calls == [] and not out.success
    assert any("no 800x image" in l for l in logs)
    _, calls, _ = _fallback(RefineConfig(coarse_fallback=False), _rr(False, low_conf=True),
                            _two(True))
    assert calls == []


def test_800x_fallback_failure_follows_abort_policy():
    out, calls, logs = _fallback(RefineConfig(), _rr(False, low_conf=True), _two(False))
    assert calls and not out.success
    assert any("800x fallback FAILED" in l for l in logs)
    assert any("NOT refined" in l and "800x pass moved it" in l for l in logs)
    try:
        _fallback(RefineConfig(abort_on_failure=True), _rr(False, low_conf=True), _two(False))
    except RuntimeError:
        pass
    else:
        raise AssertionError("abort_on_failure did not raise")


def test_mill_positions_cli_coarse_fallback_flag():
    import smartfib_executor.cli as C
    captured = {}
    old = C.mill_positions_sequential
    class _Sem:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def connect_local(self, *a): pass
        def connect_remoting(self): pass
        def get_float(self, m): return 2.0   # AP_STAGE_AT_T for the v0.46.0 tilt check
    import tempfile
    d = Path(tempfile.mkdtemp()); pf = d / "positions.json"
    db = PositionDatabase(); db.positions["p1"] = _rec("p1"); db.save(pf)
    ely = d / "x.ely"; ely.write_text("<x/>")
    old_sem = C.SmartSEM
    try:
        C.mill_positions_sequential = lambda sem, **kw: captured.update(kw)
        C.SmartSEM = _Sem
        for flag, want in ((["--no-refine-coarse-fallback"], False), ([], True)):
            captured.clear()
            C.main(["mill-positions", str(ely), "p1", "--positions-file", str(pf),
                    "--refine", "p1", "--arm", *flag])
            assert captured["refine_config"].coarse_fallback is want
    finally:
        C.mill_positions_sequential = old; C.SmartSEM = old_sem

# ------------------- beam-shift reset between positions ------------------- #
def test_beam_shift_zeroed_at_start_and_after_each_position():
    """FIB beam shift X and Y must be 0 before the first site and after each one."""
    ev = _run(["p1", "p2"], af=[], rf=["p1", "p2"])
    # There must be a zeroing of BOTH axes before the first recall.
    first_recall = next(i for i, e in enumerate(ev) if e[0] == "recall")
    pre = [e for e in ev[:first_recall] if e[0] == "beamzero"]
    axes_pre = {e[1] for e in pre}
    assert axes_pre == {"AP_FIB_BEAM_SHIFT_X", "AP_FIB_BEAM_SHIFT_Y"}, axes_pre
    # After each mill_done there must be a zeroing of both axes before the next recall.
    idxs = [i for i, e in enumerate(ev) if e[0] == "mill_done"]
    for md in idxs:
        after = [e for e in ev[md:md + 6] if e[0] == "beamzero"]
        axes = {e[1] for e in after}
        assert axes == {"AP_FIB_BEAM_SHIFT_X", "AP_FIB_BEAM_SHIFT_Y"}, (md, axes)


def test_beam_shift_zeroed_even_when_position_errors():
    """A failing position must still leave beam shift at 0 for the next site."""
    events = []
    sem = FakeSem(events)
    db = PositionDatabase()
    for n in ["p1", "p2"]:
        db.positions[n] = _rec(n)

    def fake_recall(sem, rec, **kw):
        events.append(("recall", rec.name))

    def boom_af(sem, rec, cfg, *, dry_run=False, logger=None):
        events.append(("autofocus", rec.name))
        raise RuntimeError("autofocus blew up")

    import smartfib_executor.batch as batch
    real_runner, real_recall = batch.PatternRunner, batch.recall_position
    batch.PatternRunner = lambda sem: FakeRunner(events)
    batch.recall_position = fake_recall
    try:
        mill_positions_sequential(
            sem, db=db, position_names=["p1", "p2"], ely_local_path=Path("x.ely"),
            move_method="ap_goto", wait_stage_idle=False, settle_s=0.0,
            load_method="none", wait_strategy="none", done_confirm_s=0.0,
            autofocus_names=["p1"], _autofocus_fn=boom_af,
            continue_on_error=True,
        )
    finally:
        batch.PatternRunner, batch.recall_position = real_runner, real_recall

    # p1 errored in autofocus (before milling); beam shift must still be zeroed.
    p1_recall = next(i for i, e in enumerate(events) if e == ("recall", "p1"))
    p2_recall = next(i for i, e in enumerate(events) if e == ("recall", "p2"))
    between = [e for e in events[p1_recall:p2_recall] if e[0] == "beamzero"]
    axes = {e[1] for e in between}
    assert axes == {"AP_FIB_BEAM_SHIFT_X", "AP_FIB_BEAM_SHIFT_Y"}, axes
    assert sem.beam["AP_FIB_BEAM_SHIFT_X"] == 0.0
    assert sem.beam["AP_FIB_BEAM_SHIFT_Y"] == 0.0


def test_beam_shift_reset_can_be_disabled():
    events = []
    sem = FakeSem(events)
    db = PositionDatabase()
    db.positions["p1"] = _rec("p1")

    def fake_recall(sem, rec, **kw):
        events.append(("recall", rec.name))

    import smartfib_executor.batch as batch
    real_runner, real_recall = batch.PatternRunner, batch.recall_position
    batch.PatternRunner = lambda sem: FakeRunner(events)
    batch.recall_position = fake_recall
    try:
        mill_positions_sequential(
            sem, db=db, position_names=["p1"], ely_local_path=Path("x.ely"),
            move_method="ap_goto", wait_stage_idle=False, settle_s=0.0,
            load_method="none", wait_strategy="none", done_confirm_s=0.0,
            reset_beam_shift_between_positions=False,
        )
    finally:
        batch.PatternRunner, batch.recall_position = real_runner, real_recall

    assert not any(e[0] == "beamzero" for e in events), "reset should be disabled"


def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t(); print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1; print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1; print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
