"""fib_dark_theme -- shared dark theme for the pyFIB tool family.

Drop this one file into any qtpy / PyQt5 / PySide2 app (elymaker, gisdep, ...)
to give it the same look. It is self-contained: no imports beyond qtpy, no
dependency on any of the tools.

Usage
-----
    from qtpy.QtWidgets import QApplication
    import fib_dark_theme

    app = QApplication(sys.argv)
    fib_dark_theme.apply_theme(app)     # <- one call, before you build widgets
    ...

If you already have an app instance you can just call ``apply_theme()`` with no
argument and it will use ``QApplication.instance()``.

What it does
------------
* Sets Qt's cross-platform **Fusion** base style (consistent + modern on
  Windows 7-11, unlike the dated native Windows style).
* Applies a dark, scope-room palette (so native-drawn bits match), then a
  light-touch stylesheet for spacing, rounded inputs, styled docks/menus/
  scrollbars, and a teal accent on checked/selected controls.

Two hooks worth knowing for gisdep
----------------------------------
* Give a prominent action button ``objectName("toolButton")`` to get the big
  rounded accent-on-checked treatment used by elymaker's Tools dock.
* The ``CANVAS_*`` colours below are exported for any custom-painted view so a
  canvas matches the chrome; ignore them if gisdep has no canvas.

Colours live in one place -- retune here and both apps move together.
"""

from __future__ import annotations

# ---- palette -------------------------------------------------------------- #
BG        = "#232833"   # window
BG_ALT    = "#1c212b"   # panels / input fields base
BG_RAISE  = "#2b313d"   # raised surfaces (buttons, dock/menu headers)
BORDER    = "#3a4150"
TEXT      = "#e7ebf2"
TEXT_DIM  = "#9aa3b2"
ACCENT    = "#17c3b2"   # teal
ACCENT_Hi = "#3ad9c7"
ACCENT_FG = "#06231f"   # text drawn on top of the accent colour
DANGER    = "#e0574a"

# canvas colours (only needed if the app has a custom-painted view)
CANVAS_BG   = "#12151b"
CANVAS_GRID = "#232a35"
CANVAS_AXIS = "#2f7d74"

