"""Draw an .ely pattern on a position's FIB image (report only).

Nothing here talks to the microscope. It reads the .ely with elymaker's own reader
and maps it with elymaker's own image calibration, so a pattern lands on the report
image exactly where the operator saw it while authoring:

    layout (um, y up, origin = FOV centre)  ->  image pixels (origin top-left, y down)
        px = W/2 + x / ps          py = H/2 - y / ps          (ps = um per pixel)

    RECT: (x, y) is the ANCHOR corner. In elymaker's y-down scene the box spans
    local (0, 0)..(w, -h) and is rotated by angle_deg about the anchor with Qt's
    rotation (x' = x cos a - y sin a, y' = x sin a + y cos a).
    POLYGON: points as written.  ARC: the band elymaker draws (r1 from a1 to a2,
    then r2 back).  TEXT: centred on its anchor, as elymaker shows it.

Checked on the instrument (2026-10-01 session, v0.39.0 captures): the numbered
fiducial .ely files drawn this way sit on the milled "6" and "7" in the position_6
and position_7 reference images. That data is a regression test
(report/tests/test_overlay.py).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

Point = Tuple[float, float]


@dataclass
class OverlayShape:
    kind: str                         # "polygon" | "text"
    points_px: List[Point]            # outline in image pixels (text: [anchor])
    depth_um: float
    dose_factor: float
    probe: Optional[str] = None       # e.g. "30kV:300pA" (None if the file has none)
    current_A: Optional[float] = None  # probe current (A); picks the overlay colour
    text: str = ""
    text_height_px: float = 0.0


@dataclass
class Overlay:
    source: Optional[Path]
    shapes: List[OverlayShape] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    width_px: int = 0
    height_px: int = 0

    @property
    def ok(self) -> bool:
        return bool(self.shapes)

    def bbox_px(self) -> Optional[Tuple[float, float, float, float]]:
        xs = [p[0] for s in self.shapes for p in s.points_px]
        ys = [p[1] for s in self.shapes for p in s.points_px]
        if not xs:
            return None
        return (min(xs), min(ys), max(xs), max(ys))

    def depths_um(self) -> List[float]:
        return sorted({round(s.depth_um, 3) for s in self.shapes})

    def probes(self) -> List[str]:
        return sorted({s.probe for s in self.shapes if s.probe})


def layout_to_pixel(x_um: float, y_um: float, pixel_size_um: float,
                    width_px: int, height_px: int) -> Point:
    """Layout um (y up, FOV-centred) -> image pixel (top-left origin, y down).

    Delegates to elymaker's ImageCalibration so there is one definition of the map."""
    from elymaker.gui.coords import ImageCalibration
    cal = ImageCalibration(pixel_size_um=float(pixel_size_um),
                           image_w_px=int(width_px), image_h_px=int(height_px))
    return cal.layout_to_pixel(float(x_um), float(y_um))


def rect_corners_layout(x: float, y: float, w: float, h: float,
                        angle_deg: float) -> List[Point]:
    """The four corners of an .ely RECT in layout um (y up), elymaker's convention."""
    a = math.radians(float(angle_deg))
    ca, sa = math.cos(a), math.sin(a)
    sx0, sy0 = x, -y                                  # anchor in scene (y down)
    out = []
    for lx, ly in ((0.0, 0.0), (w, 0.0), (w, -h), (0.0, -h)):   # local scene coords
        rx = lx * ca - ly * sa
        ry = lx * sa + ly * ca
        out.append((sx0 + rx, -(sy0 + ry)))          # back to layout (y up)
    return out


def _arc_points_layout(cx, cy, r1, r2, a1, a2) -> List[Point]:
    n = max(6, int(abs(a2 - a1) / 4))
    pts = []
    for k in range(n + 1):
        ang = math.radians(a1 + (a2 - a1) * k / n)
        pts.append((cx + r1 * math.cos(ang), cy + r1 * math.sin(ang)))
    for k in range(n + 1):
        ang = math.radians(a2 + (a1 - a2) * k / n)
        pts.append((cx + r2 * math.cos(ang), cy + r2 * math.sin(ang)))
    return pts


def _probe_obj(shape, layers):
    exp = getattr(shape, "exposure", None)
    probe = getattr(exp, "probe", None) if exp is not None else None
    if probe is None:
        for lay in layers or []:
            lexp = getattr(lay, "exposure", None)
            if lexp is not None and getattr(lexp, "probe", None) is not None:
                return lexp.probe
    return probe


