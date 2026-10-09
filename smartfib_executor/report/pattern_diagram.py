"""Dimensioned drawing of an .ely pattern for the report (v0.42.0).

Drawn to scale in layout micrometres (+Y up, as elymaker shows it), with:
  * left:   probe current and mill depth of each part, with a leader line;
  * top:    widths (nested dimension lines);
  * right:  heights (one dimension line per part);
  * centre: the gap between the innermost top and bottom shapes (the lamella).
Lamella patterns are mirror-symmetric about y = 0, so a top/bottom pair is ONE part:
its current/depth and width are labelled on the top shape and its height on the
bottom shape, which keeps the drawing uncluttered (Evan, 2026-10-03).

Widths and heights are the shape's own: |width| x |height| for a RECT, the bounding
box for a polygon or arc. Reads the file only.
"""

from __future__ import annotations

import html
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

Point = Tuple[float, float]
TOL_UM = 1e-3


@dataclass
class PatShape:
    pts: List[Point]
    x0: float
    y0: float
    x1: float
    y1: float
    width_um: float
    height_um: float
    depth_um: float
    dose: float
    probe: Optional[str]
    current_A: Optional[float]
    recipe: Optional[str]

    @property
    def cy(self) -> float:
        return 0.5 * (self.y0 + self.y1)


@dataclass
class Part:
    shapes: List[PatShape]
    mirrored: bool

    @property
    def rep(self) -> PatShape:
        return self.shapes[0]

    @property
    def top(self) -> PatShape:
        return max(self.shapes, key=lambda s: s.y1)

    @property
    def bottom(self) -> PatShape:
        return min(self.shapes, key=lambda s: s.y0)


@dataclass
class PatternInfo:
    name: str
    parts: List[Part] = field(default_factory=list)
    bbox: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    gap_um: Optional[float] = None
    gap_y: Tuple[float, float] = (0.0, 0.0)          # (bottom of gap, top of gap)
    gap_x0: float = 0.0
    warnings: List[str] = field(default_factory=list)


def fmt_current(a: Optional[float]) -> str:
    if a is None or a <= 0:
        return "current not set"
    if a >= 1e-9:
        return f"{a * 1e9:.3g} nA"
    return f"{a * 1e12:.3g} pA"


def fmt_um(v: float) -> str:
    s = f"{abs(v):.3f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _probe(shape, layers):
    exp = getattr(shape, "exposure", None)
    pr = getattr(exp, "probe", None) if exp is not None else None
    if pr is None:
        for lay in layers or []:
            lexp = getattr(lay, "exposure", None)
            if lexp is not None and getattr(lexp, "probe", None) is not None:
                pr, exp = lexp.probe, lexp
                break
    recipe = getattr(exp, "file", None) if exp is not None else None
    return (getattr(pr, "name", None), getattr(pr, "current_A", None),
            os.path.basename(str(recipe).replace("\\", "/")) if recipe else None)


