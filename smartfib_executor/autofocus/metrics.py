"""Focus / sharpness metrics for FIB autofocus.

Each metric maps a single image to a scalar "sharpness" score where **higher =
better focused**. The image sweep in :mod:`.search` grabs one frame per objective
voltage and picks the voltage that maximises the chosen metric.

Design notes
------------
* Pure functions of a numpy array -> float. No hardware, no I/O. This is the layer
  that is fully unit-testable offline (see ``tests/test_autofocus.py``).
* Every score is normalised by pixel count (means, not sums) so scores from
  different ROI sizes are comparable and a metric's magnitude does not depend on
  how big a crop we grabbed.
* Gradients/Laplacians are computed with dependency-free numpy slicing (no scipy),
  so the module only needs numpy.

Available metrics (``METRICS`` keys):
    "tenengrad"            - mean squared Sobel gradient magnitude. Robust default
                             for noisy SEM/FIB images (Sobel smooths across the
                             gradient direction).
    "normalized_variance"  - var(I) / mean(I)^2. Brightness/contrast invariant.
    "laplacian_variance"   - variance of the Laplacian. Sensitive but noisier.
    "brenner"              - mean squared difference of pixels two apart. Cheap.
    "gradient_energy"      - mean squared central-difference gradient. Cheap.

Recommendation: keep several available and, during the first on-instrument runs,
log the full curve for each so we can pick the metric that gives the cleanest,
most unimodal peak on *your* FIB images before committing to a default.

Preprocessing (IMPORTANT -- validated against real 2000x FIB data)
------------------------------------------------------------------
Raw gradient metrics on FIB images are dominated by shot noise: the noise adds a
large, focus-independent constant to every gradient score, so the real focus
signal rides on a huge pedestal and the peak contrast collapses. On real data the
peak contrast was only 0.087 (512px, slow dwell) and 0.021 (1024px, fast dwell)
-- the latter is below any usable confidence threshold.

Low-pass filtering *before* differentiating removes the noise pedestal without
moving the peak. On the same data this raised contrast to 0.45 and 0.32
respectively -- a 5x and 15x improvement -- with the peak location unchanged.

The optimal smoothing scale is proportional to image width (i.e. it is a fixed
*physical* scale on the sample, not a fixed pixel count): the optimum was 0.39%
of width at 512px and ~0.5% at 1024px. Hence ``presmooth_frac`` is expressed as a
fraction of image width and defaults to ``DEFAULT_PRESMOOTH_FRAC``.

Databars: SmartSEM databars are static, high-contrast text. They do not blur with
focus, so they add a constant to the score and dilute the peak. ``crop_databar``
auto-detects the bright separator row and removes the bar.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional

import numpy as np


def to_gray(image: np.ndarray) -> np.ndarray:
    """Return a 2-D float64 grayscale view of ``image``.

    Accepts HxW or HxWxC arrays of any numeric dtype. RGB(A) is collapsed with
    luminosity weights; extra/alpha channels beyond 3 are ignored.
    """
    arr = np.asarray(image)
    if arr.ndim == 2:
        return arr.astype(np.float64, copy=False)
    if arr.ndim == 3:
        c = arr.shape[2]
        if c == 1:
            return arr[:, :, 0].astype(np.float64, copy=False)
        rgb = arr[:, :, :3].astype(np.float64, copy=False)
        w = np.array([0.299, 0.587, 0.114], dtype=np.float64)
        return rgb @ w
    raise ValueError(f"Expected a 2-D or 3-D image, got shape {arr.shape!r}")


def gaussian_blur(image: np.ndarray, sigma: float) -> np.ndarray:
    """Separable Gaussian blur on float data (numpy only, reflected edges).

    Implemented here rather than via PIL so it operates on float64 without a
    lossy uint8 round-trip, and so metrics.py needs nothing but numpy.
    """
    img = to_gray(image)
    if sigma <= 0:
        return img
    radius = max(1, int(round(3.0 * sigma)))
    x = np.arange(-radius, radius + 1, dtype=np.float64)
    k = np.exp(-(x * x) / (2.0 * sigma * sigma))
    k /= k.sum()
    pad = np.pad(img, ((0, 0), (radius, radius)), mode="reflect")
    out = np.apply_along_axis(lambda m: np.convolve(m, k, mode="valid"), 1, pad)
    pad = np.pad(out, ((radius, radius), (0, 0)), mode="reflect")
    out = np.apply_along_axis(lambda m: np.convolve(m, k, mode="valid"), 0, pad)
    return out


def detect_databar_row(image: np.ndarray, min_keep_frac: float = 0.5) -> Optional[int]:
    """Return the first row of the SmartSEM databar, or None if not found.

    SmartSEM renders a near-white separator row (mean ~255) directly above the
    databar. We scan the lower part of the frame for the topmost such row. Only
    rows below ``min_keep_frac`` of the height are considered, so a bright
    feature in the image proper cannot be mistaken for the separator.

    Verified against real 2000x FIB frames: the separator sits at row 348/384 and
    696/768 -- both 90.6% of height -- in every image tested.
    """
    img = to_gray(image)
    h = img.shape[0]
    start = int(h * min_keep_frac)
    row_means = img[start:, :].mean(axis=1)
    bright = np.where(row_means > 250.0)[0]
    if bright.size == 0:
        return None
    return int(start + bright[0])


def crop_databar(image: np.ndarray, min_keep_frac: float = 0.5) -> np.ndarray:
    """Remove the SmartSEM databar if one is detected; otherwise return as-is.

    The databar is static text: it does not blur with focus, so leaving it in
    adds a constant to every sharpness score and dilutes the focus peak.
    """
    img = to_gray(image)
    row = detect_databar_row(img, min_keep_frac=min_keep_frac)
    if row is None or row < 3:
        return img
    return img[:row, :]


# Optimal pre-smoothing scale as a fraction of image width. Measured on real
# 2000x FIB data: optimum 0.39% at 512px and ~0.5% at 1024px -> 0.4% suits both.
DEFAULT_PRESMOOTH_FRAC = 0.004


def preprocess(
    image: np.ndarray,
    *,
    crop_bar: bool = True,
    presmooth_frac: float = DEFAULT_PRESMOOTH_FRAC,
    analysis_height_frac: float = 1.0,
) -> np.ndarray:
    """Standard focus preprocessing: drop the databar, then low-pass filter.

    ``presmooth_frac`` is a fraction of image WIDTH (scale-invariant), so the same
    value behaves consistently across store resolutions.

    ``analysis_height_frac`` (0 < f <= 1) keeps only the central fraction of ROWS
    before scoring. At grazing FIB incidence the focus plane tilts across the box,
    so the top and bottom rows sit at a different defocus than the centre and bias
    the sharpness metric; restricting the metric to the central band removes that
    bias without changing what the beam scans. 1.0 keeps every row (default).
    """
    img = to_gray(image)
    if crop_bar:
        img = crop_databar(img)
    if analysis_height_frac and 0.0 < analysis_height_frac < 1.0:
        img = crop_central_rows(img, analysis_height_frac)
    if presmooth_frac and presmooth_frac > 0:
        sigma = presmooth_frac * img.shape[1]
        if sigma > 0.05:  # skip pointless sub-pixel blurs
            img = gaussian_blur(img, sigma)
    return img


def crop_central_rows(image: np.ndarray, keep_frac: float) -> np.ndarray:
    """Keep the central ``keep_frac`` of rows (symmetric top/bottom crop).

    Used to exclude the out-of-focus top and bottom of a tilted FIB focus box from
    the sharpness metric. Columns are untouched.
    """
    if keep_frac is None or keep_frac >= 1.0 or image.ndim < 1:
        return image
    h = image.shape[0]
    keep = max(1, int(round(h * float(keep_frac))))
    if keep >= h:
        return image
    top = (h - keep) // 2
    return image[top:top + keep, ...]


def _require_min_size(img: np.ndarray, n: int = 3) -> None:
    if img.ndim != 2 or img.shape[0] < n or img.shape[1] < n:
        raise ValueError(
            f"Image too small for metric: need at least {n}x{n}, got {img.shape!r}"
        )


def _sobel(img: np.ndarray):
    """Return (gx, gy) Sobel responses over the valid interior (H-2, W-2)."""
    # Columns for gx: right minus left, weighted 1-2-1 down the rows.
    left = img[:-2, :-2] + 2.0 * img[1:-1, :-2] + img[2:, :-2]
    right = img[:-2, 2:] + 2.0 * img[1:-1, 2:] + img[2:, 2:]
    gx = right - left
    # Rows for gy: bottom minus top, weighted 1-2-1 across the columns.
    top = img[:-2, :-2] + 2.0 * img[:-2, 1:-1] + img[:-2, 2:]
    bottom = img[2:, :-2] + 2.0 * img[2:, 1:-1] + img[2:, 2:]
    gy = bottom - top
    return gx, gy


def tenengrad(image: np.ndarray) -> float:
    """Mean squared Sobel gradient magnitude. Robust default for FIB/SEM."""
    img = to_gray(image)
    _require_min_size(img)
    gx, gy = _sobel(img)
    return float(np.mean(gx * gx + gy * gy))


def gradient_energy(image: np.ndarray) -> float:
    """Mean squared central-difference gradient (cheaper, less smoothing)."""
    img = to_gray(image)
    _require_min_size(img)
    gx = img[:, 2:] - img[:, :-2]
    gy = img[2:, :] - img[:-2, :]
    # Overlap the valid interior region so gx and gy share a shape.
    gx = gx[1:-1, :]
    gy = gy[:, 1:-1]
    return float(np.mean(gx * gx) + np.mean(gy * gy))


def laplacian_variance(image: np.ndarray) -> float:
    """Variance of the 4-neighbour Laplacian. Sensitive but noise-prone."""
    img = to_gray(image)
    _require_min_size(img)
    lap = (
        img[:-2, 1:-1]
        + img[2:, 1:-1]
        + img[1:-1, :-2]
        + img[1:-1, 2:]
        - 4.0 * img[1:-1, 1:-1]
    )
    return float(np.var(lap))


def brenner(image: np.ndarray) -> float:
    """Mean squared difference of pixels two apart (horizontal + vertical)."""
    img = to_gray(image)
    _require_min_size(img)
    dh = img[:, 2:] - img[:, :-2]
    dv = img[2:, :] - img[:-2, :]
    return float(np.mean(dh * dh) + np.mean(dv * dv))


def normalized_variance(image: np.ndarray) -> float:
    """var(I) / mean(I)^2 -- invariant to global brightness/contrast scaling."""
    img = to_gray(image)
    mean = float(np.mean(img))
    if abs(mean) < 1e-9:
        return 0.0
    return float(np.var(img) / (mean * mean))


METRICS: Dict[str, Callable[[np.ndarray], float]] = {
    "tenengrad": tenengrad,
    "gradient_energy": gradient_energy,
    "laplacian_variance": laplacian_variance,
    "brenner": brenner,
    "normalized_variance": normalized_variance,
}

DEFAULT_METRIC = "tenengrad"


def sharpness(
    image: np.ndarray,
    method: str = DEFAULT_METRIC,
    *,
    prepared: bool = False,
    crop_bar: bool = True,
    presmooth_frac: float = DEFAULT_PRESMOOTH_FRAC,
    analysis_height_frac: float = 1.0,
) -> float:
    """Compute a single sharpness score.

    By default the image is preprocessed first (databar removed, optional central-
    row crop, then low-pass filtered) -- see the module docstring for why this
    matters on noisy FIB data. Pass ``prepared=True`` if you have already called
    :func:`preprocess`.
    """
    try:
        fn = METRICS[method]
    except KeyError:
        raise ValueError(
            f"Unknown focus metric {method!r}. Available: {sorted(METRICS)}"
        )
    img = image if prepared else preprocess(
        image, crop_bar=crop_bar, presmooth_frac=presmooth_frac,
        analysis_height_frac=analysis_height_frac,
    )
    return fn(img)


def all_scores(
    image: np.ndarray,
    *,
    prepared: bool = False,
    crop_bar: bool = True,
    presmooth_frac: float = DEFAULT_PRESMOOTH_FRAC,
) -> Dict[str, float]:
    """Compute every metric for one image (useful when comparing metrics)."""
    img = image if prepared else preprocess(
        image, crop_bar=crop_bar, presmooth_frac=presmooth_frac
    )
    return {name: fn(img) for name, fn in METRICS.items()}
