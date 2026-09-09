"""Map GIS readback state to a chamber line-art image + caption.

Pure logic (no Qt), so it is unit-tested. The three artworks were provided for GIS1:
    cryo_normal.jpg       needle Parked + valve Closed (ready)
    cryo_gis1_insert.jpg  needle Inserted + valve Closed
    cryo_gis1_on.jpg      needle Inserted + valve Open (glowing tip = depositing)

GIS2 has no dedicated art yet, so it reuses the GIS1 images with a GIS2 caption -- a
placeholder to swap out when GIS2 line-art is available.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from gisdep.gis.state import (
    NEEDLE_PARKED,
    NEEDLE_INSERTED,
    NEEDLE_UNKNOWN,
    VALVE_CLOSED,
    VALVE_OPEN,
    VALVE_TRANSIT,
)

_ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

NORMAL = "cryo_normal.jpg"
GIS_INSERT = "cryo_gis1_insert.jpg"
GIS_ON = "cryo_gis1_on.jpg"
START = "cryo_start.jpg"       # shown just after GISdep loads (ready, with X/Z hints)
BLOCKED = "cryo_blocked.jpg"   # shown when a needle insertion is refused

ALL_ASSETS = (NORMAL, GIS_INSERT, GIS_ON, START, BLOCKED)


def asset_path(filename: str) -> str:
    return os.path.join(_ASSET_DIR, filename)


@dataclass
class ImageState:
    filename: str
    caption: str
    depositing: bool = False
    transit: bool = False


def image_for_gis(needle_raw, valve_raw, port: int = 1) -> ImageState:
    """Pick the image + caption for one port's (needle, valve) readback."""
    p = f"GIS{port}"
    if needle_raw == NEEDLE_INSERTED and valve_raw == VALVE_OPEN:
        return ImageState(GIS_ON, f"{p} inserted — valve OPEN, depositing", depositing=True)
    if needle_raw == NEEDLE_INSERTED and valve_raw == VALVE_CLOSED:
        return ImageState(GIS_INSERT, f"{p} inserted — valve closed")
    if needle_raw == NEEDLE_INSERTED and valve_raw == VALVE_TRANSIT:
        return ImageState(GIS_INSERT, f"{p} inserted — valve moving…", transit=True)
    if needle_raw == NEEDLE_PARKED and valve_raw == VALVE_CLOSED:
        return ImageState(NORMAL, "Ready — needle parked, valve closed")
    if needle_raw == NEEDLE_UNKNOWN:
        return ImageState(NORMAL, f"{p} needle moving…", transit=True)
    # anything else (e.g. an unexpected combo) -- surface it, treat as transitional
    return ImageState(NORMAL, f"{p} needle={needle_raw}, valve={valve_raw}", transit=True)


def active_port_state(readings) -> ImageState:
    """Choose which port drives the big image.

    If GIS2's needle is doing something (not Parked), show GIS2; otherwise GIS1. This
    keeps the image following whichever port is active in a single-port workflow.
    ``readings`` is a dict {port: GISPortReading} from gisdep.gis.read_all.
    """
    r2 = readings.get(2)
    if r2 is not None and not r2.needle_parked:
        return image_for_gis(r2.needle_raw, r2.valve_raw, port=2)
    r1 = readings.get(1)
    if r1 is None:
        return ImageState(NORMAL, "no GIS1 reading")
    return image_for_gis(r1.needle_raw, r1.valve_raw, port=1)
