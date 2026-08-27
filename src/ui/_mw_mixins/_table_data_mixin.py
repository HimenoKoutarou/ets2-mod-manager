"""Auto-split MainWindow Mixin（方法级拆分；不持有独立状态，仅把 MainWindow 方法按功能归档）。

Mixin 类本身不做 __init__ / 不 super()，所有 self.xxx 属性都来自 MainWindow 实例自身（已在 MainWindow.__init__ 中初始化）。
唯一注意：closeEvent 位于 _SignalMixin 中，其末尾会直接调用 `QMainWindow.closeEvent(self, event)` 跳过 MRO。
"""
from __future__ import annotations
from services.priority_service import PriorityService
from services.profile_service import ProfileService, ProfileInfo

from services.i18n_service import _, tr, I18nNotifier, set_language, current_language, available_languages, language_display_name
from .._mw_widgets import _LangSwitchDialog, SplashScreen, ModTable, COL_ENABLED, COL_NAME, COL_SOURCE, COL_SIZE, COL_VERSION, COL_ORDER, COL_PKG


import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from PySide6.QtCore import Qt, QSize, QMimeData, QByteArray, Signal, QTimer, QObject, QThread
from PySide6.QtGui import QAction, QIcon, QPixmap, QImage, QBrush, QColor, QFont, QDrag
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QFrame,
    QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem, QToolBar,
    QLabel, QPlainTextEdit, QPushButton, QStatusBar, QFileDialog, QMessageBox,
    QHeaderView, QAbstractItemView, QProgressBar, QCheckBox, QComboBox, QGroupBox,
    QSizePolicy, QTreeWidget, QTreeWidgetItem, QDialogButtonBox, QDialog, QTextBrowser, QTextEdit,
    QMenu, QScrollArea, QGridLayout, QLineEdit, QSpinBox, QTabWidget, QToolButton,
    QInputDialog, QWidgetAction, QToolButton,
)

from services.i18n_service import _, tr
from core.models import Mod


