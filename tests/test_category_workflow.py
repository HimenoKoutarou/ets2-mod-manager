"""Focused regression checks for category assignment and batch operations.

Run directly with:
    QT_QPA_PLATFORM=offscreen python tests/test_category_workflow.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from unittest.mock import patch

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QApplication

from core.models import Mod, ModManifest
from services.priority_service import PriorityService
from ui._mw_widgets import ModTable, COL_ENABLED, COL_PKG
from ui._mw_mixins._signal_mixin import _SignalMixin
from ui._mw_mixins._table_data_mixin import _TableDataMixin


def _mod(name: str, category: str = "") -> Mod:
    m = Mod(
        mod_id=name,
        package_path=name,
        package_type="directory",
        manifest=ModManifest(package_name=name, display_name=name),
    )
    # Avoid writing the user's real category cache in this focused test.
    m._category_tag = category
    return m


class _FakeMain(_TableDataMixin):
    def __init__(self, mods, worklist):
        self.all_mods = list(mods)
        self.all_mods_by_pkg = self._build_mod_index(self.all_mods)
        self.priority_svc = PriorityService(self.all_mods)
        self.current_worklist = worklist
        self.current_profile = object()
        self.table_all = ModTable()
        self.table_active = ModTable()
        self.table = self.table_all
        self._mod_tables = {"all": self.table_all, "active": self.table_active}
        self._current_filter_cat = None
        self._search_keyword = ""
        self._current_mod_tab = "all"
        self._profile_fill_pending = False
        self._dirty_priority = False
        self._updating_category_checks = False

    def _apply_filter_to_table(self):
        # Rendering is what this test targets; filtering is covered by the UI.
        return None

    def _refresh_status_after_change(self):
        return None

    def _refresh_category_counts(self):
        return None

    def _mark_priority_dirty(self, _message=""):
        self._dirty_priority = True

    def statusBar(self):
        class _Status:
            @staticmethod
            def showMessage(*_args, **_kwargs):
                return None
        return _Status()


class _AssignmentFakeMain(_SignalMixin, _TableDataMixin):
    def __init__(self, mods, worklist):
        self.all_mods = list(mods)
        self.all_mods_by_pkg = self._build_mod_index(self.all_mods)
        self.priority_svc = PriorityService(self.all_mods)
        self.current_worklist = list(worklist)
        self.current_profile = object()
        self.table_all = ModTable()
        self.table_active = ModTable()
        self.table = self.table_all
        self._mod_tables = {"all": self.table_all, "active": self.table_active}
        self._current_filter_cat = None
        self._search_keyword = ""
        self._current_mod_tab = "all"
        self._profile_fill_pending = False
        self._dirty_priority = False
        self._updating_category_checks = False
        self._cat_items = {}
        self._cat_item_uncategorized = None
        self._messages = []

    def _rebuild_category_tree(self):
        return None

    def _refresh_category_counts(self):
        return None

    def _refresh_status_after_change(self):
        return None

    def statusBar(self):
        owner = self

        class _Status:
            @staticmethod
            def showMessage(message, *_args, **_kwargs):
                owner._messages.append(message)

        return _Status()


def test_category_enable_renders_both_tables_from_memory():
    mods = [_mod("A", "folder"), _mod("B", "folder"), _mod("C", "")]
    wl = [
        {"package_name": "A", "enabled": False, "order": -1, "priority_index": None},
        {"package_name": "B", "enabled": True, "order": 0, "priority_index": 0},
        {"package_name": "C", "enabled": False, "order": -1, "priority_index": None},
    ]
    ui = _FakeMain(mods, wl)
    with patch("services.category_service.all_folders", return_value=["folder"]):
        ui._render_current_worklist()
        ui._enable_category("folder")
    assert all(e["enabled"] for e in ui.current_worklist if e["package_name"] in {"A", "B"})
    all_states = {
        ui.table_all.item(r, COL_PKG).text(): ui.table_all.item(r, COL_ENABLED).checkState()
        for r in range(ui.table_all.rowCount())
    }
    assert all_states["A"] == Qt.Checked and all_states["B"] == Qt.Checked
    active_states = {
        ui.table_active.item(r, COL_PKG).text(): ui.table_active.item(r, COL_ENABLED).checkState()
        for r in range(ui.table_active.rowCount())
        if not ui.table_active.is_folder_row(r)
    }
    assert active_states["A"] == Qt.Checked and active_states["B"] == Qt.Checked


def test_category_toggle_from_empty_active_tab_keeps_complete_worklist():
    """A fully disabled folder must not make the active projection erase rows."""
    mods = [_mod("A", "folder"), _mod("B", "folder"), _mod("C", "")]
    wl = [
        {"package_name": "A", "enabled": False, "order": -1, "priority_index": None},
        {"package_name": "B", "enabled": False, "order": -1, "priority_index": None},
        {"package_name": "C", "enabled": True, "order": 0, "priority_index": 0},
    ]
    ui = _FakeMain(mods, wl)
    with patch("services.category_service.all_folders", return_value=["folder"]):
        ui._render_current_worklist()
        # Reproduce the user's view: the active projection has no folder rows
        # because every member is disabled, while the all-mods table is full.
        ui.table = ui.table_active
        ui._enable_category("folder")
    assert [e["package_name"] for e in ui.current_worklist] == ["C", "A", "B"]
    assert all(
        e["enabled"] for e in ui.current_worklist if e["package_name"] in {"A", "B"}
    )


def test_lookup_accepts_workshop_profile_display_alias():
    m = _mod("1061306287")
    ui = _FakeMain([m], [])
    got = ui._lookup_mod("1061306287|Actual Day-/Nighttime Mod")
    assert got is m


def test_priority_category_block_preserves_order():
    svc = PriorityService([])
    wl = svc.build_worklist(["A", "X", "B", "Y"], ["A", "X", "B", "Y"])
    moved = svc.move_bottom_by_package_set(wl, {"A", "B"})
    assert [e["package_name"] for e in moved if e["enabled"]] == ["X", "Y", "A", "B"]


def test_unresolved_workshop_assignment_is_visible_in_target_folder():
    package = "1061306287|Workshop title"
    # Keep one scanned Mod present so the normal render path is active while
    # the profile row itself remains unresolved.
    ui = _AssignmentFakeMain([_mod("known")], [{
        "package_name": package,
        "enabled": True,
        "order": 0,
        "priority_index": 0,
        "mod": None,
    }])
    ui._render_current_worklist()
    cache = {}

    def _set_bulk(mapping):
        cache.update(mapping)

    def _get_category(key):
        return cache.get(key, "")

    with patch("services.category_service.all_folders", return_value=["Maps"]), \
            patch("services.category_service.set_categories_bulk", _set_bulk), \
            patch("services.category_service.save", lambda *a, **k: None), \
            patch("services.category_service.get_category", _get_category):
        ui._assign_packages_to_category([package], "Maps")
        assert cache == {"1061306287": "Maps"}
        assert not ui.table_all.isRowHidden(0)
        ui._current_filter_cat = ""
        ui._apply_filter_to_table()
        assert ui.table_all.isRowHidden(0)


def test_mod_table_drag_reorders_visible_enabled_rows():
    table = ModTable()
    for i, package in enumerate(("A", "B", "C")):
        table.add_mod_row({
            "package_name": package,
            "enabled": True,
            "order": i,
            "priority_index": i,
        }, None)
    table.resize(800, 300)
    table.show()
    QApplication.processEvents()
    table.selectRow(2)
    target = table.visualRect(table.model().index(0, 1)).center()

    class _DropEvent:
        def position(self):
            return QPointF(target)

        def accept(self):
            self.accepted = True

        def ignore(self):
            self.accepted = False

    event = _DropEvent()
    table.dropEvent(event)
    assert event.accepted
    assert [table.package_at(i) for i in range(3)] == ["C", "A", "B"]
    table.close()


def test_mod_table_drag_preserves_duplicate_package_rows():
    table = ModTable()
    for i, name in enumerate(("same", "same", "other")):
        table.add_mod_row({
            "package_name": name,
            "enabled": True,
            "order": i,
            "priority_index": i,
        }, None)
    table.resize(800, 300)
    table.show()
    QApplication.processEvents()
    table.selectRow(1)
    target = table.visualRect(table.model().index(0, 1)).center()

    class _DropEvent:
        def position(self):
            return QPointF(target)

        def accept(self):
            self.accepted = True

        def ignore(self):
            self.accepted = False

    event = _DropEvent()
    table.dropEvent(event)
    assert event.accepted
    assert table.rowCount() == 3
    assert sorted(table.package_at(i) for i in range(table.rowCount())) == ["other", "same", "same"]
    table.close()


def test_stale_deleted_folder_records_count_as_uncategorized():
    from unittest.mock import patch
    import services.category_service as category_service

    with patch.object(category_service, "_load", return_value={
        "A": {"category": "deleted-folder"},
        "B": {"category": "test"},
        "C": {"category": ""},
    }), patch.object(category_service, "_load_folders", return_value=["test"]):
        stats = category_service.stats()
        assert stats == {"": 2, "test": 1}
        assert category_service.mods_in_category("") == {"A", "C"}


def test_category_count_preserves_duplicate_alias_rows():
    # The table is row-based.  Two profile aliases can temporarily resolve
    # to the same Mod object, so statistics must count both visible rows even
    # though category enable/disable operations still dedupe by Mod identity.
    mod = _mod("same", "test")
    ui = _FakeMain([mod], [
        {"package_name": "same", "enabled": True, "order": 0},
        {"package_name": "same", "enabled": False, "order": -1},
    ])
    with patch("services.category_service.all_folders", return_value=["test"]), \
            patch("services.category_service.mods_in_category", return_value={"same"}):
        assert ui._category_worklist_count("test") == 2


def test_active_table_renders_folder_group_and_expands_selection():
    mods = [_mod("A", "Maps"), _mod("B", "Maps"), _mod("C", "")]
    wl = [
        {"package_name": "A", "enabled": True, "order": 0, "priority_index": 0},
        {"package_name": "B", "enabled": True, "order": 1, "priority_index": 1},
        {"package_name": "C", "enabled": True, "order": 2, "priority_index": 2},
    ]
    ui = _FakeMain(mods, wl)
    with patch("services.category_service.all_folders", return_value=["Maps"]):
        ui._render_current_worklist()
        active = ui.table_active
        assert active.rowCount() == 4
        assert active.is_folder_row(0)
        assert active.row_folder(0) == "Maps"
        assert active.is_folder_child_row(1)
        assert active.is_folder_child_row(2)
        ui.table = active
        active.selectRow(0)
        assert ui._selected_worklist_indices() == [0, 1]


def test_mod_table_drag_preserves_hidden_rows():
    table = ModTable()
    for i, name in enumerate(("A", "B", "C")):
        table.add_mod_row({
            "package_name": name,
            "enabled": i < 2,
            "order": i if i < 2 else -1,
            "priority_index": i if i < 2 else None,
        }, None)
    table.setRowHidden(2, True)
    table.resize(800, 300)
    table.show()
    QApplication.processEvents()
    table.selectRow(1)
    target = table.visualRect(table.model().index(0, 1)).center()

    class _DropEvent:
        def position(self):
            return QPointF(target)

        def accept(self):
            self.accepted = True

        def ignore(self):
            self.accepted = False

    event = _DropEvent()
    table.dropEvent(event)
    assert event.accepted
    assert table.rowCount() == 3
    assert table.isRowHidden(2)
    table.close()


def main() -> int:
    app = QApplication.instance() or QApplication([])
    tests = [
        test_category_enable_renders_both_tables_from_memory,
        test_lookup_accepts_workshop_profile_display_alias,
        test_priority_category_block_preserves_order,
        test_unresolved_workshop_assignment_is_visible_in_target_folder,
        test_mod_table_drag_reorders_visible_enabled_rows,
        test_mod_table_drag_preserves_duplicate_package_rows,
        test_stale_deleted_folder_records_count_as_uncategorized,
        test_category_count_preserves_duplicate_alias_rows,
        test_active_table_renders_folder_group_and_expands_selection,
        test_mod_table_drag_preserves_hidden_rows,
    ]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
