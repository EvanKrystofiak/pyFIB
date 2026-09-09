"""Shared dark theme -- the same look elymaker uses.

Reconstructed apply_theme() + colour constants around the bundled fib_dark_theme.qss
(the file Evan provided). apply_theme() does three things, per the theme README: sets the
Fusion base style, applies a dark palette (so native-drawn widgets match), then the
stylesheet. Using only the .qss skips the palette, so prefer apply_theme().

Colour constants mirror elymaker/gui/style.py. If Evan later folds this exact file into
the elymaker package as a shared export, gisdep should import from there instead.
"""

from __future__ import annotations

import os

from qtpy.QtGui import QColor, QPalette
from qtpy.QtWidgets import QStyleFactory

# palette (identical to the values used throughout fib_dark_theme.qss)
BG = "#232833"          # window / main background
PANEL = "#2b313d"       # docks, menus, raised panels
INPUT_BG = "#1c212b"    # inputs, buttons, log
BORDER = "#3a4150"
TEXT = "#e7ebf2"
MUTED = "#9aa3b2"
ACCENT = "#17c3b2"      # teal
ACCENT_BRIGHT = "#3ad9c7"
ACCENT_TEXT = "#06231f"  # dark text used on top of the accent

# canvas exports (parity with elymaker; gisdep has no canvas yet)
CANVAS_BG = "#1c212b"
GRID = "#3a4150"
AXIS = "#9aa3b2"

_QSS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fib_dark_theme.qss")


def qss() -> str:
    with open(_QSS_PATH, "r", encoding="utf-8") as fh:
        return fh.read()


def dark_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.Window, QColor(BG))
    p.setColor(QPalette.WindowText, QColor(TEXT))
    p.setColor(QPalette.Base, QColor(INPUT_BG))
    p.setColor(QPalette.AlternateBase, QColor(PANEL))
    p.setColor(QPalette.Text, QColor(TEXT))
    p.setColor(QPalette.Button, QColor(INPUT_BG))
    p.setColor(QPalette.ButtonText, QColor(TEXT))
    p.setColor(QPalette.ToolTipBase, QColor(PANEL))
    p.setColor(QPalette.ToolTipText, QColor(TEXT))
    p.setColor(QPalette.Highlight, QColor(ACCENT))
    p.setColor(QPalette.HighlightedText, QColor(ACCENT_TEXT))
    if hasattr(QPalette, "PlaceholderText"):
        p.setColor(QPalette.PlaceholderText, QColor(MUTED))
    # dim disabled text
    p.setColor(QPalette.Disabled, QPalette.Text, QColor(MUTED))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(MUTED))
    p.setColor(QPalette.Disabled, QPalette.WindowText, QColor(MUTED))
    return p


def apply_theme(app) -> None:
    """Fusion base style + dark palette + stylesheet."""
    try:
        app.setStyle(QStyleFactory.create("Fusion"))
    except Exception:
        pass
    app.setPalette(dark_palette())
    app.setStyleSheet(qss())
