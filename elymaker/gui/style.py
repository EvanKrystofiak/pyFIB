"""Application theme.

Uses Qt's cross-platform **Fusion** style as the base (consistent and far more
modern than the native Windows style), a dark palette tuned for a scope-room,
and a light-touch stylesheet for spacing, rounded controls, and an accent on the
checked drawing tool. Kept in one place so the look can be retuned without
hunting through widget code.

Colours are deliberately conservative -- this is an instrument tool, not a game.
The teal accent matches the marked-edge highlight drawn on rectangles.
"""

from __future__ import annotations

# palette
BG        = "#232833"   # window
BG_ALT    = "#1c212b"   # panels / inputs base
BG_RAISE  = "#2b313d"   # raised (buttons, headers)
BORDER    = "#3a4150"
TEXT      = "#e7ebf2"
TEXT_DIM  = "#9aa3b2"
ACCENT    = "#17c3b2"   # teal
ACCENT_Hi = "#3ad9c7"
DANGER    = "#e0574a"

_QSS = """
* {{ font-size: 12px; }}

QMainWindow, QWidget {{ background: {BG}; color: {TEXT}; }}

QMenuBar {{ background: {BG}; color: {TEXT}; border-bottom: 1px solid {BORDER}; }}
QMenuBar::item {{ padding: 5px 10px; background: transparent; }}
QMenuBar::item:selected {{ background: {BG_RAISE}; border-radius: 4px; }}
QMenu {{ background: {BG_RAISE}; color: {TEXT}; border: 1px solid {BORDER}; }}
QMenu::item {{ padding: 6px 22px; }}
QMenu::item:selected {{ background: {ACCENT}; color: #06231f; }}

QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{
    background: {BG_RAISE}; padding: 7px 10px;
    border: 1px solid {BORDER}; border-bottom: none;
    font-weight: 600; letter-spacing: 0.3px;
}}
QDockWidget > QWidget {{ border: 1px solid {BORDER}; }}

QLabel {{ background: transparent; }}

/* drawing-tool buttons in the Tools dock */
QToolButton#toolButton {{
    background: {BG_ALT}; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 8px;
    padding: 8px 12px; text-align: left; font-size: 13px;
}}
QToolButton#toolButton:hover {{ border-color: {ACCENT}; }}
QToolButton#toolButton:checked {{
    background: {ACCENT}; color: #06231f; border-color: {ACCENT_Hi};
    font-weight: 700;
}}

/* generic tool buttons (e.g. edge picker) */
QToolButton {{
    background: {BG_ALT}; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 6px; padding: 4px 6px;
}}
QToolButton:hover {{ border-color: {ACCENT}; }}
QToolButton:checked {{ background: {ACCENT}; color: #06231f; border-color: {ACCENT_Hi}; }}

QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox, QPlainTextEdit {{
    background: {BG_ALT}; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 6px; padding: 4px 6px;
    selection-background-color: {ACCENT}; selection-color: #06231f;
}}
QComboBox:hover, QLineEdit:hover, QDoubleSpinBox:hover {{ border-color: {ACCENT}; }}
QComboBox QAbstractItemView {{
    background: {BG_RAISE}; color: {TEXT};
    border: 1px solid {BORDER}; selection-background-color: {ACCENT};
    selection-color: #06231f;
}}

QCheckBox {{ spacing: 7px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border: 1px solid {BORDER};
    border-radius: 4px; background: {BG_ALT};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT_Hi}; }}

QStatusBar {{ background: {BG_RAISE}; color: {TEXT_DIM}; border-top: 1px solid {BORDER}; }}
QStatusBar QLabel {{ color: {TEXT_DIM}; }}

QScrollBar:vertical {{ background: {BG}; width: 11px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {ACCENT}; }}
QScrollBar:horizontal {{ background: {BG}; height: 11px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 5px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
""".format(BG=BG, BG_ALT=BG_ALT, BG_RAISE=BG_RAISE, BORDER=BORDER,
           TEXT=TEXT, TEXT_DIM=TEXT_DIM, ACCENT=ACCENT, ACCENT_Hi=ACCENT_Hi)


def apply_theme(app) -> None:
    """Apply the Fusion base style, dark palette, and stylesheet to *app*."""
    try:
        from qtpy.QtGui import QColor, QPalette
    except Exception:  # pragma: no cover
        return

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
    pal.setColor(QPalette.HighlightedText, QColor("#06231f"))
    pal.setColor(QPalette.PlaceholderText, QColor(TEXT_DIM))
    pal.setColor(QPalette.Link, QColor(ACCENT_Hi))
    app.setPalette(pal)
    app.setStyleSheet(_QSS)


# canvas colours (used by the view/scene for a cohesive look)
CANVAS_BG = "#12151b"
CANVAS_GRID = "#232a35"
CANVAS_AXIS = "#2f7d74"
