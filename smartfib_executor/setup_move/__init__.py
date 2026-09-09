"""Setup-position moves (via CLEM SAFE). Isolated; dry-run-first.

    python -m smartfib_executor.setup_move --list
    python -m smartfib_executor.setup_move "Cryo Insertion"           # dry-run
    python -m smartfib_executor.setup_move "Cryo Insertion" --arm      # execute
    python -m smartfib_executor.setup_move "Grid1 Milling" --simulate  # offline
"""
from __future__ import annotations

from .steps import (move_to_setup_position, MoveResult, CLEM_SAFE_NAME,
                    CLEM_NEAR_LINEAR_M, ARRIVE_LINEAR_M, ARRIVE_ANGULAR_DEG,
                    PHASES_TO_CLEM, PHASES_TO_DEST)
from .scope import MoveScope
from .loader import load_setup_db, DEFAULT_SETUP_FILE

__all__ = ["move_to_setup_position", "MoveResult", "MoveScope", "load_setup_db",
           "DEFAULT_SETUP_FILE", "CLEM_SAFE_NAME", "CLEM_NEAR_LINEAR_M",
           "ARRIVE_LINEAR_M", "ARRIVE_ANGULAR_DEG", "PHASES_TO_CLEM", "PHASES_TO_DEST"]
