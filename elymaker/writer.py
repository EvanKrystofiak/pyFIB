"""Serialize a :class:`~elymaker.model.Layout` to native-looking .ely XML.

Attribute order and indentation mimic the example files so generated output
looks like SmartFIB wrote it and diffs cleanly. Any attribute captured in an
element's ``extra`` dict during reading is re-emitted after the known
attributes, keeping round-trips lossless.
"""

from __future__ import annotations

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
)
from .units import fmt_num, fmt_suffixed


def _esc(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _attr(name: str, value: str) -> str:
    return '%s="%s"' % (name, _esc(value))


def _bool(v: bool) -> str:
    return "true" if v else "false"


def _extra(extra: Dict[str, str]) -> List[str]:
    return [_attr(k, str(v)) for k, v in extra.items()]


def _fmt_points(points: List[Tuple[float, float]]) -> str:
    return "".join("(%s %s) " % (fmt_num(x), fmt_num(y)) for x, y in points)


# --------------------------------------------------------------------------- #
# Exposure / probe
# --------------------------------------------------------------------------- #
def _write_exposure(exp: Optional[Exposure], indent: str) -> List[str]:
    if exp is None:
        return []
    parts = [_attr("version", exp.version), _attr("file", exp.file)]
    parts += _extra(exp.extra)
    lines = ["%s<EXPOSURE %s>" % (indent, " ".join(parts))]
    if exp.probe is not None:
        p = exp.probe
        pparts = [
            _attr("name", p.name),
            _attr("type", p.type_),
            _attr("current", fmt_suffixed(p.current_A, "A")),
            _attr("diameter", fmt_suffixed(p.diameter_m, "m")),
        ]
        pparts += _extra(p.extra)
        lines.append("%s<PROBE %s/>" % (indent, " ".join(pparts)))
    lines.append("%s</EXPOSURE>" % indent)
    return lines


# --------------------------------------------------------------------------- #
# Shapes
# --------------------------------------------------------------------------- #
def _write_shape(sh: Shape, indent: str) -> List[str]:
    exp_indent = indent + "  "
    if isinstance(sh, Rect):
        parts = [
            _attr("dose_factor", fmt_num(sh.dose_factor)),
            _attr("z_extent", fmt_suffixed(sh.z_extent_m, "m")),
        ]
        if sh.area_fast_deg is not None:
            parts.append(_attr("area_fast", fmt_suffixed(sh.area_fast_deg, "deg")))
        if sh.ignored:
            parts.append(_attr("ignored", "true"))
        parts.append(_attr("edge_sel_auto_scan_angle", _bool(sh.edge_sel_auto_scan_angle)))
        if sh.edge_sel_marked_edge is not None:
            parts.append(_attr("edge_sel_marked_edge", str(sh.edge_sel_marked_edge)))
        parts += [
            _attr("height", fmt_num(sh.height)),
            _attr("width", fmt_num(sh.width)),
            _attr("angle", fmt_suffixed(sh.angle_deg, "deg")),
            _attr("x", fmt_num(sh.x)),
            _attr("y", fmt_num(sh.y)),
            _attr("imaging", _bool(sh.imaging)),
        ]
        parts += _extra(sh.extra)
        tag = "RECT"

    elif isinstance(sh, Polygon):
        parts = [
            _attr("dose_factor", fmt_num(sh.dose_factor)),
            _attr("z_extent", fmt_suffixed(sh.z_extent_m, "m")),
        ]
        if sh.ignored:
            parts.append(_attr("ignored", "true"))
        parts.append(_attr("edge_sel_auto_scan_angle", _bool(sh.edge_sel_auto_scan_angle)))
        if sh.edge_sel_marked_edge is not None:
            parts.append(_attr("edge_sel_marked_edge", str(sh.edge_sel_marked_edge)))
        parts += [
            _attr("points", _fmt_points(sh.points)),
            _attr("contours", sh.contours),
        ]
        parts += _extra(sh.extra)
        tag = "POLYGON"

    elif isinstance(sh, Arc):
        parts = [
            _attr("dose_factor", fmt_num(sh.dose_factor)),
            _attr("z_extent", fmt_suffixed(sh.z_extent_m, "m")),
        ]
        if sh.arc_dir is not None:
            parts.append(_attr("arc_dir", sh.arc_dir))
        parts += [
            _attr("cx", fmt_num(sh.cx)),
            _attr("cy", fmt_num(sh.cy)),
            _attr("r1", fmt_num(sh.r1)),
            _attr("r2", fmt_num(sh.r2)),
            _attr("a1", fmt_suffixed(sh.a1_deg, "deg")),
            _attr("a2", fmt_suffixed(sh.a2_deg, "deg")),
        ]
        parts += _extra(sh.extra)
        tag = "ARC"

    elif isinstance(sh, Text):
        parts = [
            _attr("dose_factor", fmt_num(sh.dose_factor)),
            _attr("z_extent", fmt_suffixed(sh.z_extent_m, "m")),
            _attr("height", fmt_num(sh.height)),
            _attr("text", sh.text),
            _attr("font", sh.font),
            _attr("angle", fmt_suffixed(sh.angle_deg, "deg")),
            _attr("x", fmt_num(sh.x)),
            _attr("y", fmt_num(sh.y)),
        ]
        parts += _extra(sh.extra)
        tag = "TEXT"

    else:  # pragma: no cover - defensive
        raise TypeError("Unknown shape type: %r" % type(sh))

    lines = ["%s<%s %s>" % (indent, tag, " ".join(parts))]
    lines += _write_exposure(sh.exposure, exp_indent)
    lines.append("%s    </%s>" % (indent, tag))
    return lines


# --------------------------------------------------------------------------- #
# Containers
# --------------------------------------------------------------------------- #
def _write_layer_reference(lr: LayerReference, indent: str) -> List[str]:
    parts = [
        _attr("frame_cx", fmt_num(lr.frame_cx)),
        _attr("frame_cy", fmt_num(lr.frame_cy)),
        _attr("frame_size", fmt_num(lr.frame_size)),
        _attr("ref", lr.ref),
    ]
    parts += _extra(lr.extra)
    lines = ["%s<LAYER_REFERENCE %s>" % (indent, " ".join(parts))]
    for sh in lr.shapes:
        lines += _write_shape(sh, indent + "  ")
    lines.append("%s</LAYER_REFERENCE>" % indent)
    return lines


def _write_structure(st: Structure, indent: str) -> List[str]:
    parts = [_attr("locked", _bool(st.locked)), _attr("name", st.name)]
    parts += _extra(st.extra)
    lines = ["%s<STRUCTURE %s>" % (indent, " ".join(parts))]
    v = st.version
    lines.append(
        '%s  <VERSION created="%s" modified="%s" number="%s"/>'
        % (indent, _esc(v.created), _esc(v.modified), _esc(v.number))
    )
    lines.append("%s  <INSTANCE_LIST/>" % indent)
    for lr in st.layer_refs:
        lines += _write_layer_reference(lr, indent + "  ")
    lines.append("%s</STRUCTURE>" % indent)
    return lines


def _write_layer(layer: Layer, indent: str) -> List[str]:
    parts = [
        _attr("fill_color", layer.fill_color),
        _attr("fill_opacity", layer.fill_opacity),
        _attr("hidden", _bool(layer.hidden)),
        _attr("locked", _bool(layer.locked)),
        _attr("name", layer.name),
    ]
    parts += _extra(layer.extra)
    lines = ["%s<LAYER %s>" % (indent, " ".join(parts))]
    if layer.exposure is not None:
        lines += _write_exposure(layer.exposure, indent + "        ")
    lines.append("%s    </LAYER>" % indent)
    return lines


def dumps(layout: Layout) -> str:
    """Serialize a Layout to an ELAYOUT XML string."""
    out: List[str] = []
    out.append('<?xml version="1.0" encoding="UTF-8" standalone="no" ?>')
    out.append(
        '<ELAYOUT version="%s" locked="%s" name="%s">'
        % (_esc(layout.version_attr), _bool(layout.locked), _esc(layout.name))
    )
    out.append("")
    v = layout.version
    out.append(
        '  <VERSION created="%s" modified="%s" number="%s"/>'
        % (_esc(v.created), _esc(v.modified), _esc(v.number))
    )
    out.append("")
    out.append('  <AXES show="%s"/>' % _bool(layout.axes_show))
    out.append("")
    out.append(
        '  <GRID horizontal="%s" show="%s" snap_to="%s" vertical="%s"/>'
        % (
            _esc(layout.grid_horizontal),
            _bool(layout.grid_show),
            _bool(layout.grid_snap_to),
            _esc(layout.grid_vertical),
        )
    )
    out.append("")
    out.append("  <LAYER_LIST>")
    for layer in layout.layers:
        out += _write_layer(layer, "    ")
    out.append("  </LAYER_LIST>")
    out.append("")
    out.append("  <STRUCTURE_LIST>")
    for st in layout.structures:
        out += _write_structure(st, "    ")
    out.append("  </STRUCTURE_LIST>")
    out.append("")
    out.append("</ELAYOUT>")
    out.append("")
    return "\n".join(out)


def write_ely(layout: Layout, path: str) -> None:
    """Write a Layout to a file path as ELAYOUT XML."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(dumps(layout))
