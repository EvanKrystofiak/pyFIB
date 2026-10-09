"""After-mill image (v0.41.0): offline checks of the command sequence and its guards.

What must hold (the site is already milled when this runs):
  * nothing at all is sent while SmartSEM still indicates milling;
  * the frame is the capture sequence: FIB imaging -> capture mag -> End Frame freeze ->
    unfreeze -> freeze -> Grab, and the scan is left FROZEN;
  * any failure freezes the scan and returns a warning -- it never raises, and in a
    batch it never turns a milled site into an error;
  * in a batch it runs after milling and BEFORE the beam-shift reset.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ..after_image import grab_after_mill_image


class _Rec:
    name = "position_3"
    fib_magnification_x = 2000.0
    image_width_px = 1024
    image_height_px = 768


class ScopeSem:
    """FIB imaging comes on after CMD_FIB_MODE_FIB; freezing makes DP_FROZEN Frozen."""

    def __init__(self, events=None, *, fib_ok=True, grab_raises=False, fib="Mill"):
        self.events = events if events is not None else []
        self.fib = fib
        self.frozen = True
        self.fib_ok = fib_ok
        self.grab_raises = grab_raises
        self.beam = {"AP_FIB_BEAM_SHIFT_X": 1.2e-6, "AP_FIB_BEAM_SHIFT_Y": -0.4e-6}

    def get_str(self, m):
        if m == "DP_FIB_IMAGING":
            return self.fib
        if m == "DP_FROZEN":
            return "Frozen" if self.frozen else "Live"
        if m == "SV_IMAGE_PATH":
            return ""
        return "Off"

    def get_float(self, m):
        if m in self.beam:
            return self.beam[m]
        return 0.0

    def set_value(self, m, v):
        self.events.append(("set", m, v))
        if m in self.beam:
            self.beam[m] = float(v)
            if float(v) == 0.0:
                self.events.append(("beamzero", m))

    def execute(self, c):
        self.events.append(("cmd", c))
        if c == "CMD_FIB_MODE_FIB" and self.fib_ok:
            self.fib = "FIB"
        if c == "CMD_UNFREEZE_ZONE":
            self.frozen = False
        if c in ("CMD_FREEZE_ZONE", "CMD_FREEZE_ALL"):
            self.frozen = True

    def grab_bmp(self, path, **kw):
        self.events.append(("grab", Path(path).name, kw.get("width"), kw.get("height")))
        if self.grab_raises:
            raise RuntimeError("Grab failed")
        Path(path).write_bytes(b"BM")


def _grab(sem, active=False, **kw):
    d = Path(tempfile.mkdtemp()) / "images"
    return grab_after_mill_image(sem, _Rec(), d, milling_active=lambda: active,
                                 frame_wait_s=0.0, frozen_timeout_s=1.0,
                                 sleep=lambda s: None, log=lambda m: None, **kw), d


def test_sequence_matches_capture_and_ends_frozen():
    sem = ScopeSem()
    (path, warn), d = _grab(sem)
    assert warn is None and path is not None and path.exists(), (path, warn)
    assert path.parent == d and path.name.startswith("position_3_after_") and path.suffix == ".bmp"
    seq = [e[1] if e[0] == "cmd" else (e[1] if e[0] == "set" else e[0]) for e in sem.events]
    want = ["CMD_FIB_MODE_FIB", "AP_FIB_MAGNIFICATION", "DP_FREEZE_ON",
            "CMD_UNFREEZE_ZONE", "CMD_FREEZE_ZONE", "grab"]
    assert seq == want, seq
    assert ("set", "AP_FIB_MAGNIFICATION", 2000.0) in sem.events
    assert ("grab", path.name, 1024, 768) in sem.events
    assert sem.frozen


def test_nothing_is_sent_while_milling_is_indicated():
    sem = ScopeSem()
    (path, warn), _ = _grab(sem, active=True)
    assert path is None and "still indicates milling" in warn
    assert sem.events == []


def test_fib_imaging_timeout_skips_and_freezes():
    sem = ScopeSem(fib_ok=False)
    clock = iter(range(0, 1000))
    d = Path(tempfile.mkdtemp())
    path, warn = grab_after_mill_image(sem, _Rec(), d, milling_active=lambda: False,
                                       fib_image_timeout_s=5, clock=lambda: float(next(clock)),
                                       sleep=lambda s: None, log=lambda m: None)
    assert path is None and "did not come on" in warn
    assert not any(e[0] == "grab" for e in sem.events)
    assert ("cmd", "CMD_FREEZE_ZONE") in sem.events


def test_grab_failure_never_raises_and_freezes():
    sem = ScopeSem(grab_raises=True)
    (path, warn), _ = _grab(sem)
    assert path is None and "FAILED" in warn and sem.frozen
    assert sem.events[-1] == ("cmd", "CMD_FREEZE_ZONE")


def _batch(after_image, *, grab_raises=False):
    import smartfib_executor.batch as batch
    import smartfib_executor.after_image as AI
    from .test_workflow import FakeRunner, _rec
    from ..positions import PositionDatabase
    from ..report.runlog import RunLog
    events = []
    # "SEM" going in: the batch's own milling interlock reads DP_FIB_IMAGING too.
    sem = ScopeSem(events, grab_raises=grab_raises, fib="SEM")
    db = PositionDatabase()
    db.positions["p1"] = _rec("p1")
    d = Path(tempfile.mkdtemp())
    rl = RunLog(d, printer=lambda m: None)

    def fake_recall(sem, rec, **kw):
        events.append(("recall", rec.name))

    real = (batch.PatternRunner, batch.recall_position, AI.grab_after_mill_image)
    orig_grab = AI.grab_after_mill_image

    def fast_grab(*a, **k):
        k.setdefault("frame_wait_s", 0.0)
        k.setdefault("sleep", lambda s: None)
        return orig_grab(*a, **k)
    batch.PatternRunner = lambda sem: FakeRunner(events)
    batch.recall_position = fake_recall
    AI.grab_after_mill_image = fast_grab
    try:
        batch.mill_positions_sequential(
            sem, db=db, position_names=["p1"], ely_local_path=Path("x.ely"),
            move_method="movestage", wait_stage_idle=False, settle_s=0.0,
            load_method="none", wait_strategy="none", done_confirm_s=0.0,
            run_log=rl, after_image=after_image, after_image_dir=d / "images")
    finally:
        batch.PatternRunner, batch.recall_position, AI.grab_after_mill_image = real
    import json
    return events, json.loads(rl.path.read_text())["sites"][0], d


def test_batch_grabs_after_milling_and_before_beam_reset():
    ev, site, d = _batch(True)
    kinds = [e[0] for e in ev]
    i_mill, i_grab = kinds.index("mill_done"), kinds.index("grab")
    i_zero = max(i for i, e in enumerate(ev) if e[0] == "beamzero")
    assert i_mill < i_grab < i_zero, ev
    assert site["status"] == "milled" and site["after_image"].startswith("images")
    assert (d / site["after_image"]).exists()


def test_batch_without_option_takes_no_after_image():
    ev, site, _ = _batch(False)
    assert not any(e[0] == "grab" for e in ev) and "after_image" not in site


def test_batch_after_image_failure_keeps_site_milled():
    ev, site, _ = _batch(True, grab_raises=True)
    assert site["status"] == "milled"
    assert site["after_image"] is None and "FAILED" in site["after_image_warning"]


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
