"""PyQt5/PySide2 (via qtpy) editor for elymaker.

Optional subpackage: importing it requires a Qt binding. The headless core
(elymaker.model/reader/writer/builder/validate/preview) has no such dependency.
"""

from .app import main

__all__ = ["main"]