def analyze_layout(layout, name: str = "pattern") -> PatternInfo:
    """Shapes -> parts (mirror pairs merged), overall bbox and the centre gap."""
    from elymaker.model import Arc, Polygon, Rect, Text
    from .overlay import rect_corners_layout, _arc_points_layout
    info = PatternInfo(name=name)
    shapes: List[PatShape] = []
    for _st, _lr, sh in layout.iter_shapes():
        if getattr(sh, "ignored", False) or (isinstance(sh, Rect) and getattr(sh, "imaging", False)):
            continue
        if isinstance(sh, Rect):
            pts = rect_corners_layout(sh.x, sh.y, sh.width, sh.height, sh.angle_deg)
            w, h = abs(sh.width), abs(sh.height)
        elif isinstance(sh, Polygon):
            pts = list(sh.points)
            w = h = None
        elif isinstance(sh, Arc):
            pts = _arc_points_layout(sh.cx, sh.cy, sh.r1, sh.r2, sh.a1_deg, sh.a2_deg)
            w = h = None
        elif isinstance(sh, Text):
            info.warnings.append(f"text shape '{sh.text}' not drawn")
            continue
        else:
            continue
        if not pts:
            continue
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        pname, cur, recipe = _probe(sh, layout.layers)
        shapes.append(PatShape(pts=pts, x0=x0, y0=y0, x1=x1, y1=y1,
                               width_um=w if w is not None else x1 - x0,
                               height_um=h if h is not None else y1 - y0,
                               depth_um=float(sh.depth_um), dose=float(sh.dose_factor),
                               probe=pname, current_A=cur, recipe=recipe))
    if not shapes:
        info.warnings.append("no millable shapes in the pattern")
        return info

    def key(s):
        return (s.probe, round(s.current_A or 0.0, 15), round(s.depth_um, 3),
                round(s.width_um, 3), round(s.height_um, 3))

    used = set()
    parts: List[Part] = []
    for i, s in enumerate(shapes):
        if i in used:
            continue
        used.add(i)
        mate = None
        for j in range(i + 1, len(shapes)):
            t = shapes[j]
            if j in used or key(t) != key(s):
                continue
            if (abs(s.x0 - t.x0) < TOL_UM and abs(s.x1 - t.x1) < TOL_UM
                    and abs(s.y0 + t.y1) < TOL_UM and abs(s.y1 + t.y0) < TOL_UM
                    and abs(s.cy) > TOL_UM):
                mate = j
                break
        if mate is not None:
            used.add(mate)
            parts.append(Part(shapes=[s, shapes[mate]], mirrored=True))
        else:
            parts.append(Part(shapes=[s], mirrored=False))
    parts.sort(key=lambda p: (-(p.rep.current_A or 0.0), -max(abs(p.top.y1), abs(p.bottom.y0))))
    info.parts = parts
    info.bbox = (min(s.x0 for s in shapes), min(s.y0 for s in shapes),
                 max(s.x1 for s in shapes), max(s.y1 for s in shapes))
    # The gap at the centre line: between shapes wholly above and wholly below y = 0
    # that span x = 0 (the lamella for a lamella pattern).
    above = [s for s in shapes if s.y0 >= -TOL_UM and s.x0 <= 0 <= s.x1]
    below = [s for s in shapes if s.y1 <= TOL_UM and s.x0 <= 0 <= s.x1]
    if above and below:
        top_edge = min(s.y0 for s in above)
        bot_edge = max(s.y1 for s in below)
        if top_edge - bot_edge > TOL_UM:
            info.gap_um = top_edge - bot_edge
            info.gap_y = (bot_edge, top_edge)
            inner = [s for s in above if abs(s.y0 - top_edge) < TOL_UM] + \
                    [s for s in below if abs(s.y1 - bot_edge) < TOL_UM]
            info.gap_x0 = min(s.x0 for s in inner)
    return info


def _spread(ys: List[float], gap: float) -> List[float]:
    """Push label y positions apart (keeping order) so they are >= gap apart."""
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    out = list(ys)
    for k in range(1, len(order)):
        a, b = order[k - 1], order[k]
        if out[b] < out[a] + gap:
            out[b] = out[a] + gap
    return out


# One colour per probe current, shared by the position overlays and this drawing
# (v0.42.1). Okabe-Ito colours, colour-blind safe, bright enough on grey FIB images;
# assigned in order of current (lowest first). CSS: .c0 ... .c5, .cn (no current).
CURRENT_COLORS = ["#56B4E9", "#009E73", "#F0E442", "#E69F00", "#D55E00", "#CC79A7"]
NO_CURRENT_COLOR = "#d9d9d9"


def _ckey(a: Optional[float]) -> Optional[int]:
    return None if not a or a <= 0 else int(round(float(a) * 1e15))     # femtoamps


def current_classes(currents) -> Dict[int, str]:
    """{current key: 'c<k>'} for a set of currents (A), lowest current = c0. More than
    six currents share colours by rank."""
    keys = sorted({k for k in (_ckey(a) for a in currents) if k is not None})
    n, m = len(keys), len(CURRENT_COLORS)
    return {k: f"c{i if n <= m else round(i * (m - 1) / (n - 1))}" for i, k in enumerate(keys)}


def class_for(cmap: Dict[int, str], a: Optional[float]) -> str:
    return cmap.get(_ckey(a), "cn")


