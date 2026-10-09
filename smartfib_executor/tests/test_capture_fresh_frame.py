"""REGRESSION: capture-position saved a STALE image (the previous position's frame).

Grab() saves the frame store as-is. The old capture did CMD_UNFREEZE_ZONE and then
Grab() immediately, before any new frame had been scanned -- so whenever the scan was
frozen going in (every capture ends frozen; refine/autofocus leave it frozen), the BMP
was the LAST frozen frame. Capture #2 saved capture #1's image, and refine then used
that stale reference.

The mock models exactly that: the frame store only refreshes when a frame completes
(freeze at end of frame) after an unfreeze; Grab() writes the store's contents.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np

from ..positions import capture_position


class _FrameStoreSem:
    def __init__(self, scene: int):
        self.scene = scene          # what the beam would image at the current stage pos
        self.store = 0              # the frame store (what Grab() saves)
        self.live = False
        self.never_frozen = False   # simulate DP_FROZEN never reporting Frozen
        self.freeze_on = None

    def set_value(self, m, v):
        if m == "DP_FREEZE_ON":
            self.freeze_on = v

    def execute(self, c):
        if c == "CMD_UNFREEZE_ZONE":
            self.live = True
        elif c in ("CMD_FREEZE_ZONE", "CMD_FREEZE_ALL"):
            if self.live:
                self.store = self.scene     # end-of-frame freeze completes a FRESH frame
            self.live = False

    def get_str(self, m):
        if m == "DP_FROZEN":
            return "Live" if (self.live or self.never_frozen) else "Frozen"
        return ""

    def get_float(self, m):
        return {"AP_STAGE_AT_X": 1e-3, "AP_STAGE_AT_Y": 2e-3, "AP_STAGE_AT_Z": 3e-3,
                "AP_FIB_OBJECTIVE_POTENTIAL": 16400.0, "AP_HEIGHT": 4.286e-5}.get(m, 0.0)

    def grab_bmp(self, path, **k):
        from PIL import Image
        Image.fromarray(np.full((48, 64), self.store, dtype=np.uint8)).save(path)


def _value(path) -> int:
    from PIL import Image
    return int(np.asarray(Image.open(path))[0, 0])


def _cap(sem, name, d, **kw):
    kw.setdefault("frame_wait_s", 0.0)
    return capture_position(sem, name, fib_magnification_x=2000.0,
                            image_bmp=d / f"{name}.bmp", ensure_fib_image=False, **kw)


def test_second_capture_saves_the_new_site_not_the_previous_frame():
    d = Path(tempfile.mkdtemp())
    sem = _FrameStoreSem(scene=50)                 # site A
    _cap(sem, "A", d)
    assert _value(d / "A.bmp") == 50
    assert not sem.live                            # capture leaves the scan frozen
    sem.scene = 200                                # stage moved to site B; store still A
    _cap(sem, "B", d)
    assert _value(d / "B.bmp") == 200, "stale grab: saved the previous position's frame"
    assert sem.freeze_on in ("End Frame", 0.0)     # freezes at END of frame


def test_as_is_flag_still_grabs_the_frame_on_screen():
    # --no-unfreeze-before-grab keeps its explicit meaning: grab what is displayed.
    d = Path(tempfile.mkdtemp())
    sem = _FrameStoreSem(scene=50); _cap(sem, "A", d)
    sem.scene = 200
    _cap(sem, "B", d, unfreeze_before_grab=False)
    assert _value(d / "B.bmp") == 50


def test_freeze_after_grab_false_leaves_scan_live():
    d = Path(tempfile.mkdtemp())
    sem = _FrameStoreSem(scene=50)
    _cap(sem, "A", d, freeze_after_grab=False)
    assert _value(d / "A.bmp") == 50 and sem.live


def test_unconfirmed_frame_is_recorded_as_a_warning():
    d = Path(tempfile.mkdtemp())
    sem = _FrameStoreSem(scene=50); sem.never_frozen = True
    rec = _cap(sem, "A", d, frozen_timeout_s=0.3)
    assert rec.notes and "DP_FROZEN did not report Frozen" in rec.notes


def test_coarse_reference_is_grabbed_after_fine_and_mag_restored():
    d = Path(tempfile.mkdtemp())
    class _Sem(_FrameStoreSem):
        def __init__(self, scene):
            super().__init__(scene); self.mags = []; self.cmds = []; self.rate = 8
        def set_value(self, m, v):
            super().set_value(m, v)
            if m == "AP_FIB_MAGNIFICATION": self.mags.append(float(v))
        def execute(self, c):
            super().execute(c); self.cmds.append(c)
            if c.startswith("CMD_SCANRATE"): self.rate = int(c[len("CMD_SCANRATE"):])
        def get_float(self, m):
            if m == "DP_SCANRATE": return float(self.rate)
            return super().get_float(m)
    sem = _Sem(scene=50)
    rec = _cap(sem, "A", d, coarse_magnification_x=800.0, coarse_scan_rate=5)
    assert (d / "A.bmp").exists() and (d / "A_coarse.bmp").exists()
    assert rec.image_bmp_coarse and rec.image_bmp_coarse.endswith("A_coarse.bmp")
    assert rec.coarse_magnification_x == 800.0 and rec.fib_magnification_x == 2000.0
    assert sem.mags == [2000.0, 800.0, 2000.0]            # fine, coarse, restored
    assert "CMD_SCANRATE5" in sem.cmds and sem.rate == 8   # coarse at 5, then restored
    assert not sem.live                                     # left frozen
    assert not rec.notes


def test_coarse_failure_keeps_fine_capture_and_warns():
    d = Path(tempfile.mkdtemp())
    class _Sem(_FrameStoreSem):
        def __init__(self, scene):
            super().__init__(scene); self.mag = 2000.0; self.mags = []
        def set_value(self, m, v):
            super().set_value(m, v)
            if m == "AP_FIB_MAGNIFICATION": self.mag = float(v); self.mags.append(float(v))
        def grab_bmp(self, path, **k):
            if self.mag == 800.0: raise RuntimeError("coarse grab failed")
            super().grab_bmp(path, **k)
    sem = _Sem(scene=50)
    rec = _cap(sem, "A", d, coarse_magnification_x=800.0)
    assert (d / "A.bmp").exists() and _value(d / "A.bmp") == 50   # fine intact
    assert rec.image_bmp_coarse is None and rec.coarse_magnification_x is None
    assert rec.notes and "coarse reference" in rec.notes and "FAILED" in rec.notes
    assert sem.mags[-1] == 2000.0                               # mag restored


def test_no_coarse_by_default():
    d = Path(tempfile.mkdtemp()); sem = _FrameStoreSem(scene=50)
    rec = _cap(sem, "A", d)
    assert rec.image_bmp_coarse is None and not (d / "A_coarse.bmp").exists()


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
