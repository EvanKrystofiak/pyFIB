"""Dataclass model of a SmartFIB ELAYOUT (.ely) document.

Coordinate/units convention (see units.py):
  * x, y, width, height, cx, cy, r1, r2, TEXT height, polygon points,
    frame_cx/cy, frame_size  -> micrometres (bare numbers in the file)
  * z_extent_m, current_A, diameter_m                 -> SI (metres, amps)
  * angle_deg, a1_deg, a2_deg, area_fast_deg          -> degrees

Fidelity: every element carries an ``extra`` dict capturing any attribute the
model does not explicitly know. The writer re-emits those verbatim so that
round-tripping a file never silently drops data -- important for an
instrument-facing format we don't fully control.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# z_extent (m) == dose_factor * DOSE_TO_METRE. Confirmed across all example
# files: dose_factor is the mill depth expressed in micrometres.
DOSE_TO_METRE = 1e-6


# --------------------------------------------------------------------------- #
# Exposure / probe
# --------------------------------------------------------------------------- #
@dataclass
class Probe:
    name: str
    current_A: float
    diameter_m: float
    type_: str = "specific"
    extra: Dict[str, str] = field(default_factory=dict)


@dataclass
class Exposure:
    file: str                      # path to the .epm recipe (as SmartFIB stores it)
    version: str = "2.1"
    probe: Optional[Probe] = None
    extra: Dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Shapes
# --------------------------------------------------------------------------- #
@dataclass
class Shape:
    """Common fields shared by every shape element."""
    dose_factor: float
    z_extent_m: float
    exposure: Optional[Exposure] = None
    extra: Dict[str, str] = field(default_factory=dict)

    @property
    def depth_um(self) -> float:
        """Mill depth in micrometres (derived from z_extent)."""
        return self.z_extent_m / DOSE_TO_METRE


@dataclass
class Rect(Shape):
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0                 # SmartFIB writes this NEGATIVE (see builder)
    angle_deg: float = 0.0
    imaging: bool = False
    edge_sel_auto_scan_angle: bool = True
    edge_sel_marked_edge: Optional[int] = None   # 3 or 4 in real files
    area_fast_deg: Optional[float] = None        # present with angle 180/270
    ignored: bool = False


@dataclass
class Polygon(Shape):
    points: List[Tuple[float, float]] = field(default_factory=list)  # um
    contours: str = "0 "               # kept as-authored ("0 " has trailing space)
    edge_sel_auto_scan_angle: bool = True
    edge_sel_marked_edge: Optional[int] = None
    ignored: bool = False


@dataclass
class Arc(Shape):
    cx: float = 0.0
    cy: float = 0.0
    r1: float = 0.0
    r2: float = 0.0
    a1_deg: float = 0.0
    a2_deg: float = 0.0
    arc_dir: Optional[str] = None      # "cw" or None (default ccw)


@dataclass
class Text(Shape):
    x: float = 0.0
    y: float = 0.0
    height: float = 0.0                # text height, um
    text: str = ""
    font: str = "sans serif"
    angle_deg: float = 0.0


# --------------------------------------------------------------------------- #
# Containers
# --------------------------------------------------------------------------- #
@dataclass
class LayerReference:
    ref: str = "Layer"
    frame_cx: float = 0.0
    frame_cy: float = 0.0
    frame_size: float = 0.0            # FIB field-of-view width hint, um
    shapes: List[Shape] = field(default_factory=list)
    extra: Dict[str, str] = field(default_factory=dict)


@dataclass
class VersionInfo:
    created: str = "01.01.2024 00:00:00"
    modified: str = "01.01.2024 00:00:00"
    number: str = "1.0"


@dataclass
class Structure:
    name: str = "Structure"
    locked: bool = False
    version: VersionInfo = field(default_factory=VersionInfo)
    layer_refs: List[LayerReference] = field(default_factory=list)
    extra: Dict[str, str] = field(default_factory=dict)


@dataclass
class Layer:
    name: str = "Layer"
    fill_color: str = "#00FF00"
    fill_opacity: str = "0.5"
    hidden: bool = False
    locked: bool = False
    exposure: Optional["Exposure"] = None   # layer-level default recipe/probe
    extra: Dict[str, str] = field(default_factory=dict)


@dataclass
class Layout:
    name: str = "Layout"
    version_attr: str = "2.0"          # ELAYOUT version attribute
    locked: bool = False
    version: VersionInfo = field(default_factory=VersionInfo)
    axes_show: bool = True
    grid_horizontal: str = "1"
    grid_vertical: str = "1"
    grid_show: bool = True
    grid_snap_to: bool = False
    layers: List[Layer] = field(default_factory=lambda: [Layer()])
    structures: List[Structure] = field(default_factory=list)

    # convenience --------------------------------------------------------- #
    def iter_shapes(self):
        """Yield (structure, layer_reference, shape) for every shape."""
        for st in self.structures:
            for lr in st.layer_refs:
                for sh in lr.shapes:
                    yield st, lr, sh

    def shape_count(self) -> int:
        return sum(1 for _ in self.iter_shapes())
