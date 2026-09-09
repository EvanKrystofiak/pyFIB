"""Non-blocking validation of an ELAYOUT before export.

Returns a list of human-readable issues. Nothing here mutates the layout or
prevents export -- the operator reviews the pattern in SmartFIB before milling.
The point is to catch the cheap mistakes that a pattern maker can introduce:
degenerate geometry, dose/depth inconsistency, shapes outside the field of
view, missing recipe/probe, unusual signs.

Severity is encoded as a prefix in each message: "ERROR" / "WARN" / "INFO".
"""

from __future__ import annotations

import math
from typing import List

from .model import Arc, Layout, Polygon, Rect, Shape, Text, DOSE_TO_METRE

_Z_TOL = 1e-12   # metres; tolerance on z_extent == dose_factor*1e-6


def _shape_label(idx: int, sh: Shape) -> str:
    return "%s#%d" % (type(sh).__name__, idx)


def _extent(sh: Shape):
    """Return (xmin, xmax, ymin, ymax) in um, or None if not applicable.

    Rect extent ignores rotation (axis-aligned bound about its anchor); good
    enough for a coarse field-of-view check.
    """
    if isinstance(sh, Rect):
        hw, hh = abs(sh.width) / 2.0, abs(sh.height) / 2.0
        return (sh.x - hw, sh.x + hw, sh.y - hh, sh.y + hh)
    if isinstance(sh, Polygon):
        if not sh.points:
            return None
        xs = [p[0] for p in sh.points]
        ys = [p[1] for p in sh.points]
        return (min(xs), max(xs), min(ys), max(ys))
    if isinstance(sh, Arc):
        r = max(abs(sh.r1), abs(sh.r2))
        return (sh.cx - r, sh.cx + r, sh.cy - r, sh.cy + r)
    if isinstance(sh, Text):
        return (sh.x, sh.x + max(1.0, len(sh.text)) * sh.height * 0.6,
                sh.y, sh.y + abs(sh.height))
    return None


def validate(layout: Layout) -> List[str]:
    issues: List[str] = []

    if not layout.structures:
        issues.append("WARN: layout has no structures (nothing to mill)")

    total = layout.shape_count()
    if total == 0:
        issues.append("WARN: layout contains no shapes")

    idx = 0
    for _st, lref, sh in layout.iter_shapes():
        idx += 1
        lbl = _shape_label(idx, sh)

        # dose / depth coupling
        expected_z = sh.dose_factor * DOSE_TO_METRE
        if abs(sh.z_extent_m - expected_z) > _Z_TOL:
            issues.append(
                "WARN: %s dose_factor=%g implies z_extent=%g m but file has %g m"
                % (lbl, sh.dose_factor, expected_z, sh.z_extent_m)
            )
        if sh.dose_factor <= 0 or sh.z_extent_m <= 0:
            issues.append(
                "ERROR: %s non-positive depth (dose_factor=%g, z_extent=%g m)"
                % (lbl, sh.dose_factor, sh.z_extent_m)
            )

        # recipe / probe presence (ignored shapes may legitimately lack a probe)
        if sh.exposure is None:
            issues.append("WARN: %s has no EXPOSURE (recipe) block" % lbl)
        else:
            if not sh.exposure.file:
                issues.append("WARN: %s EXPOSURE has empty recipe path" % lbl)
            if sh.exposure.probe is None and not getattr(sh, "ignored", False):
                issues.append("INFO: %s has no PROBE (uses recipe default)" % lbl)

        # per-shape geometry
        if isinstance(sh, Rect):
            if sh.width == 0 or sh.height == 0:
                issues.append("ERROR: %s degenerate (width=%g height=%g)"
                              % (lbl, sh.width, sh.height))
            if sh.width < 0:
                issues.append("INFO: %s has negative width=%g" % (lbl, sh.width))
        elif isinstance(sh, Polygon):
            if len(sh.points) < 3:
                issues.append("ERROR: %s has %d points (need >= 3)"
                              % (lbl, len(sh.points)))
        elif isinstance(sh, Arc):
            if sh.r1 <= 0 or sh.r2 < 0:
                issues.append("ERROR: %s bad radii (r1=%g r2=%g)"
                              % (lbl, sh.r1, sh.r2))
            if abs(sh.a2_deg - sh.a1_deg) < 1e-9:
                issues.append("ERROR: %s zero angular sweep (a1=a2=%g deg)"
                              % (lbl, sh.a1_deg))
        elif isinstance(sh, Text):
            if not sh.text:
                issues.append("WARN: %s empty text string" % lbl)
            if sh.height <= 0:
                issues.append("ERROR: %s non-positive text height=%g"
                              % (lbl, sh.height))

        # field-of-view containment (coarse, warning only)
        fov = lref.frame_size
        if fov and fov > 0:
            ext = _extent(sh)
            if ext is not None:
                half = fov / 2.0
                cx, cy = lref.frame_cx, lref.frame_cy
                xmin, xmax, ymin, ymax = ext
                if (xmin < cx - half or xmax > cx + half
                        or ymin < cy - half or ymax > cy + half):
                    issues.append(
                        "WARN: %s extends outside field of view "
                        "(+/-%g um about %g,%g)" % (lbl, half, cx, cy)
                    )

    return issues
