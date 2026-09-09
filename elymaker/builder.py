"""Fluent API for authoring ELAYOUT patterns in code.

Example -- a two-box "waffle" bulk mill::

    from elymaker import LayoutBuilder
    b = LayoutBuilder("MyWaffle", recipe=r"D:\\FIB Recipes\\Vitriuos_Ice.epm",
                      fov_um=600)
    b.rect(x=0, y=20, width=18, height=6, depth_um=15, probe="30kV:15nA")
    b.rect(x=0, y=-20, width=18, height=6, depth_um=15, probe="30kV:15nA",
           angle_deg=180)          # opposite mill, fast-scan flipped
    b.save("MyWaffle.ely")

All lengths are in **micrometres**. ``depth_um`` sets both ``dose_factor`` and
``z_extent`` consistently (they are coupled 1 um : 1e-6 m in SmartFIB).

SmartFIB convention: a RECT's ``height`` is written **negative** (the box grows
downward from its marked top edge). :meth:`rect` accepts a positive height for
convenience and stores it negative by default; pass ``height_sign=+1`` to keep
it as given. This sign affects where material is removed relative to the anchor
-- verify a generated box in SmartFIB before milling.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple, Union

from .model import (
    Arc,
    Exposure,
    Layer,
    LayerReference,
    Layout,
    Polygon,
    Probe,
    Rect,
    Structure,
    Text,
    VersionInfo,
    DOSE_TO_METRE,
)
from .probes import get_probe

ProbeArg = Union[str, Probe, None]


def _now() -> str:
    return datetime.now().strftime("%d.%m.%Y %H:%M:%S")


def _resolve_probe(probe: ProbeArg) -> Optional[Probe]:
    if probe is None:
        return None
    if isinstance(probe, Probe):
        return probe
    return get_probe(probe)


class LayoutBuilder:
    """Accumulate shapes into a single-structure, single-layer Layout."""

    def __init__(
        self,
        name: str = "Layout",
        recipe: str = r"D:\FIB Recipes\Vitriuos_Ice.epm",
        fov_um: float = 600.0,
        exposure_version: str = "2.1",
    ) -> None:
        self.name = name
        self.recipe = recipe
        self.fov_um = float(fov_um)
        self.exposure_version = exposure_version
        stamp = _now()
        self._layout = Layout(
            name=name,
            version=VersionInfo(created=stamp, modified=stamp, number="1.0"),
            layers=[Layer()],
        )
        self._lref = LayerReference(ref="Layer", frame_size=self.fov_um)
        self._structure = Structure(
            name="Structure",
            version=VersionInfo(created=stamp, modified=stamp, number="1.0"),
            layer_refs=[self._lref],
        )
        self._layout.structures = [self._structure]

    # -- exposure helper -------------------------------------------------- #
    def _exposure(self, probe: ProbeArg, recipe: Optional[str]) -> Exposure:
        return Exposure(
            file=recipe if recipe is not None else self.recipe,
            version=self.exposure_version,
            probe=_resolve_probe(probe),
        )

    @staticmethod
    def _z_from_depth(depth_um: float) -> Tuple[float, float]:
        return float(depth_um), float(depth_um) * DOSE_TO_METRE

    # -- shape factories -------------------------------------------------- #
    def rect(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        depth_um: float,
        probe: ProbeArg = None,
        angle_deg: float = 0.0,
        imaging: bool = False,
        recipe: Optional[str] = None,
        edge_sel_auto_scan_angle: bool = True,
        edge_sel_marked_edge: Optional[int] = None,
        area_fast_deg: Optional[float] = None,
        ignored: bool = False,
        height_sign: int = -1,
    ) -> Rect:
        """Add a rectangle. ``height`` given as positive; stored *height_sign*."""
        dose, z = self._z_from_depth(depth_um)
        h = abs(height) * (1 if height_sign >= 0 else -1)
        r = Rect(
            dose_factor=dose,
            z_extent_m=z,
            exposure=self._exposure(probe, recipe),
            x=float(x),
            y=float(y),
            width=float(width),
            height=float(h),
            angle_deg=float(angle_deg),
            imaging=imaging,
            edge_sel_auto_scan_angle=edge_sel_auto_scan_angle,
            edge_sel_marked_edge=edge_sel_marked_edge,
            area_fast_deg=area_fast_deg,
            ignored=ignored,
        )
        self._lref.shapes.append(r)
        return r

    def polygon(
        self,
        points: List[Tuple[float, float]],
        depth_um: float,
        probe: ProbeArg = None,
        recipe: Optional[str] = None,
        edge_sel_auto_scan_angle: bool = True,
        edge_sel_marked_edge: Optional[int] = None,
        ignored: bool = False,
    ) -> Polygon:
        """Add a polygon from a list of (x, y) vertices in micrometres."""
        dose, z = self._z_from_depth(depth_um)
        p = Polygon(
            dose_factor=dose,
            z_extent_m=z,
            exposure=self._exposure(probe, recipe),
            points=[(float(a), float(b)) for a, b in points],
            edge_sel_auto_scan_angle=edge_sel_auto_scan_angle,
            edge_sel_marked_edge=edge_sel_marked_edge,
            ignored=ignored,
        )
        self._lref.shapes.append(p)
        return p

    def rounded_rect(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        radius: float,
        side: str = "top",
        segments: int = 12,
        depth_um: float = 1.0,
        probe: ProbeArg = None,
        recipe: Optional[str] = None,
        edge_sel_auto_scan_angle: bool = True,
        edge_sel_marked_edge: Optional[int] = None,
        ignored: bool = False,
    ) -> Polygon:
        """Add a box with two rounded corners on *side* as a FIB-legal polygon.

        Parameters mirror :meth:`rect` (anchor + signed width/height) plus the
        fillet ``radius`` (um, auto-clamped), the ``side`` to round, and
        ``segments`` points per corner. Emits a plain POLYGON.
        """
        from .geometry import rounded_rect_polygon
        pts = rounded_rect_polygon(x, y, width, height, radius, side, segments)
        return self.polygon(
            pts,
            depth_um=depth_um,
            probe=probe,
            recipe=recipe,
            edge_sel_auto_scan_angle=edge_sel_auto_scan_angle,
            edge_sel_marked_edge=edge_sel_marked_edge,
            ignored=ignored,
        )

    def arc(
        self,
        cx: float,
        cy: float,
        r1: float,
        r2: float,
        a1_deg: float,
        a2_deg: float,
        depth_um: float,
        probe: ProbeArg = None,
        arc_dir: Optional[str] = None,
        recipe: Optional[str] = None,
    ) -> Arc:
        """Add an annular arc (r1 outer, r2 inner) between angles a1..a2."""
        dose, z = self._z_from_depth(depth_um)
        a = Arc(
            dose_factor=dose,
            z_extent_m=z,
            exposure=self._exposure(probe, recipe),
            cx=float(cx),
            cy=float(cy),
            r1=float(r1),
            r2=float(r2),
            a1_deg=float(a1_deg),
            a2_deg=float(a2_deg),
            arc_dir=arc_dir,
        )
        self._lref.shapes.append(a)
        return a

    def text(
        self,
        x: float,
        y: float,
        text: str,
        height: float,
        depth_um: float,
        probe: ProbeArg = None,
        font: str = "sans serif",
        angle_deg: float = 0.0,
        recipe: Optional[str] = None,
    ) -> Text:
        """Add a text label to be milled."""
        dose, z = self._z_from_depth(depth_um)
        t = Text(
            dose_factor=dose,
            z_extent_m=z,
            exposure=self._exposure(probe, recipe),
            x=float(x),
            y=float(y),
            text=text,
            height=float(height),
            font=font,
            angle_deg=float(angle_deg),
        )
        self._lref.shapes.append(t)
        return t

    # -- output ----------------------------------------------------------- #
    @property
    def layout(self) -> Layout:
        return self._layout

    def build(self) -> Layout:
        return self._layout

    def dumps(self) -> str:
        from .writer import dumps as _dumps
        return _dumps(self._layout)

    def save(self, path: str) -> None:
        from .writer import write_ely
        write_ely(self._layout, path)
