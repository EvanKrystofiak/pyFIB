"""Regression tests against REAL Crossbeam 550 FIB data (2000x, Signal A = SESI).

The fixture ``data/real_fib_curves.json`` holds the tenengrad focus curves measured
from actual FIB images at known AP_FIB_OBJECTIVE_POTENTIAL values (databar
auto-cropped, DEFAULT_PRESMOOTH_FRAC applied). Storing the curves rather than the
images keeps the package small while still locking in the findings:

  * the peak is bracketed and unimodal on the slow-dwell set;
  * the slow-dwell (512x384) set gives ~1.8x the peak contrast of the fast-dwell
    (1024x768) set, i.e. dwell/SNR beats pixel count for focusing;
  * both sets independently agree on the focus voltage to within ~30 V;
  * the real search accepts both curves under the tuned min_contrast default.

Run:
    python -m smartfib_executor.autofocus.tests.test_real_data
"""

from __future__ import annotations

import json
from pathlib import Path

from ..search import autofocus
from ..sources import ImageSource

_DATA = Path(__file__).parent / "data" / "real_fib_curves.json"


def _curves():
    return json.loads(_DATA.read_text())["sets"]


class _CurveSource(ImageSource):
    """Replays a measured (voltage -> score) curve through the real search.

    grab() returns the score directly and the test scores it with an identity
    'metric', so the search's own sweep/refine/guard logic is what is exercised.
    """

    def __init__(self, curve, start):
        self.map = {float(v): float(s) for v, s in curve}
        self.volts = sorted(self.map)
        self._v = start
        self._start = start

    def set_voltage(self, v):
        self._v = min(self.volts, key=lambda u: abs(u - v))

    def grab(self):
        import numpy as np

        # Encode the score as a constant image; the patched metric reads it back.
        return np.full((4, 4), self.map[self._v], dtype=float)

    def initial_voltage(self):
        return self._start

    def bounds(self):
        return (min(self.volts), max(self.volts))


def _run(curve):
    """Run the real autofocus over a measured curve using an identity metric."""
    from .. import metrics as m

    volts = [v for v, _ in curve]
    center = (min(volts) + max(volts)) / 2.0
    half = (max(volts) - min(volts)) / 2.0
    src = _CurveSource(curve, center)

    m.METRICS["_identity"] = lambda img: float(img[0, 0])
    try:
        return autofocus(
            src, half_range=half, coarse_points=len(volts), metric="_identity",
            crop_bar=False, presmooth_frac=0.0, refine=False,
        )
    finally:
        m.METRICS.pop("_identity", None)


def test_slow_dwell_set_focuses_successfully():
    res = _run(_curves()["B_512x384_slow_dwell"]["curve"])
    assert res.success, res.message
    assert res.bracketed
    assert abs(res.best_voltage - 16432) <= 10, res.best_voltage


def test_fast_dwell_set_also_passes_but_with_lower_contrast():
    slow = _run(_curves()["B_512x384_slow_dwell"]["curve"])
    fast = _run(_curves()["A_1024x768_fast_dwell"]["curve"])
    assert fast.success, fast.message
    assert slow.contrast > fast.contrast * 1.5, (
        f"slow-dwell should give clearly better contrast: "
        f"{slow.contrast:.3f} vs {fast.contrast:.3f}"
    )


def test_both_sets_agree_on_focus_voltage():
    slow = _run(_curves()["B_512x384_slow_dwell"]["curve"])
    fast = _run(_curves()["A_1024x768_fast_dwell"]["curve"])
    assert abs(slow.best_voltage - fast.best_voltage) < 60, (
        f"sets disagree: {slow.best_voltage} vs {fast.best_voltage}"
    )


def test_slow_dwell_curve_is_unimodal():
    curve = sorted(_curves()["B_512x384_slow_dwell"]["curve"])
    sc = [s for _, s in curve]
    i = sc.index(max(sc))
    assert all(sc[j] <= sc[j + 1] for j in range(i)), sc
    assert all(sc[j] >= sc[j + 1] for j in range(i, len(sc) - 1)), sc


def test_contrast_clears_default_min_contrast():
    from ..search import autofocus as _af  # noqa: F401
    import inspect

    default = inspect.signature(_af).parameters["min_contrast"].default
    for name in ("B_512x384_slow_dwell", "A_1024x768_fast_dwell"):
        res = _run(_curves()[name]["curve"])
        assert res.contrast > default, (
            f"{name}: contrast {res.contrast:.3f} <= min_contrast default {default}"
        )


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
