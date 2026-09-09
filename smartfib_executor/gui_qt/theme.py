"""Apply the shared fib_dark_theme (same look as elymaker / gisdep) to the Qt app.

The canonical theme lives in smartfib_executor/assets/fib_dark_theme.py -- the exact
file the sibling tools use. We load it from there so pyFIB's Qt GUI matches the family
by construction and there is a single palette source.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Optional

_ASSET = Path(__file__).resolve().parent.parent / "assets" / "fib_dark_theme.py"


def _load_theme_module():
    spec = importlib.util.spec_from_file_location("fib_dark_theme", _ASSET)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load theme from {_ASSET}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def apply_theme(app=None) -> None:
    """Apply Fusion base style + dark palette + stylesheet, from the shared asset."""
    theme = _load_theme_module()
    theme.apply_theme(app)


def scale_up(app=None, *, point_size: int = 11) -> None:
    """Enlarge fonts and control sizes for readability, ON TOP of the shared theme.

    The shared fib_dark_theme (used by elymaker/gisdep) is left untouched; this only
    appends an override stylesheet and bumps the application font, so pyFIB can run a
    larger, more usable scale without diverging the palette.
    """
    from qtpy.QtWidgets import QApplication
    from qtpy.QtGui import QFont
    if app is None:
        app = QApplication.instance()
    if app is None:
        return
    f = app.font()
    f.setPointSize(int(point_size))
    app.setFont(f)
    extra = """
    QWidget { font-size: 15px; }
    QPushButton, QToolButton, QLineEdit, QComboBox, QAbstractSpinBox {
        min-height: 30px; padding: 6px 12px;
    }
    QToolButton#toolButton, QPushButton#toolButton {
        padding: 11px 18px; font-size: 16px;
    }
    QPushButton#armButton { min-height: 34px; font-size: 16px; }
    QHeaderView::section { padding: 8px 10px; font-size: 14px; }
    QTableWidget, QPlainTextEdit { font-size: 14px; }
    QComboBox { min-width: 120px; }
    """
    app.setStyleSheet(app.styleSheet() + extra)


def palette():
    """Return the theme's colour constants (BG, ACCENT, TEXT, ...) as a dict."""
    theme = _load_theme_module()
    keys = ("BG", "BG_ALT", "BG_RAISE", "BORDER", "TEXT", "TEXT_DIM",
            "ACCENT", "ACCENT_Hi", "ACCENT_FG", "DANGER")
    return {k: getattr(theme, k, None) for k in keys}
