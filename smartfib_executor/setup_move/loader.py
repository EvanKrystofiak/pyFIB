"""Load the setup fixtures (setup_positions.json shipped in assets/)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

DEFAULT_SETUP_FILE = Path(__file__).resolve().parent.parent / "assets" / "setup_positions.json"


def load_setup_db(path: Optional[str] = None):
    from ..positions import PositionDatabase
    return PositionDatabase.load(Path(path) if path else DEFAULT_SETUP_FILE)
