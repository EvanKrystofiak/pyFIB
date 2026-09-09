"""elymaker - author ZEISS SmartFIB ELAYOUT (.ely) milling-pattern files.

Standalone authoring/round-trip library for pyFIB. Pure geometry + XML I/O;
no COM, no instrument access. Windows-only constraints do NOT apply here.

v0.1.0 - headless core:
  * model      : dataclass representation of an ELAYOUT document
  * reader     : parse .ely -> Layout
  * writer     : Layout -> .ely (native-looking, lossless round-trip)
  * builder    : fluent API to author patterns programmatically
  * probes     : standard 30 kV probe library seeded from real files
  * validate   : geometry / parameter sanity checks (warnings, never blocks)
  * preview    : Layout -> SVG for visual QA without SmartFIB
  * cli        : info / validate / preview / roundtrip commands
"""

from .model import (
    Layout,
    Layer,
    Structure,
    LayerReference,
    Exposure,
    Probe,
    Rect,
    Polygon,
    Arc,
    Text,
    Shape,
)
from .reader import read_ely, loads
from .writer import write_ely, dumps
from .builder import LayoutBuilder
from .probes import PROBES, get_probe
from .validate import validate

__version__ = "0.9.1"

__all__ = [
    "Layout",
    "Layer",
    "Structure",
    "LayerReference",
    "Exposure",
    "Probe",
    "Rect",
    "Polygon",
    "Arc",
    "Text",
    "Shape",
    "read_ely",
    "loads",
    "write_ely",
    "dumps",
    "LayoutBuilder",
    "PROBES",
    "get_probe",
    "validate",
    "__version__",
]