class _TableDataMixin:
    def _build_mod_index(self, mods):
        idx: Dict[str, Mod] = {}
        import re as _re_idx
        for m in mods:
            # 主：manifest.package_name 左段
            pkg_name = getattr(getattr(m, "manifest", None), "package_name", None) or ""
            if pkg_name:
                left = pkg_name.split("|",1)[0].strip()
                if left:
                    idx.setdefault(left, m)
                idx.setdefault(pkg_name.strip(), m)
            # 主（同级）：mod_id（文件名/目录名，快速扫描阶段 = manifest.package_name 的兜底）
            if m.mod_id:
                idx.setdefault(m.mod_id, m)
            # 再 workshop_id 剥后缀纯数字
            stripped = _re_idx.sub(r"_(workshop|copy\d*|local)$", "", m.mod_id) if m.mod_id else ""
            if stripped and stripped != m.mod_id and stripped.isdigit():
                idx.setdefault(stripped, m)
        return idx

    def _lookup_mod(self, pkg: str) -> Optional["Mod"]:
        """按 pkg（可能含|，可能带_workshop后缀）从 all_mods_by_pkg 查 Mod 对象，4 层 fallback"""
        if not pkg or not self.all_mods_by_pkg:
            return None
        import re as _re_lu
        if pkg in self.all_mods_by_pkg:
            return self.all_mods_by_pkg[pkg]
        left = pkg.split("|", 1)[0].strip()
        if left and left in self.all_mods_by_pkg:
            return self.all_mods_by_pkg[left]
        s_left = _re_lu.sub(r"_(workshop|copy\d*|local)$", "", left) if left else ""
        if s_left and s_left != left and s_left in self.all_mods_by_pkg:
            return self.all_mods_by_pkg[s_left]
        s_pkg = _re_lu.sub(r"_(workshop|copy\d*|local)$", "", pkg)
        if s_pkg and s_pkg != pkg and s_pkg in self.all_mods_by_pkg:
            return self.all_mods_by_pkg[s_pkg]
        if (left.isdigit() or s_left.isdigit() or s_pkg.isdigit() or pkg.isdigit()):
            target_num = left if left.isdigit() else (s_left if s_left.isdigit() else (s_pkg if s_pkg.isdigit() else pkg))
            for m_ in (self.all_mods_by_pkg or {}).values():
                ms = _re_lu.sub(r"_(workshop|copy\d*|local)$", "", m_.mod_id) if m_.mod_id else ""
                mp = getattr(getattr(m_, "manifest", None), "package_name", "") or ""
                mp_left = mp.split("|", 1)[0].strip()
                if ms == target_num or mp_left == target_num:
                    return m_
        return None

    # ---------- UI 构建 ----------
    def _fill_table_for_profile(self, prof: ProfileInfo):
        for t in (self.table_all, self.table_active):
            t.setUpdatesEnabled(False)
        try:
            self._fill_table_impl(prof)
        finally:
            for t in (self.table_all, self.table_active):
                t.setUpdatesEnabled(True)

    def _fill_table_impl(self, prof: ProfileInfo):
        # 快速扫描未完成时（all_mods 为空 / priority_svc 未就绪），跳过填表格
        # 设置 _profile_fill_pending 标志，数据就绪后自动填充
        if not self.all_mods or not self.all_mods_by_pkg or self.priority_svc is None:
            self._profile_fill_pending = True
            return
        self._profile_fill_pending = False
        # profile 成功加载后建立启用+优先级 hash 基线，后续改动据此判定 dirty
        try:
            if hasattr(self, "current_worklist") and hasattr(self, "_clear_priority_dirty"):
                self._clear_priority_dirty()
        except Exception:
            pass
        try:
            active = self.profile_svc.get_active_mods(prof)
        except Exception as e:
            QMessageBox.warning(self, _("dlg.read_fail_title"), _("dlg.read_active_fail", e=f"{e!r}"))
            active = []
        self.current_worklist = self.priority_svc.build_worklist(
            active, list(self.all_mods_by_pkg.keys())
        )
        # 两张表都用相同数据构建（active 表通过 _apply_filter_to_table 自动只显示 enabled）
        for t in (self.table_all, self.table_active):
            t.blockSignals(True)
            t.setRowCount(0)
            for entry in self.current_worklist:
                m = self._lookup_mod(entry["package_name"]) or (entry.get("mod") if isinstance(entry.get("mod"), Mod) else None)
                # ===== 新增：mod 丢失时的 2 种分支 =====
                missing = (m is None)
                if missing:
                    if not bool(entry.get("enabled")):
                        # 未启用 + 找不到 → 直接去掉
                        continue
                    # 已启用 + 找不到 → 打标记给 add_mod_row 改颜色
                    entry = dict(entry)  # 复制一份避免污染原 worklist
                    entry["_missing_mod"] = True
                t.add_mod_row(entry, m)
            self._reorder_table_for(t)
            t.blockSignals(False)
        self._apply_filter_to_table()
        self._refresh_status_after_change()

    def _reorder_table_for(self, tbl):
        """对指定 tbl 按 current_worklist 重排序并 renumber"""
        tbl.setUpdatesEnabled(False)
        try:
            ordered = [x for x in self.current_worklist if x.get("enabled")] +                       [x for x in self.current_worklist if not x.get("enabled")]
            pkg_order = [x["package_name"] for x in ordered]
            # 快速路径：比较当前行顺序是否已经一致
            rows_cur = []
            for r in range(tbl.rowCount()):
                it = tbl.item(r, COL_PKG)
                rows_cur.append(it.text() if it else "")
            if rows_cur == pkg_order:
                tbl._renumber_order()
                return
            enabled_rows = []; disabled_rows = []
            for r in range(tbl.rowCount()):
                if tbl._row_enabled(r):
                    enabled_rows.append(self._take_row_from(tbl, r))
                else:
                    disabled_rows.append(self._take_row_from(tbl, r))
            tbl.setRowCount(0)
            rows_by_pkg: Dict[str, list] = {}
            for cell in enabled_rows + disabled_rows:
                pkg_item = cell[COL_PKG]
                key = pkg_item.text() if pkg_item is not None else ("__none__" + str(id(cell)))
                rows_by_pkg.setdefault(key, []).append(cell)
            for pn in pkg_order:
                cells = rows_by_pkg.get(pn)
                if not cells: continue
                tbl.insertRow(tbl.rowCount())
                for col, item in enumerate(cells[0]):
                    if item is None: continue
                    tbl.setItem(tbl.rowCount() - 1, col, item)
                rows_by_pkg[pn] = cells[1:]
            tbl._renumber_order()
        finally:
            tbl.setUpdatesEnabled(True)

    def _take_row_from(self, tbl, r):
        return [tbl.takeItem(r, c) for c in range(tbl.columnCount())]

    def _reorder_table_according_to_worklist(self):
        """根据 current_worklist，把 enabled 行在前，disabled 在后（两张表同时刷新）。"""
        for t in (self.table_all, self.table_active):
            self._reorder_table_for(t)

    # ---------- 用户操作：批量、上下移、保存 ----------
    def _sync_worklist_from_table(self):
        """把当前活动表格的勾选+顺序回写到 self.current_worklist，同时同步勾选状态到另一张表。

        Bug A 修复：
         - 对 other_tbl 的 set_row_enabled 调用外包 blockSignals(True/False)，
           防止 Qt.Checked/Unchanged 切换触发 itemChanged → 重入 _on_check_changed
           → 两张表互相 ping 直到死锁。
         - 顶部加重入 guard（极快点击/缺失 mod 行删除重填期间尤其重要）。
        """
        # 重入 guard：如果正在本函数内就退出，避免递归循环修改 worklist + 表格
        guard = getattr(self, "_in_sync_worklist", False)
        if guard:
            return
        self._in_sync_worklist = True
        try:
            # Step 0: 同步勾选状态（如果两张表都有对应 pkg 的行，两边勾选保持一致）
            try:
                src_tbl = self.table
                other_tbl = self.table_active if src_tbl is self.table_all else self.table_all
                sync_map: Dict[str, bool] = {}
                # 读源表勾选也加 blockSignals，防止某些 Qt 版本读 checkState 时触发奇怪信号
                src_tbl.blockSignals(True)
                try:
                    for r in range(src_tbl.rowCount()):
                        pi = src_tbl.item(r, COL_PKG)
                        if pi is None: continue
                        sync_map[pi.text()] = src_tbl._row_enabled(r)
                finally:
                    src_tbl.blockSignals(False)
                # 写对端表：必须 blockSignals，否则 setCheckState 触发 itemChanged
                other_tbl.blockSignals(True)
                try:
                    for r in range(other_tbl.rowCount()):
                        pi = other_tbl.item(r, COL_PKG)
                        if pi is None: continue
                        val = sync_map.get(pi.text())
                        if val is None: continue
                        if val != other_tbl._row_enabled(r):
                            other_tbl.set_row_enabled(r, val)
                finally:
                    other_tbl.blockSignals(False)
            except Exception:
                pass
            enabled_pkgs = []
            disabled_pkgs = []
            pkg_enabled = {}
            for r in range(self.table.rowCount()):
                pkg = self.table.package_at(r)
                en = self.table._row_enabled(r)
                pkg_enabled[pkg] = en
                if en: enabled_pkgs.append(pkg)
                else: disabled_pkgs.append(pkg)
            # 保留 worklist 其他信息，只重排
            by_pkg = {x["package_name"]: x for x in self.current_worklist}
            new_wl = []
            for i, pn in enumerate(enabled_pkgs):
                w = dict(by_pkg.get(pn, {"package_name": pn}))
                w["enabled"] = True; w["order"] = i
                new_wl.append(w)
            for pn in disabled_pkgs:
                w = dict(by_pkg.get(pn, {"package_name": pn}))
                w["enabled"] = False; w["order"] = -1
                new_wl.append(w)
            self.current_worklist = new_wl
        finally:
            self._in_sync_worklist = False

    def _refresh_status_after_change(self):
        en = sum(1 for x in self.current_worklist if x.get("enabled"))
        tot = len(self.current_worklist)
        prof_id = self.current_profile.profile_id[:14] if self.current_profile else _("detail.none")
        # 搜索 / Tab 过滤后的可见数量
        vis = 0
        try:
            if getattr(self, "table", None) is not None:
                for r in range(self.table.rowCount()):
                    if not self.table.isRowHidden(r):
                        vis += 1
        except Exception:
            pass
        kw = getattr(self, "_search_keyword", "") or ""
        tab = getattr(self, "_current_mod_tab", "all")
        suffix = ""
        if tab == "active":
            suffix += "  [" + _("ui.tab_active_mods") + "]"
        if kw:
            suffix += "  " + _("ui.sb_search_shown", shown=vis, total=tot)
        self.statusBar().showMessage(
            _("ui.sb_enabled_count", en=en, tot=tot, prof=prof_id) + suffix)

    def _batch(self, action: str):
        rows = self.table.selected_rows()
        if not rows:
            QMessageBox.information(self, _("dlg.hint_title"), _("dlg.hint_select_rows"))
            return
        self._sync_worklist_from_table()
        wl2 = PriorityService.batch_toggle(self.current_worklist, rows, action)
        self.current_worklist = wl2
        self._schedule_refresh(order=True, filter=True, counts=True, status=True)

    def _move(self, kind: str):
        rows = self.table.selected_rows()
        if not rows: return
        self._sync_worklist_from_table()
        if kind == "up": self.current_worklist = self.priority_svc.move_up(self.current_worklist, rows)
        elif kind == "down": self.current_worklist = self.priority_svc.move_down(self.current_worklist, rows)
        elif kind == "top": self.current_worklist = self.priority_svc.move_top(self.current_worklist, rows)
        elif kind == "bottom": self.current_worklist = self.priority_svc.move_bottom(self.current_worklist, rows)
        self._schedule_refresh(order=True, filter=True, counts=True, status=True)

    def _move_up(self): self._move("up")
    def _move_down(self): self._move("down")
    def _move_top(self): self._move("top")
    def _move_bottom(self): self._move("bottom")

    def _move_up_n(self, n: int): self._move_delta(-n)
    def _move_down_n(self, n: int): self._move_delta(n)

    def _move_delta(self, delta: int):
        """在启用列表中，将选中的行往前(-)/后(+)移动 delta 个优先级（单位是 enabled-list 的 index，而非表格行）。"""
        tbl = getattr(self, "table", None)
        if tbl is None: return
        rows = tbl.selected_rows()
        if not rows or not self.priority_svc or not self.current_profile:
            return
        self._sync_worklist_from_table()
        # move_up 支持 steps 参数，move_down 同理
        if delta < 0:
            self.current_worklist = self.priority_svc.move_up(self.current_worklist, rows, steps=abs(delta))
        else:
            self.current_worklist = self.priority_svc.move_down(self.current_worklist, rows, steps=delta)
        # 先记下 pkg 以便恢复选中
        pkgs = []
        for r in rows:
            it = tbl.item(r, COL_PKG)
            if it: pkgs.append(it.text())
        self._schedule_refresh(order=True, filter=True, counts=True, status=True)
        # 恢复选中（按 COL_PKG 查找）—— 延迟到下一轮事件循环以便表格刷新完成
        def _restore_sel(pkgs=pkgs):
            for pkg in pkgs:
                rr = self.table.find_row_by_pkg(pkg)
                if rr is not None:
                    self.table.selectRow(rr)
        QTimer.singleShot(50, _restore_sel)
        self.statusBar().showMessage(
            _("ui.sb_priority_delta", n=delta, moved=len(rows)), 3000
        )

    # ---------- 分类整体操作 Helper ----------
    def _cat_key_to_pkg_set(self, cat_key: str) -> set:
        """返回指定分类下所有 mod 的 package_name 集合。"""
        pkgs = set()
        try:
            for pkg, mod in (self.all_mods_by_pkg or {}).items():
                tag = getattr(mod, "category_tag", "") or ""
                if tag == cat_key:
                    pkgs.add(pkg)
        except Exception:
            pass
        return pkgs

    def _cat_display_name(self, cat_key: str) -> str:
        if not cat_key:
            try:
                return _("cat.uncategorized")
            except Exception:
                return "Uncategorized"
        return cat_key

    def _save_worklist_after_cat_op(self, cat_key: str, count: int):
        """分类操作完成后统一收尾：重填表格、刷新状态、刷新计数。"""
        if self.current_profile:
            self._fill_table_for_profile(self.current_profile)
        else:
            self._apply_filter_to_table()
        try:
            self._refresh_category_counts()
        except Exception:
            pass
        try:
            self._refresh_status_after_change()
        except Exception:
            pass
        name = self._cat_display_name(cat_key)
        self.statusBar().showMessage(_("ui.sb_cat_done", name=name, count=count), 5000)

    def _enable_category(self, cat_key: str):
        """启用指定分类下所有 mod。"""
        if not self.current_profile:
            QMessageBox.information(self, _("dlg.hint_title"), _("ui.sb_cat_no_profile"))
            return
        pkg_set = self._cat_key_to_pkg_set(cat_key)
        if not pkg_set:
            self.statusBar().showMessage(_("ui.sb_cat_empty", name=self._cat_display_name(cat_key)), 4000)
            return
        self._sync_worklist_from_table()
        count = 0
        for e in self.current_worklist:
            if e.get("package_name") in pkg_set:
                if not e.get("enabled"):
                    count += 1
                e["enabled"] = True
        self._save_worklist_after_cat_op(cat_key, count)

    def _disable_category(self, cat_key: str):
        """禁用指定分类下所有 mod。"""
        if not self.current_profile:
            QMessageBox.information(self, _("dlg.hint_title"), _("ui.sb_cat_no_profile"))
            return
        pkg_set = self._cat_key_to_pkg_set(cat_key)
        if not pkg_set:
            self.statusBar().showMessage(_("ui.sb_cat_empty", name=self._cat_display_name(cat_key)), 4000)
            return
        self._sync_worklist_from_table()
        count = 0
        for e in self.current_worklist:
            if e.get("package_name") in pkg_set:
                if e.get("enabled"):
                    count += 1
                e["enabled"] = False
        self._save_worklist_after_cat_op(cat_key, count)

    def _toggle_category(self, cat_key: str):
        """反选指定分类下所有 mod 的启用状态。"""
        if not self.current_profile:
            QMessageBox.information(self, _("dlg.hint_title"), _("ui.sb_cat_no_profile"))
            return
        pkg_set = self._cat_key_to_pkg_set(cat_key)
        if not pkg_set:
            self.statusBar().showMessage(_("ui.sb_cat_empty", name=self._cat_display_name(cat_key)), 4000)
            return
        self._sync_worklist_from_table()
        count = 0
        for e in self.current_worklist:
            if e.get("package_name") in pkg_set:
                e["enabled"] = not e.get("enabled", False)
                count += 1
        self._save_worklist_after_cat_op(cat_key, count)

    def _move_cat_up(self, cat_key: str, steps: int = 1):
        """按 package_set 整体上移分类（保持块内相对顺序）。"""
        if not self.current_profile or not self.priority_svc:
            QMessageBox.information(self, _("dlg.hint_title"), _("ui.sb_cat_no_profile"))
            return
        pkg_set = self._cat_key_to_pkg_set(cat_key)
        if not pkg_set:
            self.statusBar().showMessage(_("ui.sb_cat_empty", name=self._cat_display_name(cat_key)), 4000)
            return
        self._sync_worklist_from_table()
        self.current_worklist = self.priority_svc.move_up_by_package_set(
            self.current_worklist, pkg_set, steps=steps
        )
        try: self._mark_priority_dirty("已按分类整体上移 · 请点工具栏「保存」写回 profile")
        except Exception: pass
        count = len([p for p in pkg_set if any(
            e.get("package_name") == p and e.get("enabled") for e in self.current_worklist
        )])
        self._save_worklist_after_cat_op(cat_key, count)

    def _move_cat_down(self, cat_key: str, steps: int = 1):
        """按 package_set 整体下移分类（保持块内相对顺序）。"""
        if not self.current_profile or not self.priority_svc:
            QMessageBox.information(self, _("dlg.hint_title"), _("ui.sb_cat_no_profile"))
            return
        pkg_set = self._cat_key_to_pkg_set(cat_key)
        if not pkg_set:
            self.statusBar().showMessage(_("ui.sb_cat_empty", name=self._cat_display_name(cat_key)), 4000)
            return
        self._sync_worklist_from_table()
        self.current_worklist = self.priority_svc.move_down_by_package_set(
            self.current_worklist, pkg_set, steps=steps
        )
        try: self._mark_priority_dirty("已按分类整体下移 · 请点工具栏「保存」写回 profile")
        except Exception: pass
        count = len([p for p in pkg_set if any(
            e.get("package_name") == p and e.get("enabled") for e in self.current_worklist
        )])
        self._save_worklist_after_cat_op(cat_key, count)

    def _cat_top(self, cat_key: str):
        """把分类整体置顶（保持块内相对顺序）。"""
        if not self.current_profile or not self.priority_svc:
            QMessageBox.information(self, _("dlg.hint_title"), _("ui.sb_cat_no_profile"))
            return
        pkg_set = self._cat_key_to_pkg_set(cat_key)
        if not pkg_set:
            self.statusBar().showMessage(_("ui.sb_cat_empty", name=self._cat_display_name(cat_key)), 4000)
            return
        self._sync_worklist_from_table()
        self.current_worklist = self.priority_svc.move_top_by_package_set(
            self.current_worklist, pkg_set
        )
        try: self._mark_priority_dirty("已按分类整体置顶 · 请点工具栏「保存」写回 profile")
        except Exception: pass
        count = len([p for p in pkg_set if any(
            e.get("package_name") == p and e.get("enabled") for e in self.current_worklist
        )])
        self._save_worklist_after_cat_op(cat_key, count)

    def _cat_bottom(self, cat_key: str):
        """把分类整体置底（保持块内相对顺序）。"""
        if not self.current_profile or not self.priority_svc:
            QMessageBox.information(self, _("dlg.hint_title"), _("ui.sb_cat_no_profile"))
            return
        pkg_set = self._cat_key_to_pkg_set(cat_key)
        if not pkg_set:
            self.statusBar().showMessage(_("ui.sb_cat_empty", name=self._cat_display_name(cat_key)), 4000)
            return
        self._sync_worklist_from_table()
        self.current_worklist = self.priority_svc.move_bottom_by_package_set(
            self.current_worklist, pkg_set
        )
        try: self._mark_priority_dirty("已按分类整体置底 · 请点工具栏「保存」写回 profile")
        except Exception: pass
        count = len([p for p in pkg_set if any(
            e.get("package_name") == p and e.get("enabled") for e in self.current_worklist
        )])
        self._save_worklist_after_cat_op(cat_key, count)

    def _iter_user_categories_for_menu(self) -> list:
        """返回用户自定义分类列表（仅用户创建的文件夹，不包含未分类）。"""
        try:
            from services.category_service import all_folders
            return list(all_folders())
        except Exception:
            return []

    def _refresh_category_action_enabled(self):
        """刷新所有分类相关菜单的 enabled 状态：无 profile 或无用户分类时置灰。"""
        has_profile = self.current_profile is not None
        has_user_cats = len(self._iter_user_categories_for_menu()) > 0
        enabled = has_profile and has_user_cats
        try:
            for attr in dir(self):
                if attr.startswith("_cat_menu_action_"):
                    act = getattr(self, attr, None)
                    if act is not None:
                        try:
                            act.setEnabled(enabled)
                        except Exception:
                            pass
        except Exception:
            pass

    def _rebuild_mods_menu_categories(self):
        """打开「模组操作 ▼」时动态重填按分类批量启用/禁用/反选子菜单。"""
        from PySide6.QtGui import QAction
        cats = self._iter_user_categories_for_menu()
        has_profile = self.current_profile is not None
        for menu, method, i18n_prefix in [
            (getattr(self, "_cat_sm_en", None), self._enable_category, "en"),
            (getattr(self, "_cat_sm_dis", None), self._disable_category, "dis"),
            (getattr(self, "_cat_sm_tog", None), self._toggle_category, "tog"),
        ]:
            if menu is None: continue
            menu.clear()
            if not cats or not has_profile:
                act = menu.addAction(_("dlg.nm_no_folders"))
                act.setEnabled(False)
                continue
            for ck in cats:
                safe = "".join(c if c.isalnum() or c == "_" else "_" for c in ck)
                label = _("ui.cat_prefix", label=ck)
                a = QAction(label, menu); a.setProperty("i18n_key", f"_dyn_cat_{i18n_prefix}_{safe}")
                a.triggered.connect(lambda _c=False, key=ck, m=method: m(key))
                menu.addAction(a)
                setattr(self, f"_cat_menu_action_{i18n_prefix}_{safe}", a)

    def _rebuild_prio_menu_categories(self):
        """打开「优先级 ▼」时动态重填按分类整体排序子菜单。"""
        from PySide6.QtGui import QAction
        cats = self._iter_user_categories_for_menu()
        has_profile = self.current_profile is not None and self.priority_svc is not None
        # 1) 按分类整体上移 → 步长子菜单
        sm_up = getattr(self, "_cat_sm_up", None)
        if sm_up is not None:
            sm_up.clear()
            if not cats or not has_profile:
                act = sm_up.addAction(_("dlg.nm_no_folders")); act.setEnabled(False)
            else:
                for ck in cats:
                    label = _("ui.cat_prefix", label=ck)
                    sm_cat = sm_up.addMenu(label)
                    for steps_txt, steps in [(_("ui.tb_up"), 1), (_("ui.tb_up10"), 10), (_("ui.tb_up50"), 50), (_("ui.tb_up100"), 100)]:
                        a = sm_cat.addAction(steps_txt)
                        a.triggered.connect(lambda _c=False, key=ck, s=steps: self._move_cat_up(key, steps=s))
        # 2) 按分类整体下移 → 步长子菜单
        sm_down = getattr(self, "_cat_sm_down", None)
        if sm_down is not None:
            sm_down.clear()
            if not cats or not has_profile:
                act = sm_down.addAction(_("dlg.nm_no_folders")); act.setEnabled(False)
            else:
                for ck in cats:
                    label = _("ui.cat_prefix", label=ck)
                    sm_cat = sm_down.addMenu(label)
                    for steps_txt, steps in [(_("ui.tb_down"), 1), (_("ui.tb_down10"), 10), (_("ui.tb_down50"), 50), (_("ui.tb_down100"), 100)]:
                        a = sm_cat.addAction(steps_txt)
                        a.triggered.connect(lambda _c=False, key=ck, s=steps: self._move_cat_down(key, steps=s))
        # 3) 按分类置顶
        sm_top = getattr(self, "_cat_sm_top", None)
        if sm_top is not None:
            sm_top.clear()
            if not cats or not has_profile:
                act = sm_top.addAction(_("dlg.nm_no_folders")); act.setEnabled(False)
            else:
                for ck in cats:
                    label = _("ui.cat_prefix", label=ck)
                    a = sm_top.addAction(label)
                    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in ck)
                    setattr(self, f"_cat_menu_action_top_{safe}", a)
                    a.triggered.connect(lambda _c=False, key=ck: self._cat_top(key))
        # 4) 按分类置底
        sm_bot = getattr(self, "_cat_sm_bottom", None)
        if sm_bot is not None:
            sm_bot.clear()
            if not cats or not has_profile:
                act = sm_bot.addAction(_("dlg.nm_no_folders")); act.setEnabled(False)
            else:
                for ck in cats:
                    label = _("ui.cat_prefix", label=ck)
                    a = sm_bot.addAction(label)
                    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in ck)
                    setattr(self, f"_cat_menu_action_bot_{safe}", a)
                    a.triggered.connect(lambda _c=False, key=ck: self._cat_bottom(key))

    # ---------- Tab 切换（全部模组 / 已启用模组） ----------
    def _load_behavior_prefs(self) -> dict:
        """从 config/behavior.json 加载用户行为偏好，不存在则返回默认 {backup_before_profile_save: 'prompt'}"""
        default = {"backup_before_profile_save": "prompt"}
        try:
            cfg_dir = Path("config")
            cfg_file = cfg_dir / "behavior.json"
            if not cfg_file.exists():
                return default
            with cfg_file.open("r", encoding="utf-8-sig") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                pref = str(raw.get("backup_before_profile_save", "prompt"))
                if pref in ("always", "prompt", "never"):
                    return {"backup_before_profile_save": pref}
            return default
        except Exception:
            return default

    def _save_behavior_prefs(self, prefs: dict):
        """保存偏好到 config/behavior.json"""
        try:
            cfg_dir = Path("config")
            cfg_dir.mkdir(parents=True, exist_ok=True)
            cfg_file = cfg_dir / "behavior.json"
            with cfg_file.open("w", encoding="utf-8") as f:
                json.dump(prefs, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _ensure_backup_before_save(self) -> bool:
        """
        存档写入前的备份检查/询问入口。
        返回 True = 可以继续执行写入（不管用户选择备份还是跳过，只要不是Cancel）
        """
        prefs = self._load_behavior_prefs()
        pref = prefs.get("backup_before_profile_save", "prompt")

        if pref == "always":
            if self.current_profile and getattr(self.current_profile, "profile_sii", None):
                self.backup_svc.backup(self.current_profile.profile_sii, tag="auto-pre-save")
            return True

        if pref == "never":
            return True

        # pref == "prompt"
        dlg = QMessageBox(self)
        dlg.setWindowTitle(_("dlg.backup_prompt_title"))
        dlg.setText(_("dlg.backup_prompt_msg"))
        dlg.setIcon(QMessageBox.Warning)

        cb_always = QCheckBox(_("dlg.backup_prompt_always"))
        dlg.setCheckBox(cb_always)

        btn_backup = dlg.addButton(_("dlg.backup_btn_backup_then_write"), QMessageBox.AcceptRole)
        btn_skip = dlg.addButton(_("dlg.backup_btn_skip_just_write"), QMessageBox.DestructiveRole)
        btn_cancel = dlg.addButton(QMessageBox.Cancel)

        dlg.exec()
        clicked = dlg.clickedButton()

        if clicked == btn_cancel:
            return False

        if clicked == btn_backup:
            if cb_always.isChecked():
                self._save_behavior_prefs({"backup_before_profile_save": "always"})
            if self.current_profile and getattr(self.current_profile, "profile_sii", None):
                path = self.backup_svc.backup(self.current_profile.profile_sii, tag="user-pre-save")
                if path is not None:
                    self.statusBar().showMessage(_("dlg.backup_ok_then_write_msg"), 3000)
            return True

        if clicked == btn_skip:
            return True

        return False

    # ---------- 保存 profile ----------
    def _save_profile(self):
        if not self.current_profile:
            QMessageBox.warning(self, _("dlg.no_profile_title"), _("dlg.no_profile_save"))
            return
        self._sync_worklist_from_table()
        if not self._ensure_backup_before_save():
            return
        new_active = PriorityService.worklist_to_active(self.current_worklist)
        dirty_note = "（* 有未保存的优先级/启用变更）" if getattr(self, "_dirty_priority", False) else ""
        ret = QMessageBox.question(
            self, _("dlg.save_confirm_title"),
            _("dlg.save_confirm_msg", prof=self.current_profile.profile_id[:16], n=len(new_active)) + dirty_note)
        if ret != QMessageBox.Yes: return
        try:
            wrote = self.profile_svc.set_active_mods(self.current_profile, new_active)
            try: self._clear_priority_dirty()
            except Exception: pass
            QMessageBox.information(self, _("dlg.save_ok_title"), _("dlg.save_ok_msg", wrote=wrote))
            self.statusBar().showMessage(_("ui.sb_saved", n=len(new_active)))
        except Exception as e:
            QMessageBox.critical(self, _("dlg.save_fail_title"), _("dlg.save_fail_msg", e=f"{e!r}"))

    def _do_backup_now(self):
        if not self.current_profile:
            QMessageBox.warning(self, _("dlg.no_profile_title"), _("dlg.no_profile_save"))
            return
        path = self.backup_svc.backup(self.current_profile.profile_sii, tag="ui-snapshot")
        if path is None:
            QMessageBox.information(self, _("dlg.backup_skip_title"), _("dlg.backup_skip_msg"))
        else:
            QMessageBox.information(self, _("dlg.backup_ok_title"), _("dlg.backup_ok_msg", path=path))

    # ---------- 分类文件夹筛选 / 右键归类 ----------
    def _create_folder(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, _("dlg.new_folder_title"), _("dlg.new_folder_label"))
        if not ok: return
        name = name.strip()
        if not name:
            QMessageBox.warning(self, _("dlg.new_folder_title"), _("dlg.folder_name_empty"))
            return
        from services import category_service as _cs
        if not _cs.create_folder(name):
            QMessageBox.warning(self, _("dlg.new_folder_title"), _("dlg.folder_exists"))
            return
        self._rebuild_category_tree()
        self.statusBar().showMessage(_("ui.sb_folder_created", name=name), 5000)

    def _rename_folder(self, old_name: str):
        from PySide6.QtWidgets import QInputDialog
        new_name, ok = QInputDialog.getText(
            self, _("dlg.rename_folder_title"), _("dlg.rename_folder_label"), text=old_name)
        if not ok: return
        new_name = new_name.strip()
        if not new_name or new_name == old_name: return
        from services import category_service as _cs
        n = _cs.rename_folder(old_name, new_name)
        if n < 0:
            QMessageBox.warning(self, _("dlg.rename_folder_title"), _("dlg.folder_exists"))
            return
        self._rebuild_category_tree()
        self._apply_filter_to_table()
        self.statusBar().showMessage(
            _("ui.sb_folder_renamed", old=old_name, new=new_name, n=max(n, 0)), 5000)

    def _delete_folder(self, name: str):
        from services import category_service as _cs
        st = _cs.stats()
        count = st.get(name, 0)
        ret = QMessageBox.question(
            self, _("dlg.delete_folder_title"),
            _("dlg.delete_folder_msg", name=name, n=count))
        if ret != QMessageBox.Yes: return
        n = _cs.delete_folder(name)
        self._rebuild_category_tree()
        self._apply_filter_to_table()
        self.statusBar().showMessage(
            _("ui.sb_folder_deleted", name=name, n=max(n, 0)), 5000)

    def _rebuild_category_tree(self):
        from services.category_service import all_folders
        cur = self.tree_categories.currentItem()
        cur_role = cur.data(0, Qt.UserRole) if cur else None
        for it in list(self._cat_items.values()):
            idx = self.tree_categories.indexOfTopLevelItem(it)
            if idx >= 0:
                self.tree_categories.takeTopLevelItem(idx)
        self._cat_items.clear()
        for fname in all_folders():
            it = QTreeWidgetItem([_("ui.cat_prefix", label=fname)])
            it.setData(0, Qt.UserRole, ("__filter_cat__", fname))
            self._cat_items[fname] = it
            self.tree_categories.addTopLevelItem(it)
        if cur_role:
            key = cur_role[1] if cur_role[0] == "__filter_cat__" else None
            if key is None:
                self.tree_categories.setCurrentItem(self._cat_item_all)
            elif key == "":
                self.tree_categories.setCurrentItem(self._cat_item_uncategorized)
            elif key in self._cat_items:
                self.tree_categories.setCurrentItem(self._cat_items[key])
            else:
                self.tree_categories.setCurrentItem(self._cat_item_all)
        self._refresh_category_counts()

    def _refresh_category_counts(self):
        """把 services.category_service.stats() 的计数写到每个树节点后。"""
        try:
            from services.category_service import stats
            st = stats()
            total = sum(st.values())
            self._cat_item_all.setText(0, _("ui.cat_all") + f"  ({total})")
            self._cat_item_uncategorized.setText(0, _("ui.cat_uncategorized") + f"  ({st.get('', 0)})")
            for ck, it in self._cat_items.items():
                it.setText(0, _("ui.cat_prefix", label=ck) + f"  ({st.get(ck, 0)})")
        except Exception:
            pass

    def _apply_filter_to_table(self):
        cat = getattr(self, "_current_filter_cat", None)
        kw = getattr(self, "_search_keyword", "") or ""
        kw_low = kw.lower() if kw else ""
        tables_to_filter = []
        # 对两个表格分别应用过滤（切换 Tab 时用户能看到一致结果）
        for key in ("all", "active"):
            t = self._mod_tables.get(key) if hasattr(self, "_mod_tables") else None
            if t is None: continue
            if not t.rowCount(): continue
            tables_to_filter.append((key, t))
        for tab_key, tbl in tables_to_filter:
            for row in range(tbl.rowCount()):
                hide = False
                pkg_item = tbl.item(row, COL_PKG)
                pkg = pkg_item.text() if pkg_item is not None else None
                name_item = tbl.item(row, COL_NAME)
                src_item = tbl.item(row, COL_SOURCE)
                name_text = name_item.text() if name_item is not None else ""
                src_text = src_item.text() if src_item is not None else ""
                mod = self._lookup_mod(pkg) if pkg else None
                # (A) Tab 过滤：active 表只显示 enabled 的条目（根据 tbl.check 状态判断以兼容实时勾选）
                if tab_key == "active":
                    if not tbl._row_enabled(row):
                        tbl.setRowHidden(row, True)
                        continue
                # (B) 搜索关键词过滤：名称 / 包名 / 作者 / 版本 / 分类标签 / id
                if kw_low:
                    hay = [name_text.lower(), pkg.lower() if pkg else "", src_text.lower()]
                    if mod is not None:
                        mf = mod.manifest
                        hay.extend([
                            (mf.display_name or "").lower(),
                            (mf.package_name or "").lower(),
                            (mf.author or "").lower(),
                            (mf.package_version or "").lower(),
                            (mod.mod_id or "").lower(),
                            (getattr(mod, "category_tag", "") or "").lower(),
                        ])
                    joined = chr(10).join(hay)
                    # 多关键词 AND 搜索（空格分隔）
                    tokens = [t for t in kw_low.split() if t]
                    for tok in tokens:
                        if tok not in joined:
                            hide = True; break
                # (C) 分类文件夹过滤
                if not hide and mod is not None:
                    try:
                        mcat = getattr(mod, "category_tag", "") or ""
                    except Exception:
                        mcat = ""
                    if cat == "":
                        hide = bool(mcat)  # 未分类：有分类的隐藏
                    elif cat is not None:
                        hide = (mcat != cat)
                tbl.setRowHidden(row, hide)
        self._refresh_category_counts()

    def _assign_checked_rows_to_category(self, cat_key: str):
        n = 0
        for row in range(self.table.rowCount()):
            if self.table.isRowHidden(row): continue
            if not self.table._row_enabled(row):  # 用"启用勾选"作为批量归类选择？——用户说的"勾选"其实是表格的启用复选框（唯一勾选）
                pass
            # 要求：表格里"勾选的条目"——本表只有启用复选框是勾选，就用它
            # 但启用复选框不是"选择条目"的语义，改：用"选中的行（高亮）"作为归类目标，更符合资源管理器直觉
        # 重新统计：选高亮行（selected_rows）
        rows = self.table.selected_rows()
        if not rows:
            QMessageBox.information(self, _("dlg.assign_hint_title"), _("dlg.assign_hint_msg"))
            return
        for r in rows:
            if self.table.isRowHidden(r): continue
            pkg_item = self.table.item(r, COL_PKG)
            pkg = pkg_item.text() if pkg_item is not None else None
            mod = self._lookup_mod(pkg) if pkg else None
            if mod is None: continue
            try:
                mod.category_tag = cat_key or ""
                n += 1
            except Exception:
                pass
        try:
            from services import category_service as _cs
            _cs.save()
        except Exception:
            pass
        self._apply_filter_to_table()
        cat_label = _("cat.uncategorized") if cat_key == "" else cat_key
        self.statusBar().showMessage(
            _("ui.sb_assigned", n=n, cat=cat_label), 5000
        )
        self._refresh_category_counts()

    # ---------- 加载顺序弹窗 ----------
    def _show_load_order_dialog(self):
        prof = self.current_profile
        if not prof:
            QMessageBox.information(self, _("dlg.lo_title"), _("dlg.lo_no_profile"))
            return
        active: list[str] = []
        try:
            active = list(self.profile_svc.get_active_mods(prof))
        except Exception:
            try:
                active = list(getattr(prof, "active_mods", []) or [])
            except Exception:
                active = []
        if not active:
            QMessageBox.information(
                self, _("dlg.lo_title"),
                _("dlg.lo_empty", prof=str(prof))
            )
            return
        lines = [
            _("dlg.lo_html_profile", prof=str(prof)),
            _("dlg.lo_html_count", n=len(active)),
            "<ol style='margin:8px 0 8px 28px; line-height:1.75'>",
        ]
        for i, pn in enumerate(active, start=1):
            mod = self._lookup_mod(pn)
            if mod is None:
                lines.append(_("dlg.lo_missing", pn=f"{pn!r}"))
                continue
            title = mod.display_title
            ver = mod.display_version
            lines.append(f"<li>{title}  <span style='color:#666;font-size:12px'>({ver})</span></li>")
        lines.append("</ol>")
        html = "<style>body{font-family:'Microsoft YaHei','PingFang SC',sans-serif;}</style>" + "".join(lines)
        dlg = QDialog(self)
        dlg.setWindowTitle(_("dlg.lo_dlg_title", prof=str(prof)))
        dlg.resize(580, 620)
        lv = QVBoxLayout(dlg)
        tb = QTextBrowser(dlg); tb.setHtml(html)
        lv.addWidget(tb)
        bb = QDialogButtonBox(QDialogButtonBox.Ok)
        bb.accepted.connect(dlg.accept)
        lv.addWidget(bb)
        dlg.exec()

    # ---------- 新模组归类弹窗 ----------
    def _show_new_mods_dialog(self, new_ids: list[str]):
        if not new_ids: return
        try:
            from services.category_service import all_folders
            folders = all_folders()
        except Exception:
            cats = []
        rows: list[tuple[str, object, QComboBox]] = []
        for mid in new_ids:
            m = getattr(self, "_all_mods_by_id", {}).get(mid)
            cb = QComboBox()
            cb.addItem(_("dlg.nm_uncategorized"), "")
            for fname in folders:
                cb.addItem(_("dlg.nm_cat_item", label=fname), fname)
            # 根据 manifest.categories 自动预选
            if m is not None:
                mani = getattr(m, "manifest", None)
                if mani is not None:
                    mcats = getattr(mani, "categories", None) or []
                    if mcats:
                        for idx in range(cb.count()):
                            if cb.itemData(idx) in mcats:
                                cb.setCurrentIndex(idx); break
            cb.addItem(_("dlg.nm_new_folder"), "__new_folder__")
            rows.append((mid, m, cb))
        dlg = QDialog(
            self,
            Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint
        )
        dlg.setWindowTitle(_("dlg.nm_title", n=len(new_ids)))
        dlg.setModal(True)
        dlg.setMinimumWidth(660)
        dlg.setMinimumHeight(560)
        dlg.resize(680, 580)
        dlg.setSizeGripEnabled(True)
        lv = QVBoxLayout(dlg)
        lv.addWidget(QLabel(
            _("dlg.nm_info", n=len(new_ids))
        ))
        sa = QScrollArea(dlg); sa.setWidgetResizable(True)
        sw = QWidget(); gl = QGridLayout(sw); gl.setHorizontalSpacing(14); gl.setVerticalSpacing(5)
        h1 = QLabel(_("dlg.nm_h_name")); h1.setStyleSheet("padding:3px")
        h2 = QLabel(_("dlg.nm_h_cat")); h2.setStyleSheet("padding:3px")
        gl.addWidget(h1, 0, 0); gl.addWidget(h2, 0, 1)
        for i, (mid, m, cb) in enumerate(rows, start=1):
            title = getattr(m, "display_title", None) or mid
            lbl = QLabel(title)
            lbl.setWordWrap(True)
            lbl.setToolTip(_("ui.tooltip_id_path", id=str(mid), path=str(getattr(m, "package_path", ""))))
            lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            gl.addWidget(lbl, i, 0)
            gl.addWidget(cb, i, 1)
        sa.setWidget(sw)
        lv.addWidget(sa, 1)
        # 批量快捷设置
        hb = QHBoxLayout()
        hb.addWidget(QLabel(_("dlg.nm_batch_lbl")))
        qcb = QComboBox(); qcb.addItem(_("dlg.nm_uncategorized"), "")
        for fname in folders:
            qcb.addItem(_("dlg.nm_cat_item", label=fname), fname)
        qcb.addItem(_("dlg.nm_new_folder"), "__new_folder__")
        hb.addWidget(qcb, 1)
        qbtn = QPushButton(_("dlg.nm_batch_apply"))
        def _apply_all():
            v = qcb.currentData()
            for _mid, _m, _cb in rows:
                for ii in range(_cb.count()):
                    if _cb.itemData(ii) == v: _cb.setCurrentIndex(ii); break
        qbtn.clicked.connect(_apply_all)
        hb.addWidget(qbtn); hb.addStretch(1)
        lv.addLayout(hb)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dlg)
        ok_btn = bb.button(QDialogButtonBox.Ok)
        cancel_btn = bb.button(QDialogButtonBox.Cancel)
        ok_btn.setText(_("dlg.nm_ok"))
        ok_btn.setMinimumHeight(34)
        ok_btn.setMinimumWidth(120)
        cancel_btn.setText(_("dlg.nm_cancel"))
        cancel_btn.setMinimumHeight(34)
        cancel_btn.setMinimumWidth(120)
        ok_btn.setDefault(True)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lv.addWidget(bb)
        dlg.exec()   # OK/Cancel 都保留 combo 当前选择（Cancel 即使用默认：未分类）
        changes: dict[str, str] = {}
        for mid, m, cb in rows:
            ck = cb.currentData() or ""
            if ck == "__new_folder__":
                from PySide6.QtWidgets import QInputDialog
                fname, ok = QInputDialog.getText(self, _("dlg.new_folder_title"), _("dlg.new_folder_label"))
                if ok and fname.strip():
                    fname = fname.strip()
                    from services import category_service as _cs
                    _cs.create_folder(fname)
                    ck = fname
                else:
                    ck = ""
            changes[mid] = ck
            if m is not None:
                try: m._category_tag = ck
                except Exception: pass
        try:
            from services import category_service as _cs
            _cs.set_categories_bulk(changes)
            _cs.save()
        except Exception:
            pass
        self._apply_filter_to_table()

