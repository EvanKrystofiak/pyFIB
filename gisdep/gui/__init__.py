"""gisdep GUI.

Importing this package does NOT import Qt -- only the pure state->image mapping is
exposed here. The Qt application lives in gisdep.gui.app and is imported lazily
(when you launch the GUI), so the rest of gisdep imports and tests without PyQt5.

Launch:  python -m gisdep.gui   (requires: pip install PyQt5 qtpy)
"""

from gisdep.gui.state_images import (
    ImageState,
    image_for_gis,
    active_port_state,
    asset_path,
    ALL_ASSETS,
    NORMAL,
    GIS_INSERT,
    GIS_ON,
    START,
    BLOCKED,
)

__all__ = [
    "ImageState",
    "image_for_gis",
    "active_port_state",
    "asset_path",
    "ALL_ASSETS",
    "NORMAL",
    "GIS_INSERT",
    "GIS_ON",
    "START",
    "BLOCKED",
]