_QSS_TEMPLATE = """
* {{ font-size: 12px; }}

QMainWindow, QWidget {{ background: {BG}; color: {TEXT}; }}

QMenuBar {{ background: {BG}; color: {TEXT}; border-bottom: 1px solid {BORDER}; }}
QMenuBar::item {{ padding: 5px 10px; background: transparent; }}
QMenuBar::item:selected {{ background: {BG_RAISE}; border-radius: 4px; }}
QMenu {{ background: {BG_RAISE}; color: {TEXT}; border: 1px solid {BORDER}; }}
QMenu::item {{ padding: 6px 22px; }}
QMenu::item:selected {{ background: {ACCENT}; color: {ACCENT_FG}; }}

QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{
    background: {BG_RAISE}; padding: 7px 10px;
    border: 1px solid {BORDER}; border-bottom: none;
    font-weight: 600; letter-spacing: 0.3px;
}}
QDockWidget > QWidget {{ border: 1px solid {BORDER}; }}

QGroupBox {{
    border: 1px solid {BORDER}; border-radius: 8px; margin-top: 10px;
    padding: 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {TEXT_DIM};
}}

QLabel {{ background: transparent; }}

/* prominent action buttons: give them objectName("toolButton") */
QToolButton#toolButton, QPushButton#toolButton {{
    background: {BG_ALT}; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 8px;
    padding: 8px 12px; text-align: left; font-size: 13px;
}}
QToolButton#toolButton:hover, QPushButton#toolButton:hover {{ border-color: {ACCENT}; }}
QToolButton#toolButton:checked, QPushButton#toolButton:checked {{
    background: {ACCENT}; color: {ACCENT_FG}; border-color: {ACCENT_Hi};
    font-weight: 700;
}}

/* generic buttons */
QToolButton, QPushButton {{
    background: {BG_ALT}; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 10px;
}}
QToolButton:hover, QPushButton:hover {{ border-color: {ACCENT}; }}
QToolButton:checked, QPushButton:checked {{
    background: {ACCENT}; color: {ACCENT_FG}; border-color: {ACCENT_Hi};
}}
QPushButton:disabled, QToolButton:disabled {{ color: {TEXT_DIM}; border-color: {BORDER}; }}

QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox, QPlainTextEdit, QTextEdit, QAbstractSpinBox {{
    background: {BG_ALT}; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 6px; padding: 4px 6px;
    selection-background-color: {ACCENT}; selection-color: {ACCENT_FG};
}}
QComboBox:hover, QLineEdit:hover, QDoubleSpinBox:hover, QSpinBox:hover {{ border-color: {ACCENT}; }}
QComboBox QAbstractItemView {{
    background: {BG_RAISE}; color: {TEXT};
    border: 1px solid {BORDER}; selection-background-color: {ACCENT};
    selection-color: {ACCENT_FG};
}}

QCheckBox, QRadioButton {{ spacing: 7px; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px; border: 1px solid {BORDER};
    border-radius: 4px; background: {BG_ALT};
}}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {ACCENT}; border-color: {ACCENT_Hi};
}}

QProgressBar {{
    border: 1px solid {BORDER}; border-radius: 6px; text-align: center;
    background: {BG_ALT}; color: {TEXT};
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}

QTabBar::tab {{
    background: {BG_ALT}; color: {TEXT_DIM};
    border: 1px solid {BORDER}; padding: 6px 12px;
    border-top-left-radius: 6px; border-top-right-radius: 6px;
}}
QTabBar::tab:selected {{ background: {BG_RAISE}; color: {TEXT}; }}

QStatusBar {{ background: {BG_RAISE}; color: {TEXT_DIM}; border-top: 1px solid {BORDER}; }}
QStatusBar QLabel {{ color: {TEXT_DIM}; }}

QScrollBar:vertical {{ background: {BG}; width: 11px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {ACCENT}; }}
QScrollBar:horizontal {{ background: {BG}; height: 11px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 5px; min-width: 24px; }}
QScrollBar::handle:horizontal:hover {{ background: {ACCENT}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
"""


def stylesheet() -> str:
    """Return the QSS string with palette colours substituted in."""
    return _QSS_TEMPLATE.format(
        BG=BG, BG_ALT=BG_ALT, BG_RAISE=BG_RAISE, BORDER=BORDER,
        TEXT=TEXT, TEXT_DIM=TEXT_DIM, ACCENT=ACCENT, ACCENT_Hi=ACCENT_Hi,
        ACCENT_FG=ACCENT_FG,
    )


def apply_theme(app=None) -> None:
    """Apply the Fusion base style, dark palette, and stylesheet to *app*.

    *app* defaults to the running ``QApplication.instance()``. Safe to call once
    at startup, before building the main window.
    """
    from qtpy.QtWidgets import QApplication
    from qtpy.QtGui import QColor, QPalette

    if app is None:
        app = QApplication.instance()
    if app is None:
        raise RuntimeError("No QApplication -- create one before apply_theme()")

    try:
        app.setStyle("Fusion")
    except Exception:  # pragma: no cover
        pass

    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(BG))
    pal.setColor(QPalette.WindowText, QColor(TEXT))
    pal.setColor(QPalette.Base, QColor(BG_ALT))
    pal.setColor(QPalette.AlternateBase, QColor(BG_RAISE))
    pal.setColor(QPalette.Text, QColor(TEXT))
    pal.setColor(QPalette.Button, QColor(BG_RAISE))
    pal.setColor(QPalette.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.ToolTipBase, QColor(BG_RAISE))
    pal.setColor(QPalette.ToolTipText, QColor(TEXT))
    pal.setColor(QPalette.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor(ACCENT_FG))
    pal.setColor(QPalette.PlaceholderText, QColor(TEXT_DIM))
    pal.setColor(QPalette.Link, QColor(ACCENT_Hi))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(TEXT_DIM))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(TEXT_DIM))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet())


if __name__ == "__main__":  # write the raw .qss next to this file
    with open("fib_dark_theme.qss", "w") as fh:
        fh.write(stylesheet())
    print("wrote fib_dark_theme.qss")
