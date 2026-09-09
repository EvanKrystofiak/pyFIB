from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union


_RE_FLOAT = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def _parse_number(s: str) -> float:
    m = _RE_FLOAT.search(s.strip())
    if not m:
        raise ValueError(f"Could not parse number from: {s!r}")
    return float(m.group(0))


def _parse_with_units(s: str, expected_unit: Optional[str] = None) -> float:
    """Parse strings like '2e-006 m' or '7e-010 A' or '0 deg'."""
    s = s.strip()
    val = _parse_number(s)
    if expected_unit is not None:
        # Not strict; just sanity check if unit token appears.
        if expected_unit not in s:
            # allow missing unit
            pass
    return val


@dataclass(frozen=True)
class Probe:
    name: str
    current_A: float
    diameter_m: Optional[float] = None


@dataclass(frozen=True)
class Exposure:
    file: str
    version: Optional[str] = None
    probe: Optional[Probe] = None


@dataclass(frozen=True)
class Rect:
    """A single <RECT> milling element from an .ely ELayout file.

    Coordinates and sizes in ELayout are typically expressed in microns (µm) for x/y/width/height,
    while z_extent is expressed in metres in the file (e.g., '2e-006 m').
    """

    x_um: float
    y_um: float
    width_um: float
    height_um: float
    angle_deg: float
    z_extent_m: Optional[float]
    dose_factor: Optional[float]
    exposure: Optional[Exposure]
    raw_attrib: Dict[str, str]

    def area_um2(self) -> float:
        return abs(self.width_um * self.height_um)

    def bbox_um(self) -> Tuple[float, float, float, float]:
        """Approximate axis-aligned bounding box ignoring rotation."""
        w = abs(self.width_um)
        h = abs(self.height_um)
        return (self.x_um - w / 2, self.y_um - h / 2, self.x_um + w / 2, self.y_um + h / 2)


@dataclass
class ELayoutPattern:
    name: str
    version: Optional[str]
    rects: List[Rect]
    source_path: Optional[Path] = None

    @staticmethod
    def from_file(path: Union[Path, str]) -> "ELayoutPattern":
        path = Path(path)
        tree = ET.parse(path)
        root = tree.getroot()

        if root.tag != "ELAYOUT":
            raise ValueError(f"Not an ELayout (.ely) file: root tag is {root.tag!r}")

        name = root.attrib.get("name", path.stem)
        version = root.attrib.get("version")

        rects: List[Rect] = []
        for rect_el in root.iter("RECT"):
            attrib = dict(rect_el.attrib)

            x_um = float(attrib.get("x", "0"))
            y_um = float(attrib.get("y", "0"))
            width_um = float(attrib.get("width", "0"))
            height_um = float(attrib.get("height", "0"))

            angle_deg = _parse_with_units(attrib.get("angle", "0 deg"), expected_unit="deg")

            z_extent_m = None
            if "z_extent" in attrib:
                z_extent_m = _parse_with_units(attrib["z_extent"], expected_unit="m")

            dose_factor = None
            if "dose_factor" in attrib:
                try:
                    dose_factor = float(attrib["dose_factor"])
                except Exception:
                    dose_factor = None

            exposure: Optional[Exposure] = None
            exp_el = rect_el.find("EXPOSURE")
            if exp_el is not None:
                exp_file = exp_el.attrib.get("file", "")
                exp_version = exp_el.attrib.get("version")
                probe = None
                probe_el = exp_el.find("PROBE")
                if probe_el is not None:
                    probe = Probe(
                        name=probe_el.attrib.get("name", ""),
                        current_A=_parse_with_units(probe_el.attrib.get("current", "0 A"), expected_unit="A"),
                        diameter_m=(
                            _parse_with_units(probe_el.attrib.get("diameter"), expected_unit="m")
                            if "diameter" in probe_el.attrib
                            else None
                        ),
                    )
                exposure = Exposure(file=exp_file, version=exp_version, probe=probe)

            rects.append(
                Rect(
                    x_um=x_um,
                    y_um=y_um,
                    width_um=width_um,
                    height_um=height_um,
                    angle_deg=angle_deg,
                    z_extent_m=z_extent_m,
                    dose_factor=dose_factor,
                    exposure=exposure,
                    raw_attrib=attrib,
                )
            )

        return ELayoutPattern(name=name, version=version, rects=rects, source_path=path)

    def exposure_files(self) -> List[str]:
        files = []
        for r in self.rects:
            if r.exposure and r.exposure.file:
                files.append(r.exposure.file)
        # preserve order, remove duplicates
        seen = set()
        out: List[str] = []
        for f in files:
            if f not in seen:
                out.append(f)
                seen.add(f)
        return out

    def bbox_um(self) -> Tuple[float, float, float, float]:
        if not self.rects:
            return (0.0, 0.0, 0.0, 0.0)
        xs: List[float] = []
        ys: List[float] = []
        for r in self.rects:
            x0, y0, x1, y1 = r.bbox_um()
            xs.extend([x0, x1])
            ys.extend([y0, y1])
        return (min(xs), min(ys), max(xs), max(ys))

    def summary_dict(self) -> Dict[str, object]:
        bbox = self.bbox_um()
        currents = sorted({r.exposure.probe.current_A for r in self.rects if (r.exposure and r.exposure.probe)})
        return {
            "name": self.name,
            "version": self.version,
            "rect_count": len(self.rects),
            "bbox_um": bbox,
            "unique_exposure_files": self.exposure_files(),
            "unique_probe_currents_A": currents,
        }

    def rewrite_exposure_file(self, new_path: str, *, in_place: bool = False, out_path: Optional[Path] = None) -> Path:
        """Rewrite all <EXPOSURE file='...'> attributes to point to new_path.

        This helps when the .ely file contains an absolute path that isn't valid on your microscope PC.
        """
        if self.source_path is None:
            raise ValueError("rewrite_exposure_file requires source_path (pattern loaded from disk).")

        tree = ET.parse(self.source_path)
        root = tree.getroot()
        for exp_el in root.iter("EXPOSURE"):
            if "file" in exp_el.attrib:
                exp_el.attrib["file"] = new_path

        if in_place:
            tree.write(self.source_path, encoding="UTF-8", xml_declaration=True)
            return self.source_path

        if out_path is None:
            out_path = self.source_path.with_name(self.source_path.stem + "_rewritten.ely")
        tree.write(out_path, encoding="UTF-8", xml_declaration=True)
        return out_path