def _probe_name(shape, layers) -> Optional[str]:
    exp = getattr(shape, "exposure", None)
    probe = getattr(exp, "probe", None) if exp is not None else None
    if probe is None:
        for lay in layers or []:
            lexp = getattr(lay, "exposure", None)
            if lexp is not None and getattr(lexp, "probe", None) is not None:
                probe = lexp.probe
                break
    return getattr(probe, "name", None) if probe is not None else None


def layout_overlay(layout, pixel_size_um: float, width_px: int, height_px: int,
                   source: Optional[Path] = None) -> Overlay:
    """Convert every shape in an elymaker Layout to image-pixel outlines."""
    from elymaker.model import Arc, Polygon, Rect, Text

    ov = Overlay(source=source, width_px=int(width_px), height_px=int(height_px))
    if pixel_size_um is None or pixel_size_um <= 0:
        ov.warnings.append("no pixel size recorded for this image; pattern not drawn")
        return ov

    def to_px(pts):
        return [layout_to_pixel(px, py, pixel_size_um, width_px, height_px)
                for px, py in pts]

    skipped = 0
    for _st, lref, sh in layout.iter_shapes():
        if abs(getattr(lref, "frame_cx", 0.0)) > 1e-9 or abs(getattr(lref, "frame_cy", 0.0)) > 1e-9:
            msg = (f"layer frame centre is ({lref.frame_cx:g}, {lref.frame_cy:g}) um, not 0: "
                   f"shapes are drawn at their file coordinates")
            if msg not in ov.warnings:
                ov.warnings.append(msg)
        _pr = _probe_obj(sh, layout.layers)
        common = dict(depth_um=float(sh.depth_um), dose_factor=float(sh.dose_factor),
                      probe=_probe_name(sh, layout.layers),
                      current_A=getattr(_pr, "current_A", None) if _pr is not None else None)
        if getattr(sh, "ignored", False):
            skipped += 1
            continue
        if isinstance(sh, Rect):
            if getattr(sh, "imaging", False):
                skipped += 1
                continue
            pts = rect_corners_layout(sh.x, sh.y, sh.width, sh.height, sh.angle_deg)
            ov.shapes.append(OverlayShape(kind="polygon", points_px=to_px(pts), **common))
        elif isinstance(sh, Polygon):
            ov.shapes.append(OverlayShape(kind="polygon", points_px=to_px(sh.points), **common))
        elif isinstance(sh, Arc):
            pts = _arc_points_layout(sh.cx, sh.cy, sh.r1, sh.r2, sh.a1_deg, sh.a2_deg)
            ov.shapes.append(OverlayShape(kind="polygon", points_px=to_px(pts), **common))
        elif isinstance(sh, Text):
            anchor = to_px([(sh.x, sh.y)])
            ov.shapes.append(OverlayShape(kind="text", points_px=anchor, text=sh.text,
                                          text_height_px=abs(sh.height) / pixel_size_um,
                                          **common))
            if "text is drawn as elymaker shows it (approximate)" not in ov.warnings:
                ov.warnings.append("text is drawn as elymaker shows it (approximate)")
        else:
            skipped += 1
    if skipped:
        ov.warnings.append(f"{skipped} shape(s) not drawn (imaging/ignored/unknown)")
    bb = ov.bbox_px()
    if bb is not None:
        x0, y0, x1, y1 = bb
        if x0 < 0 or y0 < 0 or x1 > width_px or y1 > height_px:
            ov.warnings.append("part of the pattern lies outside the image")
    return ov


def load_overlay(ely_path, pixel_size_um: float, width_px: int, height_px: int) -> Overlay:
    """Read an .ely and convert it. Never raises: problems come back as warnings."""
    p = Path(ely_path)
    if not p.exists():
        return Overlay(source=p, width_px=width_px, height_px=height_px,
                       warnings=[f"pattern file not found: {p}"])
    try:
        from elymaker.reader import read_ely
        layout = read_ely(str(p))
    except Exception as e:  # noqa: BLE001
        return Overlay(source=p, width_px=width_px, height_px=height_px,
                       warnings=[f"could not read {p.name}: {e}"])
    return layout_overlay(layout, pixel_size_um, width_px, height_px, source=p)
