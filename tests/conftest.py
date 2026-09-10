from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_QT_APP = None


def pytest_configure(config):
    """Keep one QApplication alive before pytest executes Qt widget tests."""
    global _QT_APP
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:
        return
    _QT_APP = QApplication.instance() or QApplication([])
