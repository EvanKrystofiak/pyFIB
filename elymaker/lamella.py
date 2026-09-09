"""Lamella milling-pattern generator (core, no Qt).

Builds the classic stepped stack: 5 stages (Rough 1, Rough 2, Fine 1, Fine 2,
Polish), each a top/bottom pair straddling the lamella, milled toward it. As
stages refine they get shorter in X and -- crucially -- much **shorter in Y**
(each stage's box height is set per stage), so the low-current fine/polish
stages only clean a thin sliver near the lamella instead of milling a tall box
(which would take far too long). Default heights are taken from a real pattern
(roughly halving each stage: 2.65, 1.3, 0.7, 0.27, 0.2 um).

Vertical layout (top side; bottom mirrors across the centerline). Boxes are
positioned from the inside out so each overlaps its outer neighbour by
``overlap`` (no un-milled seam):

    Polish  inner = thickness/2 ,          outer = inner + h_Polish
    Fine 2  inner = outer_Polish - overlap, outer = inner + h_Fine2
    ... up to Rough 1 (outermost).

Box width (X) is ``final_width + k*h_step`` (k = 0 innermost .. 4 outermost),
centered on the lamella. Every box marks its **inner** edge (top box -> bottom
edge, bottom box -> top edge) so it mills toward the lamella.

Optional **side trenches**: two thin vertical boxes spanning the full pattern
height, offset ``trench_offset`` outside the widest (Rough 1) box, each milling
inward toward the lamella. Defaults (width ~1 um, offset ~2.2 um) come from the
same real pattern.

Output is plain ``Rect`` models (FIB-legal). Milling order interleaves per stage
(both Rough 1, both Rough 2, ...); trenches follow the Rough 1 pair.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .model import Rect, Exposure, DOSE_TO_METRE
from .probes import get_probe, PROBES

# marked-edge indices for a w>0, h<0 rect (vertex order): 1=top 2=right 3=bottom 4=left
_EDGE_TOP, _EDGE_RIGHT, _EDGE_BOTTOM, _EDGE_LEFT = 1, 2, 3, 4


@dataclass
class LamellaStage:
    name: str
    probe: Optional[str] = None      # probe name in the library, or None
    height_um: float = 1.0           # Y extent of this stage's boxes


def default_stages() -> List[LamellaStage]:
    """Rough1..Polish (outermost first) with default currents and heights taken
    from a real pattern -- heights roughly halve each stage so the fine/polish
    stages stay fast."""
    return [
        LamellaStage("Rough 1", "30kV:700pA", 2.65),
        LamellaStage("Rough 2", "30kV:300pA", 1.30),
        LamellaStage("Fine 1", "30kV:100pA", 0.70),
        LamellaStage("Fine 2", "30kV:50pA", 0.27),
        LamellaStage("Polish", "30kV:20pA", 0.20),
    ]


@dataclass
class LamellaSpec:
    final_width_um: float = 12.0       # X length of the finished lamella
    final_thickness_um: float = 0.275  # Y gap the polish leaves (tip-to-tip)
    h_step_um: float = 2.0             # X width reduction per stage (inward)
    overlap_um: float = 0.15           # vertical overlap between adjacent stages
    depth_um: float = 1.0              # mill depth (same for every box)
    center_x_um: float = 0.0
    center_y_um: float = 0.0
    recipe: Optional[str] = None       # same .epm for all boxes
    stages: List[LamellaStage] = field(default_factory=default_stages)
    # optional side trenches
    trenches: bool = False
    trench_width_um: float = 1.0
    trench_offset_um: float = 2.2      # gap from the widest box edge to trench
    trench_probe: Optional[str] = "30kV:700pA"


def _exposure(spec: LamellaSpec, probe_name: Optional[str]) -> Optional[Exposure]:
    probe = get_probe(probe_name) if (probe_name and probe_name in PROBES) else None
    if spec.recipe is None and probe is None:
        return None
    return Exposure(file=spec.recipe or "", probe=probe)


def _rect(spec, exp, x, y, w, h, marked):
    return Rect(
        dose_factor=spec.depth_um, z_extent_m=spec.depth_um * DOSE_TO_METRE,
        exposure=exp, x=x, y=y, width=w, height=h,
        edge_sel_auto_scan_angle=True, edge_sel_marked_edge=marked,
    )


def build_lamella(spec: LamellaSpec) -> List[Rect]:
    """Return the ordered Rect boxes (10 stages; +2 if trenches enabled)."""
    n = len(spec.stages)
    half_t = spec.final_thickness_um / 2.0
    cx, cy = spec.center_x_um, spec.center_y_um

    # position each stage inside-out so it overlaps its outer neighbour
    inners: Dict[int, float] = {}
    outers: Dict[int, float] = {}
    for j in range(n - 1, -1, -1):          # innermost (last stage) first
        st = spec.stages[j]
        inner = half_t if j == n - 1 else (outers[j + 1] - spec.overlap_um)
        inners[j] = inner
        outers[j] = inner + max(st.height_um, 1e-4)

    pairs: List[Rect] = []
    for j, st in enumerate(spec.stages):
        k = (n - 1) - j                      # outermost stage has largest width
        width = spec.final_width_um + k * spec.h_step_um
        left = cx - width / 2.0
        inner, outer = inners[j], outers[j]
        box_h = outer - inner
        exp = _exposure(spec, st.probe)
        # top box: anchor at its outer (upper) edge, inner edge (bottom) marked
        pairs.append(_rect(spec, exp, left, cy + outer, width, -box_h, _EDGE_BOTTOM))
        # bottom box: mirror; inner edge (top) marked
        pairs.append(_rect(spec, exp, left, cy - inner, width, -box_h, _EDGE_TOP))

    # optional side trenches, inserted right after the Rough 1 pair (as in the
    # real pattern). They span the full pattern height and mill inward.
    if spec.trenches:
        r1_half = (spec.final_width_um + (n - 1) * spec.h_step_um) / 2.0
        pat_half_h = outers[0]               # Rough 1 outer edge = pattern half-height
        th = 2.0 * pat_half_h
        tw = spec.trench_width_um
        inner_x = r1_half + spec.trench_offset_um
        exp_t = _exposure(spec, spec.trench_probe)
        # left trench: right edge is inner (faces +x toward lamella)
        left_tr = _rect(spec, exp_t, cx - (inner_x + tw), cy + pat_half_h, tw, -th, _EDGE_RIGHT)
        # right trench: left edge is inner (faces -x)
        right_tr = _rect(spec, exp_t, cx + inner_x, cy + pat_half_h, tw, -th, _EDGE_LEFT)
        pairs = pairs[:2] + [left_tr, right_tr] + pairs[2:]

    return pairs
