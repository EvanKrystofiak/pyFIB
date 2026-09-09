"""Geometry helpers for authoring patterns.

SmartFIB has no rounded-rectangle primitive, so a rounded box is expressed as an
ordinary POLYGON whose two corners on the chosen side are replaced by
quarter-circle fillets sampled at several points. The result is a plain point
list -- strictly FIB-legal, with no nonstandard attributes -- that round-trips
through elymaker like any other polygon.

Coordinates are layout micrometres (y-up), matching the rest of elymaker. The
input box is given the same way a RECT is (anchor + signed width/height); the
function normalises it to axis-aligned corners internally.
"""

from __future__ import annotations

import math
from typing import List, Tuple

SIDES = ("top", "bottom", "left", "right")

# which two corners (by name) get rounded for each side, in a y-up frame
_ROUNDED_CORNERS = {
    "top": {"TL", "TR"},
    "bottom": {"BL", "BR"},
    "left": {"BL", "TL"},
    "right": {"BR", "TR"},
}


def max_radius(width: float, height: float, side: str) -> float:
    """Largest fillet radius that fits without the two fillets overlapping."""
    w, h = abs(width), abs(height)
    if side in ("top", "bottom"):
        return min(w / 2.0, h)
    return min(h / 2.0, w)


def default_radius(width: float, height: float) -> float:
    """Default fillet radius: 10% of the shorter box dimension."""
    return 0.1 * min(abs(width), abs(height))


def _fillet_points(C, P, N, r: float, segments: int) -> List[Tuple[float, float]]:
    """Quarter-circle replacing corner C, arriving from P and leaving toward N."""
    cx0, cy0 = C

    def unit(to):
        dx, dy = to[0] - cx0, to[1] - cy0
        L = math.hypot(dx, dy) or 1.0
        return dx / L, dy / L

    upx, upy = unit(P)          # toward previous vertex
    unx, uny = unit(N)          # toward next vertex
    t_in = (cx0 + r * upx, cy0 + r * upy)     # tangent on incoming edge
    t_out = (cx0 + r * unx, cy0 + r * uny)    # tangent on outgoing edge
    cx = cx0 + r * upx + r * unx              # arc centre (inside the corner)
    cy = cy0 + r * upy + r * uny
    a_in = math.atan2(t_in[1] - cy, t_in[0] - cx)
    a_out = math.atan2(t_out[1] - cy, t_out[0] - cx)
    da = a_out - a_in
    while da > math.pi:
        da -= 2 * math.pi
    while da < -math.pi:
        da += 2 * math.pi
    pts = []
    for k in range(segments):
        t = k / (segments - 1) if segments > 1 else 0.0
        ang = a_in + da * t
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    return pts


def rounded_rect_polygon(x: float, y: float, width: float, height: float,
                         radius: float, side: str = "top",
                         segments: int = 12) -> List[Tuple[float, float]]:
    """Return CCW layout-um points for a box with two rounded corners on *side*.

    Parameters mirror a RECT: ``(x, y)`` is the anchor corner and ``width`` /
    ``height`` are signed. ``radius`` is clamped to :func:`max_radius`; a radius
    <= 0 (or ``segments`` < 1) yields the plain 4-point rectangle.
    """
    if side not in SIDES:
        raise ValueError("side must be one of %r" % (SIDES,))
    x0, x1 = sorted((x, x + width))
    y0, y1 = sorted((y, y + height))          # y0 bottom, y1 top (y-up)
    BL, BR, TR, TL = (x0, y0), (x1, y0), (x1, y1), (x0, y1)
    order = [("BL", BL), ("BR", BR), ("TR", TR), ("TL", TL)]   # CCW

    r = max(0.0, min(radius, max_radius(width, height, side)))
    if r <= 0.0 or segments < 1:
        return [BL, BR, TR, TL]

    rounded = _ROUNDED_CORNERS[side]
    pts: List[Tuple[float, float]] = []
    n = len(order)
    for i, (name, C) in enumerate(order):
        if name in rounded:
            P = order[(i - 1) % n][1]
            N = order[(i + 1) % n][1]
            pts.extend(_fillet_points(C, P, N, r, segments))
        else:
            pts.append(C)
    return pts
