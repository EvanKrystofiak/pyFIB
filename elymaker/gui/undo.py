"""Undo / redo for the pattern editor.

Snapshot model: the whole pattern (shapes, order, per-shape properties, marked
edges) is captured as a core ``Layout`` via ``scene.to_layout()`` -- which
already round-trips losslessly -- and pushed on a stack. One snapshot is taken
per committed action (add a shape, change a parameter, move, delete, duplicate),
so undo/redo step through actions one at a time.

Images are intentionally *not* part of the snapshot: ``scene.load_layout`` only
rebuilds shapes and leaves the image underlay untouched, so undo/redo never
disturbs the loaded FIB image.

The stack depth is small on purpose (default 5).
"""

from __future__ import annotations

import copy
from typing import List, Optional


class UndoManager:
    def __init__(self, scene, depth: int = 5) -> None:
        self.scene = scene
        self.depth = depth
        self._undo: List = []
        self._redo: List = []
        self._current = scene.to_layout()
        self._restoring = False
        self.on_change = lambda: None   # set by the window to refresh buttons

    # -- lifecycle -------------------------------------------------------- #
    def reset(self) -> None:
        """Forget history and take the current scene as the baseline."""
        self._undo.clear()
        self._redo.clear()
        self._current = self.scene.to_layout()
        self.on_change()

    def commit(self) -> None:
        """Record that a mutation just happened. Call *after* the change."""
        if self._restoring:
            return
        self._undo.append(self._current)
        if len(self._undo) > self.depth:
            self._undo.pop(0)
        self._redo.clear()
        self._current = self.scene.to_layout()
        self.on_change()

    # -- queries ---------------------------------------------------------- #
    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    # -- actions ---------------------------------------------------------- #
    def undo(self) -> None:
        if not self._undo:
            return
        self._redo.append(self._current)
        self._current = self._undo.pop()
        self._restore(self._current)
        self.on_change()

    def redo(self) -> None:
        if not self._redo:
            return
        self._undo.append(self._current)
        self._current = self._redo.pop()
        self._restore(self._current)
        self.on_change()

    # -- internals -------------------------------------------------------- #
    def _restore(self, layout) -> None:
        self._restoring = True
        try:
            # deep-copy so the stored snapshot is not mutated by later edits
            self.scene.load_layout(copy.deepcopy(layout))
        finally:
            self._restoring = False
