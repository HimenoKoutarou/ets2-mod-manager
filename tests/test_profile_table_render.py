"""Regression test for the asynchronous Profile table renderer."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtWidgets import QApplication

from core.models import Mod
from ui._mw_mixins._table_data_mixin import _TableDataMixin
from ui._mw_mixins._toolbar_mixin import _ToolbarMixin
from ui._mw_widgets import ModTable


class _StatusBar:
    def showMessage(self, *args):
        pass


class _RenderHarness(_TableDataMixin):
    def __init__(self):
        mod = Mod("example", "example.scs", "scs", file_size=1024)
        self.table_all = ModTable()
        self.table_active = ModTable()
        self.table = self.table_all
        self.current_profile = SimpleNamespace(location="local", profile_id="p", profile_sii="profile.sii")
        self.current_worklist = [{"package_name": "example", "enabled": True, "order": 0, "mod": mod}]
        self.all_mods = [mod]
        self.all_mods_by_pkg = {"example": mod}
        self._all_mods_by_id = {"example": mod}
        self._worklist_profile_key = ("local", "p", "profile.sii")
        self._profile_render_token = 0
        self._status = _StatusBar()

    def statusBar(self):
        return self._status

    def _reorder_table_for(self, table):
        pass

    def _apply_filter_to_table(self):
        pass

    def _refresh_status_after_change(self):
        pass


class _EditableTable:
    def __init__(self):
        self.calls = 0

    def set_editable(self, editable):
        self.calls += 1


class _EditableHarness:
    def __init__(self):
        self._profile_editable = True
        self.table_all = _EditableTable()
        self.table_active = _EditableTable()


def main() -> int:
    app = QApplication.instance() or QApplication([])
    harness = _RenderHarness()
    harness._start_profile_table_render(1)
    for _ in range(5):
        app.processEvents()
    assert harness.table_all.rowCount() == 1
    assert harness._profile_table_pending_key == ("local", "p", "profile.sii")

    editable = _EditableHarness()
    _ToolbarMixin._set_profile_editable_state(
        editable, SimpleNamespace(location="local")
    )
    assert editable.table_all.calls == 0
    assert editable.table_active.calls == 0
    print("PASS Profile renderer populates the all-mods table")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