def diagram_svg(info: PatternInfo, cmap: Optional[Dict[int, str]] = None) -> str:
    if not info.parts:
        return ""
    e = lambda s: html.escape(str(s), quote=True)  # noqa: E731
    bx0, by0, bx1, by1 = info.bbox
    W_um, H_um = max(bx1 - bx0, 1e-3), max(by1 - by0, 1e-3)
    s = max(4.0, min(400.0, min(520.0 / W_um, 400.0 / H_um)))
    nW = len({(round(p.top.x0, 3), round(p.top.x1, 3)) for p in info.parts})
    nH = len({(round(p.bottom.y0, 3), round(p.bottom.y1, 3)) for p in info.parts})
    L, T = 200.0, 24.0 + 22.0 * nW
    R, B = 30.0 + 14.0 * nH + 70.0, 60.0
    Wpx, Hpx = L + W_um * s + R, T + H_um * s + B

    def X(x): return L + (x - bx0) * s
    def Y(y): return T + (by1 - y) * s

    if cmap is None:
        cmap = current_classes(p.rep.current_A for p in info.parts)

    def cls(cur):
        return class_for(cmap, cur)

    out = [f'<svg class="patdia" viewBox="0 0 {Wpx:.0f} {Hpx:.0f}" '
           f'xmlns="http://www.w3.org/2000/svg" role="img" '
           f'aria-label="{e(info.name)} pattern drawing with dimensions">']
    # shapes: big currents first, fine ones on top. Extension lines are collected in
    # `ext` and drawn UNDER the (opaque) shapes so they only show in the gaps.
    ext: List[str] = []
    shapes_svg: List[str] = []
    for p in sorted(info.parts, key=lambda p: -(p.rep.current_A or 0.0)):
        for sh in p.shapes:
            pts = " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in sh.pts)
            shapes_svg.append(f'<polygon class="sh {cls(sh.current_A)}" points="{pts}"/>')
    shapes_at = len(out)

    # widths, above (top shape of each part), nested biggest outermost
    seen = set()
    wreps = []
    for p in info.parts:
        k = (round(p.top.x0, 3), round(p.top.x1, 3))
        if k not in seen:
            seen.add(k)
            wreps.append(p.top)
    wreps.sort(key=lambda r: -(r.x1 - r.x0))
    for k, r in enumerate(reversed(wreps)):            # smallest closest to the drawing
        yl = Y(by1) - 14 - k * 22
        xa, xb = X(r.x0), X(r.x1)
        ext.append(f'<line class="ext" x1="{xa:.1f}" y1="{Y(r.y1):.1f}" x2="{xa:.1f}" y2="{yl:.1f}"/>'
                   f'<line class="ext" x1="{xb:.1f}" y1="{Y(r.y1):.1f}" x2="{xb:.1f}" y2="{yl:.1f}"/>')
        out.append(f'<line class="dim" x1="{xa:.1f}" y1="{yl:.1f}" x2="{xb:.1f}" y2="{yl:.1f}"/>'
                   f'<line class="dim" x1="{xa:.1f}" y1="{yl-4:.1f}" x2="{xa:.1f}" y2="{yl+4:.1f}"/>'
                   f'<line class="dim" x1="{xb:.1f}" y1="{yl-4:.1f}" x2="{xb:.1f}" y2="{yl+4:.1f}"/>'
                   f'<text class="dl" x="{(xa+xb)/2:.1f}" y="{yl-4:.1f}" text-anchor="middle">'
                   f'{fmt_um(r.width_um if r.width_um else r.x1 - r.x0)} µm</text>')

    # heights, right (bottom shape of each part)
    seen = set()
    hreps = []
    for p in info.parts:
        k = (round(p.bottom.y0, 3), round(p.bottom.y1, 3))
        if k not in seen:
            seen.add(k)
            hreps.append(p.bottom)
    hreps.sort(key=lambda r: -r.cy)                     # top-most first = closest
    xr = X(bx1)
    x_lab = xr + 14 + len(hreps) * 14 + 6
    lab_y = _spread([Y(r.cy) for r in hreps], 15.0)
    for k, r in enumerate(hreps):
        xl = xr + 14 + k * 14
        ya, yb = Y(r.y1), Y(r.y0)
        ext.append(f'<line class="ext" x1="{X(r.x1):.1f}" y1="{ya:.1f}" x2="{xl+4:.1f}" y2="{ya:.1f}"/>'
                   f'<line class="ext" x1="{X(r.x1):.1f}" y1="{yb:.1f}" x2="{xl+4:.1f}" y2="{yb:.1f}"/>')
        out.append(f'<line class="dim" x1="{xl:.1f}" y1="{ya:.1f}" x2="{xl:.1f}" y2="{yb:.1f}"/>'
                   f'<line class="dim" x1="{xl-4:.1f}" y1="{ya:.1f}" x2="{xl+4:.1f}" y2="{ya:.1f}"/>'
                   f'<line class="dim" x1="{xl-4:.1f}" y1="{yb:.1f}" x2="{xl+4:.1f}" y2="{yb:.1f}"/>'
                   f'<line class="lead" x1="{xl:.1f}" y1="{Y(r.cy):.1f}" x2="{x_lab-3:.1f}" '
                   f'y2="{lab_y[k]:.1f}"/>'
                   f'<text class="dl" x="{x_lab:.1f}" y="{lab_y[k]+4:.1f}">'
                   f'{fmt_um(r.height_um if r.height_um else r.y1 - r.y0)} µm</text>')

    # current + depth, left (top shape of each part); the centre gap joins this column
    items = [(Y(p.top.cy), X(p.top.x0), f"{fmt_current(p.rep.current_A)} · "
              f"{fmt_um(p.rep.depth_um)} µm deep", cls(p.rep.current_A)) for p in info.parts]
    if info.gap_um is not None:
        gy = Y(0.5 * (info.gap_y[0] + info.gap_y[1]))
        gx = X(info.gap_x0) - 8
        out.append(f'<line class="dim" x1="{gx:.1f}" y1="{Y(info.gap_y[1]):.1f}" x2="{gx:.1f}" '
                   f'y2="{Y(info.gap_y[0]):.1f}"/>'
                   f'<line class="dim" x1="{gx-4:.1f}" y1="{Y(info.gap_y[1]):.1f}" x2="{gx+4:.1f}" '
                   f'y2="{Y(info.gap_y[1]):.1f}"/>'
                   f'<line class="dim" x1="{gx-4:.1f}" y1="{Y(info.gap_y[0]):.1f}" x2="{gx+4:.1f}" '
                   f'y2="{Y(info.gap_y[0]):.1f}"/>')
        items.append((gy, gx, f"gap {fmt_um(info.gap_um)} µm (lamella)", "gap"))
    lab_y = _spread([it[0] for it in items], 15.0)
    xl = X(bx0) - 16
    for (ya, xa, text, c), yl in zip(items, lab_y):
        out.append(f'<line class="lead" x1="{xa:.1f}" y1="{ya:.1f}" x2="{xl+4:.1f}" y2="{yl:.1f}"/>'
                   f'<text class="cl" x="{xl:.1f}" y="{yl+4:.1f}" text-anchor="end">'
                   f'{e(text)}</text>')

    # scale bar
    bar = 1.0
    for v in (0.5, 1, 2, 5, 10, 20, 50, 100):
        if v <= 0.25 * W_um:
            bar = float(v)
    yb = Y(by0) + 34
    out.append(f'<line class="dim" x1="{X(bx0):.1f}" y1="{yb:.1f}" x2="{X(bx0) + bar*s:.1f}" '
               f'y2="{yb:.1f}" style="stroke-width:3"/>'
               f'<text class="dl" x="{X(bx0) + bar*s + 8:.1f}" y="{yb+4:.1f}">{bar:g} µm</text>')
    out[shapes_at:shapes_at] = ext + shapes_svg
    out.append("</svg>")
    return "".join(out)


