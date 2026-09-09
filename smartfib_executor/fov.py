"""Shared field-of-view / pixel-size geometry (single source of truth).

Both the core (positions capture) and the refine fork use these, so the
magnification-reference constant lives in ONE place. Previously it lived only in
refine/geometry.py; core must not import a fork, so it is lifted here and the refine
fork now re-uses it.

MAG_REF_WIDTH_M is measured, not derived: from real 2000x data the Image Pixel Size
was 55.82 nm over 1024 px (FOV width ~57.2 um), confirmed from the 10 um scale bar at
two store resolutions -- agreement to ~1.2%. When the AP_WIDTH improvement lands, the
live field-of-view read will replace this constant here, and everything that imports
it moves together.
"""

from __future__ import annotations

MAG_REF_WIDTH_M = 0.1143   # metres * magnification (FOV width = MAG_REF_WIDTH_M / mag)


def fov_width_m(magnification: float) -> float:
    """Field-of-view WIDTH in metres at a given magnification."""
    mag = float(magnification)
    if mag <= 0:
        raise ValueError("magnification must be > 0")
    return MAG_REF_WIDTH_M / mag


def pixel_size_m(magnification: float, image_width_px: int) -> float:
    """Sample-plane size of one pixel (metres), self-consistent with the image width.

    Deriving pixel size from the ACTUAL grabbed image width (not the store grid) is
    what keeps it correct regardless of store resolution -- see the v0.9.1 refine fix.
    """
    if image_width_px <= 0:
        raise ValueError("image_width_px must be > 0")
    return fov_width_m(magnification) / float(image_width_px)
