"""Shared test fixtures.

The Qt tests run headless via the *offscreen* platform. A single, session-scoped
QApplication is shared across all modules; per-test widgets are explicitly closed
and deleted in teardown so C++ objects are destroyed while the application is
still alive. Without this, PyQt can segfault at interpreter shutdown when
leftover QGraphics objects are collected in an arbitrary order.
"""

import gc
import os

import pytest

# must be set before any QApplication is created
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from qtpy.QtWidgets import QApplication
    _HAVE_QT = True
except Exception:  # pragma: no cover
    _HAVE_QT = False


@pytest.fixture(scope="session")
def qapp():
    if not _HAVE_QT:
        pytest.skip("no Qt binding installed")
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


@pytest.fixture()
def qt_cleanup(qapp):
    """Track widgets/objects created in a test and delete them afterwards."""
    created = []
    yield created
    for obj in created:
        try:
            if hasattr(obj, "close"):
                obj.close()
            obj.setParent(None)
            obj.deleteLater()
        except Exception:
            pass
    qapp.processEvents()
    gc.collect()
    qapp.processEvents()
