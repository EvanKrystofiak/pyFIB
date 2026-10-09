"""REGRESSION: after milling, the FIB mag is lowered and a mag set issued BEFORE the
column is in FIB imaging mode does not stick. The autofocus source must ensure
imaging mode, then set AND read back the mag, before EVERY grab.
(Observed on the instrument: position 2 autofocus ran at the post-mill mag.)"""
from __future__ import annotations

import numpy as np

from ..sources import ScopeImageSource


class PostMillFIB:
    """Mag lowered by milling; a set only sticks once in FIB imaging mode."""
    def __init__(self):
        self.mag = 500.0; self.imaging = False; self.mag_at_grab = []
    def set_value(self, m, v):
        if m == "AP_FIB_MAGNIFICATION" and self.imaging:
            self.mag = float(v)
    def get_float(self, m):
        return {"AP_FIB_MAGNIFICATION": self.mag,
                "DP_FIB_IMAGING": 1.0 if self.imaging else 0.0,
                "AP_FIB_PIXEL_SIZE": 5.58e-8, "AP_FIB_SCAN_ROTATION": 0.0,
                "DP_FROZEN": 1.0, "DP_SCANRATE": 8.0,
                "AP_FIB_OBJECTIVE_POTENTIAL": 16400.0, "AP_FIB_BEAM_SHIFT_X": 0.0,
                "AP_WIDTH": 5.715e-5}.get(m, 0.0)
    def get_str(self, m):
        if m == "DP_FIB_IMAGING": return "FIB" if self.imaging else "SEM"
        if m == "DP_FROZEN": return "Frozen"
        return ""
    def execute(self, c):
        if c == "CMD_FIB_MODE_FIB": self.imaging = True
    def grab_bmp(self, path, **k):
        self.mag_at_grab.append(self.mag)
        from PIL import Image
        Image.new("L", (64, 48), 100).save(path)


def _src(sem):
    return ScopeImageSource(sem, start_voltage=16400.0, bounds=(16300, 16500),
                            fib_magnification=2000.0, settle_s=0.0, frame_wait_s=0.0,
                            strict_scan_rotation=False, imaging_scan_rate=None,
                            dry_run=False, logger=lambda m: None)


def test_prepare_restores_mag_after_entering_imaging_mode():
    sem = PostMillFIB()
    _src(sem).prepare()
    assert sem.mag == 2000.0     # not the post-mill 500


def test_grab_reasserts_mag_when_milling_lowered_it_again():
    sem = PostMillFIB()
    src = _src(sem); src.prepare()
    sem.mag = 500.0              # milling knocked it down between frames
    src.grab()
    assert sem.mag_at_grab[-1] == 2000.0


def test_refuses_to_grab_if_mag_cannot_be_established():
    class Stuck(PostMillFIB):
        def set_value(self, m, v): pass   # mag never takes
    sem = Stuck(); sem.imaging = True
    src = _src(sem)
    try:
        src.prepare()
        raise AssertionError("should refuse when mag cannot be verified")
    except RuntimeError as e:
        assert "wrong mag" in str(e)


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