def parts_table(info: PatternInfo, cmap: Optional[Dict[int, str]] = None) -> str:
    if not info.parts:
        return ""
    e = lambda s: html.escape(str(s), quote=True)  # noqa: E731
    if cmap is None:
        cmap = current_classes(p.rep.current_A for p in info.parts)
    rows = []
    for i, p in enumerate(info.parts, start=1):
        r = p.rep
        c = class_for(cmap, r.current_A)
        where = ("top + bottom (mirror pair)" if p.mirrored else
                 ("above centre" if r.y0 >= 0 else "below centre" if r.y1 <= 0 else "across centre"))
        rows.append(f"<tr><td><svg width='14' height='14' aria-hidden='true'><rect class='sh {c}' "
                    f"x='1' y='1' width='12' height='12'/></svg> {i}</td>"
                    f"<td class='num'>{e(fmt_current(r.current_A))}</td><td>{e(r.probe or '—')}</td>"
                    f"<td class='num'>{fmt_um(r.depth_um)} µm</td>"
                    f"<td class='num'>{fmt_um(r.width_um)} µm</td>"
                    f"<td class='num'>{fmt_um(r.height_um)} µm</td>"
                    f"<td>{e(where)}</td><td class='num'>{r.dose:g}</td><td>{e(r.recipe or '—')}</td></tr>")
    head = ("<tr><th>Part</th><th class='num'>Current</th><th>Probe</th><th class='num'>Depth</th>"
            "<th class='num'>Width</th><th class='num'>Height</th><th>Where</th>"
            "<th class='num'>Dose factor</th><th>Recipe</th></tr>")
    gap = (f"<p class='note'>Gap between the innermost top and bottom shapes: "
           f"<b>{fmt_um(info.gap_um)} µm</b> (the lamella, before beam tails).</p>"
           if info.gap_um is not None else "")
    return f"<div class='scroll'><table>{head}{''.join(rows)}</table></div>{gap}"


