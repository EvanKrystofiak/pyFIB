"""Image registration for position refinement (pure numpy).

Cross-correlates the reference BMP captured by positions.py with a fresh FIB image
grabbed at the SAME magnification after a stage recall, and returns the sub-pixel
translation between them. Pure functions of numpy arrays -- no hardware, no COM --
so this whole layer is unit-testable offline.

Convention (pinned by tests, not assumed):
    phase_cross_correlation(reference, moving) returns (dy, dx) such that
        moving ~= numpy.roll(reference, shift=(dy, dx), axis=(0, 1))
    i.e. (dy, dx) is how far the CONTENT of `moving` sits shifted from `reference`
    (down = +dy, right = +dx). test_registration.py locks this with known rolls.

Preprocessing matters on FIB frames:
  * the SmartSEM databar is static, high-contrast, and identical in position across
    frames, so it would pin the correlation at zero shift -- it is cropped out;
  * a Hann window suppresses FFT edge-wrap artefacts;
  * the mean is removed so uniform brightness differences do not dominate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np


# --------------------------------------------------------------------------- #
# Image preprocessing (isolated copy -- kept independent of the autofocus fork)
# --------------------------------------------------------------------------- #
def to_gray(image: np.ndarray) -> np.ndarray:
    arr = np.asarray(image)
    if arr.ndim == 2:
        return arr.astype(np.float64, copy=False)
    if arr.ndim == 3:
        if arr.shape[2] == 1:
            return arr[:, :, 0].astype(np.float64, copy=False)
        rgb = arr[:, :, :3].astype(np.float64, copy=False)
        return rgb @ np.array([0.299, 0.587, 0.114])
    raise ValueError(f"expected 2-D or 3-D image, got shape {arr.shape!r}")


def detect_databar_row(image: np.ndarray, min_keep_frac: float = 0.5) -> Optional[int]:
    """First row of the SmartSEM databar (bright separator), or None."""
    img = to_gray(image)
    start = int(img.shape[0] * min_keep_frac)
    row_means = img[start:, :].mean(axis=1)
    bright = np.where(row_means > 250.0)[0]
    if bright.size == 0:
        return None
    return int(start + bright[0])


def crop_databar(image: np.ndarray, min_keep_frac: float = 0.5) -> np.ndarray:
    img = to_gray(image)
    row = detect_databar_row(img, min_keep_frac=min_keep_frac)
    if row is None or row < 3:
        return img
    return img[:row, :]


def _hann2d(shape: Tuple[int, int]) -> np.ndarray:
    h, w = shape
    wy = np.hanning(h) if h > 1 else np.ones(1)
    wx = np.hanning(w) if w > 1 else np.ones(1)
    return np.outer(wy, wx)


def _match_shapes(a: np.ndarray, b: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Center-crop both arrays to their common (min) height and width."""
    h = min(a.shape[0], b.shape[0])
    w = min(a.shape[1], b.shape[1])

    def crop(x):
        y0 = (x.shape[0] - h) // 2
        x0 = (x.shape[1] - w) // 2
        return x[y0:y0 + h, x0:x0 + w]

    return crop(a), crop(b)


def preprocess(image: np.ndarray, *, crop_bar: bool = True,
               window: bool = True) -> np.ndarray:
    img = to_gray(image)
    if crop_bar:
        img = crop_databar(img)
    img = img - float(img.mean())
    if window:
        img = img * _hann2d(img.shape)
    return img


# --------------------------------------------------------------------------- #
# Phase cross-correlation
# --------------------------------------------------------------------------- #
@dataclass
class RegistrationResult:
    dy_px: float           # moving is shifted +dy down from reference
    dx_px: float           # moving is shifted +dx right from reference
    peak: float            # normalised correlation peak height (0..1)
    snr: float             # (peak - mean) / std of the correlation surface
    shape: Tuple[int, int] # size the correlation was computed on (after cropping)


def _parabolic_offset(v_minus: float, v0: float, v_plus: float) -> float:
    """Sub-sample peak offset in [-0.5, 0.5] from three samples around a max."""
    denom = (v_minus - 2.0 * v0 + v_plus)
    if abs(denom) < 1e-12:
        return 0.0
    off = 0.5 * (v_minus - v_plus) / denom
    return float(np.clip(off, -0.5, 0.5))


def phase_cross_correlation(
    reference: np.ndarray,
    moving: np.ndarray,
    *,
    crop_bar: bool = True,
    window: bool = True,
    subpixel: bool = True,
) -> RegistrationResult:
    """Return the sub-pixel (dy, dx) translation of `moving` vs `reference`."""
    a = preprocess(reference, crop_bar=crop_bar, window=window)
    b = preprocess(moving, crop_bar=crop_bar, window=window)
    a, b = _match_shapes(a, b)
    rows, cols = a.shape
    if rows < 4 or cols < 4:
        raise ValueError(f"images too small to register: {a.shape!r}")

    A = np.fft.fft2(a)
    B = np.fft.fft2(b)
    # conj(A)*B gives a peak at +shift when `moving` is `reference` rolled by
    # +shift (verified in test_registration_sign). A*conj(B) yields the negative
    # convention, which is a classic phase-correlation sign trap.
    cross = np.conj(A) * B
    mag = np.abs(cross)
    mag[mag < 1e-12] = 1e-12
    r = np.fft.ifft2(cross / mag).real  # phase correlation surface

    py, px = np.unravel_index(int(np.argmax(r)), r.shape)
    peak = float(r[py, px])
    snr = (peak - float(r.mean())) / (float(r.std()) + 1e-12)

    # Sub-pixel refinement (parabolic) using wrap-around neighbours.
    ddy = ddx = 0.0
    if subpixel:
        ym, yp = (py - 1) % rows, (py + 1) % rows
        xm, xp = (px - 1) % cols, (px + 1) % cols
        ddy = _parabolic_offset(r[ym, px], peak, r[yp, px])
        ddx = _parabolic_offset(r[py, xm], peak, r[py, xp])

    # Map the peak index to a signed shift.
    dy = py + ddy
    dx = px + ddx
    if dy > rows / 2.0:
        dy -= rows
    if dx > cols / 2.0:
        dx -= cols

    return RegistrationResult(dy_px=float(dy), dx_px=float(dx),
                              peak=peak, snr=float(snr), shape=(rows, cols))
