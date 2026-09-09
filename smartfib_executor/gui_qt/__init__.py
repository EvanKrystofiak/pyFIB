"""pyFIB Qt GUI (re-skin in progress). Requires qtpy/PyQt5.

    python -m smartfib_executor.gui_qt

Step 1 is the scaffold (theme + nav/tabs + subprocess-fed Log). Later phases fill in
the Capture / Positions / Mill / Tools pages. The engine and CLI are untouched -- the
GUI shells out to the same commands, exactly like the tkinter GUI.
"""
from __future__ import annotations

__all__ = ["main"]


def main(argv=None, positions_file=None) -> int:
    from .app import main as _main
    return _main(argv, positions_file=positions_file)
