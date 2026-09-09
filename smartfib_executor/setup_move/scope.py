"""Dry-run-aware stage-move scope for setup moves (reuses prep_cryo's pattern)."""
from __future__ import annotations

from typing import Callable, Optional


class MoveScope:
    """Gates the ONLY mutation (move_stage) on ``armed``; reads always happen."""

    def __init__(self, sem, *, armed: bool = False,
                 logger: Optional[Callable[[str], None]] = None):
        self._sem = sem
        self.armed = bool(armed)
        self._log = logger or (lambda m: None)

    def get_float(self, mnemonic: str) -> Optional[float]:
        try:
            return float(self._sem.get_float(mnemonic))
        except Exception:
            return None

    def move_stage(self, x, y, z, t, r, m, *, note: str = "") -> None:
        desc = (f"x={x * 1e3:.4f} y={y * 1e3:.4f} z={z * 1e3:.4f} mm, "
                f"t={t:.3f} r={r:.3f} deg, m={m * 1e3:.4f} mm")
        if not self.armed:
            self._log(f"    [dry-run] would move stage ({note}): {desc}")
            return
        self._log(f"    move stage ({note}): {desc}")
        self._sem.move_stage(x, y, z, t, r, m)
