"""Offline tests for the FIB autofocus core.

No hardware, no COM: everything runs against numpy + the simulator. Run either:

    python -m smartfib_executor.autofocus.tests.test_autofocus     # standalone
    pytest smartfib_executor/autofocus/tests/                      # if pytest present
"""

from __future__ import annotations

import numpy as np

from .. import metrics
from ..sweep import plan_voltages, refine_peak, _parabola_vertex
from ..search import autofocus
from ..sources import SimulatedImageSource

try:
    from PIL import Image, ImageFilter  # noqa: F401
    _HAVE_PIL = True
except Exception:
    _HAVE_PIL = False


# --------------------------- metrics ---------------------------------- #
def _sharp_image(seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = np.full((128, 128), 40.0)
    for _ in range(50):
        cy, cx = rng.integers(6, 122), rng.integers(6, 122)
        img[cy - 3 : cy + 3, cx - 3 : cx + 3] = float(rng.integers(150, 256))
    return img


def _blur(img: np.ndarray, radius: float) -> np.ndarray:
    if radius <= 0 or not _HAVE_PIL:
        return img.copy()
    pil = Image.fromarray(img.astype(np.uint8)).filter(ImageFilter.GaussianBlur(radius))
    return np.asarray(pil, dtype=np.float64)


def test_metrics_decrease_with_blur():
    """Every metric should drop monotonically as the image is blurred."""
    base = _sharp_image()
    radii = [0.0, 1.0, 2.0, 4.0, 8.0]
    for name in metrics.METRICS:
        scores = [metrics.sharpness(_blur(base, r), name) for r in radii]
        for a, b in zip(scores, scores[1:]):
            assert a >= b - 1e-9, f"{name} not monotonic under blur: {scores}"


def test_normalized_variance_brightness_invariant():
    """Global brightness scaling should barely move normalized_variance."""
    base = _sharp_image()
    s1 = metrics.normalized_variance(base)
    s2 = metrics.normalized_variance(base * 1.5)  # scale brightness/contrast together
    assert abs(s1 - s2) / max(s1, 1e-9) < 1e-6


def test_to_gray_shapes():
    assert metrics.to_gray(np.zeros((10, 10))).shape == (10, 10)
    assert metrics.to_gray(np.zeros((10, 10, 3))).shape == (10, 10)
    assert metrics.to_gray(np.zeros((10, 10, 1))).shape == (10, 10)


# --------------------------- preprocessing ---------------------------- #
def _with_databar(img: np.ndarray) -> np.ndarray:
    """Append a SmartSEM-like databar: white separator row + static text block."""
    h, w = img.shape
    bar_h = int(round(h * 0.094 / (1 - 0.094)))  # ~9.4% of the final height
    sep = np.full((1, w), 255.0)
    bar = np.zeros((bar_h, w))
    bar[2:8, 5 : w // 2] = 255.0  # high-contrast "text"
    return np.vstack([img, sep, bar])


def test_detect_databar_row():
    base = _sharp_image()
    full = _with_databar(base)
    row = metrics.detect_databar_row(full)
    assert row == base.shape[0], f"expected {base.shape[0]}, got {row}"


def test_detect_databar_none_when_absent():
    assert metrics.detect_databar_row(_sharp_image()) is None


def test_crop_databar_restores_image_area():
    base = _sharp_image()
    out = metrics.crop_databar(_with_databar(base))
    assert out.shape == base.shape
    assert np.allclose(out, base)


def test_databar_would_otherwise_skew_score():
    """The static bar must not contribute: cropped score == bare-image score."""
    base = _sharp_image()
    bare = metrics.sharpness(base, "tenengrad", presmooth_frac=0.0)
    cropped = metrics.sharpness(_with_databar(base), "tenengrad", presmooth_frac=0.0)
    assert abs(bare - cropped) / bare < 1e-9


def test_gaussian_blur_reduces_sharpness_and_preserves_shape():
    base = _sharp_image()
    out = metrics.gaussian_blur(base, 2.0)
    assert out.shape == base.shape
    assert metrics.tenengrad(out) < metrics.tenengrad(base)
    assert np.allclose(metrics.gaussian_blur(base, 0.0), base)


def test_presmoothing_lifts_focus_contrast_out_of_noise():
    """Regression test for the real-FIB finding.

    Shot noise adds a focus-independent pedestal to gradient metrics, collapsing
    peak contrast. Low-passing before differentiating must restore it WITHOUT
    moving the peak.
    """
    if not _HAVE_PIL:
        return
    rng = np.random.default_rng(11)
    # Match REAL 2000x FIB statistics: fine-scale texture whose structure std
    # (~9) is comparable to the shot noise (~10), i.e. SNR ~= 1. This is the
    # regime where the noise pedestal actually swamps gradient metrics; the
    # earlier high-contrast blob phantom was far too easy to show the effect.
    # Calibrated against the real Set B frames: this phantom reproduces
    # frame std ~13.9 (real 13.9) and raw peak contrast ~0.083 (real 0.087).
    texture = rng.normal(0.0, 1.0, (192, 192))
    texture = _blur(texture * 40.0 + 128.0, 4.0)          # smooth, low-contrast structure
    texture = (texture - texture.mean())
    texture = texture / texture.std() * 9.2 + 80.0        # structure sd ~9.2, mean ~80
    true_focus, volts = 30.0, [24.0, 27.0, 30.0, 33.0, 36.0]

    def frame(v):
        img = _blur(texture, 0.3 + 0.45 * abs(v - true_focus))
        return np.clip(img + rng.normal(0, 10.4, img.shape), 0, 255)  # real noise sd

    frames = {v: frame(v) for v in volts}

    def contrast(pf):
        sc = [metrics.sharpness(frames[v], "tenengrad", presmooth_frac=pf) for v in volts]
        return (max(sc) - min(sc)) / max(sc), volts[int(np.argmax(sc))]

    c_raw, peak_raw = contrast(0.0)
    c_smooth, peak_smooth = contrast(metrics.DEFAULT_PRESMOOTH_FRAC)
    assert c_smooth > c_raw * 2, f"presmoothing must lift contrast: {c_raw} -> {c_smooth}"
    assert peak_smooth == true_focus, f"presmoothing moved the peak to {peak_smooth}"


# --------------------------- sweep ------------------------------------ #
def test_plan_voltages_basic():
    vs = plan_voltages(30.0, 6.0, 7)
    assert len(vs) == 7
    assert vs[0] == 24.0 and vs[-1] == 36.0
    assert vs == sorted(vs)


def test_plan_voltages_clamps_to_bounds():
    vs = plan_voltages(2.0, 6.0, 7, bounds=(0.0, 60.0))
    assert vs[0] >= 0.0 and vs[-1] <= 60.0
    assert min(vs) == 0.0  # lower edge clamped


def test_parabola_vertex_known():
    # y = -(x-5)^2 + 10 -> vertex at x = 5
    f = lambda x: -((x - 5.0) ** 2) + 10.0
    vx = _parabola_vertex((3.0, f(3)), (4.0, f(4)), (7.0, f(7)))
    assert abs(vx - 5.0) < 1e-6


def test_refine_peak_edge_flagged():
    # Monotonically increasing -> max at the right edge -> not bracketed.
    samples = [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]
    r = refine_peak(samples)
    assert r.bracketed is False
    assert r.best_voltage == 2.0


def test_refine_peak_interior_refines():
    samples = [(0.0, 1.0), (1.0, 4.0), (2.0, 3.0)]  # peak bracketed at v=1
    r = refine_peak(samples)
    assert r.bracketed is True and r.refined is True
    assert 0.0 <= r.estimated_voltage <= 2.0


# --------------------------- full search ------------------------------ #
def test_autofocus_converges_various_offsets():
    """Search should land within ~one coarse step of the true focus.

    Uses the REAL instrument scale (~16.4 kV objective potential, +/-150 V sweep)
    measured from 2000x FIB data, not an arbitrary toy scale.
    """
    if not _HAVE_PIL:
        return  # simulator blur needs Pillow; skip if unavailable
    half_range = 150.0
    coarse = 9
    step = 2 * half_range / (coarse - 1)  # 37.5 V
    for true_focus in (16200.0, 16425.0, 16800.0):
        for offset in (-120.0, -60.0, 80.0, 130.0):
            src = SimulatedImageSource(
                true_focus=true_focus,
                start_voltage=true_focus + offset,
                seed=7,
            )
            res = autofocus(src, half_range=half_range, coarse_points=coarse,
                            metric="tenengrad")
            assert res.success, res.message
            err = abs(res.best_voltage - true_focus)
            assert err <= step, (
                f"true={true_focus} offset={offset} -> {res.best_voltage:.2f} "
                f"(err {err:.2f} > step {step:.2f})"
            )


def test_autofocus_refine_beats_coarse_grid():
    """Parabola refinement should land well inside the coarse grid spacing."""
    if not _HAVE_PIL:
        return
    true_focus = 16437.0
    src = SimulatedImageSource(true_focus=true_focus, start_voltage=16500.0,
                               noise_sigma=2.0, seed=3)
    coarse, half_range = 9, 150.0
    step = 2 * half_range / (coarse - 1)  # 37.5 V
    res = autofocus(src, half_range=half_range, coarse_points=coarse, fine_points=5,
                    metric="tenengrad", refine=True)
    assert res.success and res.refined
    assert abs(res.best_voltage - true_focus) < step / 2


def test_autofocus_low_contrast_flagged_and_restored():
    """A flat/no-signal source -> low confidence, voltage restored to start."""
    class FlatSource(SimulatedImageSource):
        def grab(self):
            # Ignore voltage: return uniform noise -> no focus signal.
            return self._rng.normal(128.0, 1.0, size=(64, 64))

    start = 16500.0
    src = FlatSource(true_focus=16425.0, start_voltage=start, seed=5)
    res = autofocus(src, half_range=150.0, coarse_points=7, metric="tenengrad",
                    min_contrast=0.2, restore_on_low_confidence=True)
    assert res.success is False and res.low_confidence
    assert abs(src._voltage - start) < 1e-9  # returned to where it started


def test_autofocus_respects_frame_budget():
    if not _HAVE_PIL:
        return
    src = SimulatedImageSource(true_focus=16425.0, start_voltage=16500.0, seed=1)
    res = autofocus(src, half_range=150.0, coarse_points=7, fine_points=5,
                    metric="tenengrad", max_frames=7)
    assert res.n_frames <= 7


# --------------------------- runner ----------------------------------- #
# ----------- central-row analysis crop (tilted FIB focus box) ----------- #
def test_crop_central_rows_keeps_middle_band():
    img = np.arange(100).reshape(100, 1) * np.ones((1, 10))
    out = metrics.crop_central_rows(img, 0.5)
    assert out.shape[0] == 50 and out.shape[1] == 10
    # symmetric: rows 25..74 kept
    assert out[0, 0] == 25 and out[-1, 0] == 74


def test_crop_central_rows_noop_at_one():
    img = np.random.RandomState(0).rand(40, 20)
    assert metrics.crop_central_rows(img, 1.0).shape == img.shape


def test_analysis_height_frac_excludes_top_bottom_from_score():
    """A frame sharp only in the CENTRAL band scores higher when we crop to it.

    Emulates a tilted focus box: centre rows in focus (high-frequency texture),
    top and bottom rows blurred (out of focus plane).
    """
    rng = np.random.RandomState(3)
    h, w = 120, 120
    frame = np.zeros((h, w), dtype=np.float64)
    texture = rng.rand(h, w) * 255.0
    # central third is crisp; outer rows are smeared (low frequency)
    band0, band1 = h // 3, 2 * h // 3
    frame[band0:band1] = texture[band0:band1]
    smear = (texture[:band0].mean(axis=0, keepdims=True) * np.ones((band0, 1)))
    frame[:band0] = smear
    frame[band1:] = smear
    full = metrics.sharpness(frame, "tenengrad", crop_bar=False, presmooth_frac=0.0,
                             analysis_height_frac=1.0)
    central = metrics.sharpness(frame, "tenengrad", crop_bar=False, presmooth_frac=0.0,
                                analysis_height_frac=1.0 / 3.0)
    # Scoring only the crisp central band gives a stronger sharpness signal.
    assert central > full, (central, full)


def test_raster_h_scale_shortens_the_box():
    from ..sources import raster_for_side
    tall = raster_for_side("left", (1024, 768), 0.5, h_scale=0.60)
    short = raster_for_side("left", (1024, 768), 0.5, h_scale=0.30)
    # same x/width, shorter height, still vertically centred
    assert short[3] < tall[3]
    assert short[1] > tall[1]              # taller top offset -> centred
    assert short[0] == tall[0] and short[2] == tall[2]


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
        except Exception as e:  # pragma: no cover
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed"
          + ("" if _HAVE_PIL else "  (some skipped: Pillow unavailable)"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
