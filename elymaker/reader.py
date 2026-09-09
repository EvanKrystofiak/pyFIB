"""Parse a SmartFIB ELAYOUT (.ely) file into a :class:`~elymaker.model.Layout`.

Unknown attributes are preserved in each element's ``extra`` dict so that
round-tripping is lossless even for attributes this version does not model.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

from .model import (
    Arc,
    Exposure,
    Layer,
    LayerReference,
    Layout,
    Polygon,
    Probe,
    Rect,
    Shape,
    Structure,
    Text,
    VersionInfo,
)
from .units import parse_suffixed


class ElyParseError(ValueError):
    """Raised when a file is not a well-formed ELAYOUT document."""


def _pop_bool(attrs: Dict[str, str], key: str, default: bool) -> bool:
    if key in attrs:
        return attrs.pop(key).strip().lower() == "true"
    return default


def _pop_float(attrs: Dict[str, str], key: str, default: float = 0.0) -> float:
    if key in attrs:
        return parse_suffixed(attrs.pop(key))
    return default


def _pop_opt_float(attrs: Dict[str, str], key: str) -> Optional[float]:
    if key in attrs:
        return parse_suffixed(attrs.pop(key))
    return None


def _pop_opt_int(attrs: Dict[str, str], key: str) -> Optional[int]:
    if key in attrs:
        return int(float(attrs.pop(key)))
    return None


def _parse_points(raw: str) -> List[Tuple[float, float]]:
    """Parse ``"(x y) (x y) "`` -> list of (x, y) tuples."""
    pts: List[Tuple[float, float]] = []
    buf = raw
    i = 0
    while True:
        lp = buf.find("(", i)
        if lp == -1:
            break
        rp = buf.find(")", lp)
        if rp == -1:
            break
        inner = buf[lp + 1 : rp].split()
        if len(inner) >= 2:
            pts.append((float(inner[0]), float(inner[1])))
        i = rp + 1
    return pts


def _parse_exposure(elem: ET.Element) -> Exposure:
    attrs = dict(elem.attrib)
    version = attrs.pop("version", "2.1")
    file_ = attrs.pop("file", "")
    probe: Optional[Probe] = None
    for child in elem:
        if child.tag == "PROBE":
            pa = dict(child.attrib)
            probe = Probe(
                name=pa.pop("name", ""),
                type_=pa.pop("type", "specific"),
                current_A=parse_suffixed(pa.pop("current", "0 A")),
                diameter_m=parse_suffixed(pa.pop("diameter", "0 m")),
                extra=pa,
            )
    return Exposure(file=file_, version=version, probe=probe, extra=attrs)


def _find_exposure(elem: ET.Element) -> Optional[Exposure]:
    for child in elem:
        if child.tag == "EXPOSURE":
            return _parse_exposure(child)
    return None


def _parse_common(attrs: Dict[str, str]) -> Tuple[float, float]:
    dose = _pop_float(attrs, "dose_factor", 0.0)
    z = _pop_float(attrs, "z_extent", 0.0)
    return dose, z


def _parse_shape(elem: ET.Element) -> Optional[Shape]:
    tag = elem.tag
    attrs = dict(elem.attrib)
    exposure = _find_exposure(elem)

    if tag == "RECT":
        dose, z = _parse_common(attrs)
        sh: Shape = Rect(
            dose_factor=dose,
            z_extent_m=z,
            exposure=exposure,
            area_fast_deg=_pop_opt_float(attrs, "area_fast"),
            ignored=_pop_bool(attrs, "ignored", False),
            edge_sel_auto_scan_angle=_pop_bool(attrs, "edge_sel_auto_scan_angle", True),
            edge_sel_marked_edge=_pop_opt_int(attrs, "edge_sel_marked_edge"),
            height=_pop_float(attrs, "height"),
            width=_pop_float(attrs, "width"),
            angle_deg=_pop_float(attrs, "angle"),
            x=_pop_float(attrs, "x"),
            y=_pop_float(attrs, "y"),
            imaging=_pop_bool(attrs, "imaging", False),
        )
        sh.extra = attrs
        return sh

    if tag == "POLYGON":
        dose, z = _parse_common(attrs)
        pts = _parse_points(attrs.pop("points", ""))
        contours = attrs.pop("contours", "0 ")
        sh = Polygon(
            dose_factor=dose,
            z_extent_m=z,
            exposure=exposure,
            ignored=_pop_bool(attrs, "ignored", False),
            edge_sel_auto_scan_angle=_pop_bool(attrs, "edge_sel_auto_scan_angle", True),
            edge_sel_marked_edge=_pop_opt_int(attrs, "edge_sel_marked_edge"),
            points=pts,
            contours=contours,
        )
        sh.extra = attrs
        return sh

    if tag == "ARC":
        dose, z = _parse_common(attrs)
        sh = Arc(
            dose_factor=dose,
            z_extent_m=z,
            exposure=exposure,
            arc_dir=attrs.pop("arc_dir", None),
            cx=_pop_float(attrs, "cx"),
            cy=_pop_float(attrs, "cy"),
            r1=_pop_float(attrs, "r1"),
            r2=_pop_float(attrs, "r2"),
            a1_deg=_pop_float(attrs, "a1"),
            a2_deg=_pop_float(attrs, "a2"),
        )
        sh.extra = attrs
        return sh

    if tag == "TEXT":
        dose, z = _parse_common(attrs)
        sh = Text(
            dose_factor=dose,
            z_extent_m=z,
            exposure=exposure,
            height=_pop_float(attrs, "height"),
            text=attrs.pop("text", ""),
            font=attrs.pop("font", "sans serif"),
            angle_deg=_pop_float(attrs, "angle"),
            x=_pop_float(attrs, "x"),
            y=_pop_float(attrs, "y"),
        )
        sh.extra = attrs
        return sh

    return None  # unknown / non-shape child (e.g. EXPOSURE handled above)


def _parse_version(elem: Optional[ET.Element]) -> VersionInfo:
    if elem is None:
        return VersionInfo()
    a = elem.attrib
    return VersionInfo(
        created=a.get("created", "01.01.2024 00:00:00"),
        modified=a.get("modified", "01.01.2024 00:00:00"),
        number=a.get("number", "1.0"),
    )


def _parse_layer_reference(elem: ET.Element) -> LayerReference:
    attrs = dict(elem.attrib)
    lr = LayerReference(
        ref=attrs.pop("ref", "Layer"),
        frame_cx=_pop_float(attrs, "frame_cx"),
        frame_cy=_pop_float(attrs, "frame_cy"),
        frame_size=_pop_float(attrs, "frame_size"),
        extra=attrs,
    )
    for child in elem:
        sh = _parse_shape(child)
        if sh is not None:
            lr.shapes.append(sh)
    return lr


def _parse_structure(elem: ET.Element) -> Structure:
    attrs = dict(elem.attrib)
    st = Structure(
        name=attrs.pop("name", "Structure"),
        locked=_pop_bool(attrs, "locked", False),
        version=_parse_version(elem.find("VERSION")),
        extra=attrs,
    )
    for child in elem:
        if child.tag == "LAYER_REFERENCE":
            st.layer_refs.append(_parse_layer_reference(child))
    return st


def _parse_layer(elem: ET.Element) -> Layer:
    attrs = dict(elem.attrib)
    return Layer(
        name=attrs.pop("name", "Layer"),
        fill_color=attrs.pop("fill_color", "#00FF00"),
        fill_opacity=attrs.pop("fill_opacity", "0.5"),
        hidden=_pop_bool(attrs, "hidden", False),
        locked=_pop_bool(attrs, "locked", False),
        exposure=_find_exposure(elem),
        extra=attrs,
    )


def _layout_from_root(root: ET.Element) -> Layout:
    if root.tag != "ELAYOUT":
        raise ElyParseError("Root element is %r, expected 'ELAYOUT'" % root.tag)

    a = root.attrib
    layout = Layout(
        name=a.get("name", "Layout"),
        version_attr=a.get("version", "2.0"),
        locked=(a.get("locked", "false").lower() == "true"),
        version=_parse_version(root.find("VERSION")),
    )

    axes = root.find("AXES")
    if axes is not None:
        layout.axes_show = axes.get("show", "true").lower() == "true"

    grid = root.find("GRID")
    if grid is not None:
        layout.grid_horizontal = grid.get("horizontal", "1")
        layout.grid_vertical = grid.get("vertical", "1")
        layout.grid_show = grid.get("show", "true").lower() == "true"
        layout.grid_snap_to = grid.get("snap_to", "false").lower() == "true"

    ll = root.find("LAYER_LIST")
    if ll is not None:
        layers = [_parse_layer(e) for e in ll.findall("LAYER")]
        if layers:
            layout.layers = layers

    sl = root.find("STRUCTURE_LIST")
    if sl is not None:
        layout.structures = [_parse_structure(e) for e in sl.findall("STRUCTURE")]

    return layout


def loads(text: str) -> Layout:
    """Parse an ELAYOUT document from a string."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ElyParseError("XML parse error: %s" % exc) from exc
    return _layout_from_root(root)


def read_ely(path: str) -> Layout:
    """Read an ELAYOUT document from a file path."""
    with open(path, "r", encoding="utf-8") as fh:
        return loads(fh.read())