def pattern_currents(ely_path) -> List[float]:
    """Probe currents (A) used in a pattern file; [] if unreadable."""
    try:
        from elymaker.reader import read_ely
        info = analyze_layout(read_ely(str(ely_path)))
        return [p.rep.current_A for p in info.parts if p.rep.current_A]
    except Exception:  # noqa: BLE001
        return []


def pattern_section(ely_path, cmap: Optional[Dict[int, str]] = None) -> Tuple[str, List[str]]:
    """(HTML for the report, warnings). Never raises."""
    p = Path(ely_path)
    try:
        from elymaker.reader import read_ely
        layout = read_ely(str(p))
    except Exception as e:  # noqa: BLE001
        return "", [f"pattern drawing: could not read {p.name}: {e}"]
    info = analyze_layout(layout, name=p.name)
    if not info.parts:
        return "", [f"pattern drawing: {w}" for w in info.warnings]
    svg = diagram_svg(info, cmap)
    cap = (f"{html.escape(p.name)} drawn to scale (µm, +Y up, centred on the image centre "
           f"where it is milled). Left: current and depth of each part; top: widths; right: "
           f"heights. A top/bottom mirror pair is labelled once. The colours are the ones "
           f"used for each current on the position images.")
    return (f"<div class='card'><figure>{svg}<figcaption>{cap}</figcaption></figure>"
            f"{parts_table(info, cmap)}</div>", [f"pattern drawing: {w}" for w in info.warnings])


def parts_table_compact(info: PatternInfo, cmap: Optional[Dict[int, str]] = None) -> str:
    """v0.51.1 compact parts table: Part (x2 = top/bottom mirror pair), Current (probe name
    on hover), Depth, W x H, Dose, Recipe."""
    if not info.parts:
        return ""
    e = lambda s: html.escape(str(s), quote=True)  # noqa: E731
    if cmap is None:
        cmap = current_classes(p.rep.current_A for p in info.parts)
    rows = []
    for i, p in enumerate(info.parts, start=1):
        r = p.rep
        c = class_for(cmap, r.current_A)
        rows.append(f"<tr><td><svg width='14' height='14' aria-hidden='true'><rect class='sh {c}' "
                    f"x='1' y='1' width='12' height='12'/></svg> {i}"
                    f"{' ×2' if p.mirrored else ''}</td>"
                    f"<td class='num' title='{e(r.probe or '')}'>{e(fmt_current(r.current_A))}</td>"
                    f"<td class='num'>{fmt_um(r.depth_um)}</td>"
                    f"<td class='num'>{fmt_um(r.width_um)} × {fmt_um(r.height_um)}</td>"
                    f"<td class='num'>{r.dose:g}</td><td>{e(Path(str(r.recipe)).name if r.recipe else '—')}</td></tr>")
    head = ("<tr><th>Part</th><th class='num'>Current</th><th class='num'>Depth (µm)</th>"
            "<th class='num'>W × H (µm)</th><th class='num'>Dose</th><th>Recipe</th></tr>")
    return f"<div class='scroll'><table class='tight'>{head}{''.join(rows)}</table></div>"


def pattern_card(ely_path, cmap: Optional[Dict[int, str]] = None, *, title: str,
                 subtitle: str = "") -> Tuple[str, List[str]]:
    """v0.51.1: one pattern of the report's Patterns section -- name, where it was used,
    the dimensioned drawing and the compact parts table. Never raises."""
    p = Path(ely_path)
    e = lambda s: html.escape(str(s), quote=True)  # noqa: E731
    head = (f"<h3>{e(title)}</h3>" + (f"<p class='note'>{e(subtitle)}</p>" if subtitle else ""))
    if not p.exists():
        return (f"<div class='card pcard'>{head}<p class='warn'>pattern file not found: "
                f"{e(p)}</p></div>", [f"pattern {p.name}: file not found"])
    try:
        from elymaker.reader import read_ely
        layout = read_ely(str(p))
    except Exception as ex:  # noqa: BLE001
        return (f"<div class='card pcard'>{head}<p class='warn'>could not read: {e(ex)}</p>"
                f"</div>", [f"pattern drawing: could not read {p.name}: {ex}"])
    info = analyze_layout(layout, name=p.name)
    if not info.parts:
        return (f"<div class='card pcard'>{head}</div>",
                [f"pattern drawing: {w}" for w in info.warnings])
    return (f"<div class='card pcard'>{head}<figure>{diagram_svg(info, cmap)}</figure>"
            f"{parts_table_compact(info, cmap)}</div>",
            [f"pattern drawing: {w}" for w in info.warnings])
