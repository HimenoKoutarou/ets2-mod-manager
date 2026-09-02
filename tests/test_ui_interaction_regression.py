"""Headless UI smoke test for startup and read-only interaction paths."""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow


def main() -> int:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    results: list[str] = []

    def finish() -> None:
        try:
            assert window.isVisible() and window.isEnabled()
            assert window.table_all.rowCount() > 0
            assert window.table_active is not None
            results.append("startup")

            row = window.table_all.item(0, 0)
            assert row is not None and bool(row.flags() & Qt.ItemIsUserCheckable)
            old_state = row.checkState()
            row_rect = window.table_all.visualItemRect(row)
            QTest.mouseClick(
                window.table_all.viewport(),
                Qt.LeftButton,
                pos=QPoint(row_rect.left() + 5, row_rect.center().y()),
            )
            app.processEvents()
            assert row.checkState() != old_state
            assert window.isEnabled()
            results.append("checkbox")

            # Use real mouse events. Programmatic setCurrentIndex/setCheckState
            # can pass even when a disabled overlay intercepts user input.
            tab_bar = window.tab_mods.tabBar()
            QTest.mouseClick(tab_bar, Qt.LeftButton, pos=tab_bar.tabRect(1).center())
            app.processEvents()
            assert window._current_mod_tab == "active"
            assert window.table is window.table_active
            assert window.table_all.rowCount() > 0
            results.append("tab")

            QTest.mouseClick(
                window.search_input,
                Qt.LeftButton,
                pos=window.search_input.rect().center(),
            )
            QTest.keyClicks(window.search_input, "no-such-mod")
            app.processEvents()
            assert window.search_input.text() == "no-such-mod"
            results.append("search")

            window._set_profile_editable_state(SimpleNamespace(location="cloud"))
            app.processEvents()
            row = window.table_all.item(0, 0)
            assert window.isEnabled() and window.table_all.isEnabled()
            assert window.table_active.isEnabled() and window.tree_categories.isEnabled()
            assert row is not None and not bool(row.flags() & Qt.ItemIsUserCheckable)
            assert row is not None and not bool(row.flags() & Qt.ItemIsDragEnabled)
            results.append("readonly-browse")

            window._set_profile_editable_state(SimpleNamespace(location="local"))
            app.processEvents()
            row = window.table_all.item(0, 0)
            assert row is not None and bool(row.flags() & Qt.ItemIsUserCheckable)
            assert row is not None and bool(row.flags() & Qt.ItemIsDragEnabled)
            results.append("local-edit")
            print("PASS " + ",".join(results), flush=True)
        finally:
            for worker in window._background_workers():
                if worker.isRunning() and hasattr(worker, "stop"):
                    worker.stop()
                    worker.wait(5000)
            window.close()
            QTimer.singleShot(200, app.quit)

    QTimer.singleShot(12000, finish)
    QTimer.singleShot(60000, app.quit)
    app.exec()
    return 0 if len(results) == 6 else 1


if __name__ == "__main__":
    raise SystemExit(main())
