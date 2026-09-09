"""Per-session positions directory, matching the convention pyFIB used before.

Each new session gets its own timestamped folder so the positions JSON and the BMP
grabs stay grouped together:

    <project_root>/positions/session_<YYYYMMDD_HHMMSS>/positions.json
    <project_root>/positions/session_<YYYYMMDD_HHMMSS>/images/<name>.bmp

An old session is loaded simply by pointing at its positions.json (Load Previous…).
Nothing is created on disk until the first capture writes to it.
"""

from __future__ import annotations

import time
from pathlib import Path


def project_root() -> Path:
    # gui_qt/session.py -> gui_qt -> smartfib_executor -> project root
    return Path(__file__).resolve().parent.parent.parent


def new_session_positions_path() -> Path:
    ts = time.strftime("%Y%m%d_%H%M%S")
    return project_root() / "positions" / f"session_{ts}" / "positions.json"


def images_dir_for(positions_file: str) -> Path:
    """The images folder that belongs beside a positions file."""
    return Path(positions_file).resolve().parent / "images"
