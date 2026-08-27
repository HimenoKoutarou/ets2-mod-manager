"""Auto-split MainWindow Mixin（方法级拆分；不持有独立状态，仅把 MainWindow 方法按功能归档）。

Mixin 类本身不做 __init__ / 不 super()，所有 self.xxx 属性都来自 MainWindow 实例自身（已在 MainWindow.__init__ 中初始化）。
唯一注意：closeEvent 位于 _SignalMixin 中，其末尾会直接调用 `QMainWindow.closeEvent(self, event)` 跳过 MRO。
"""
from __future__ import annotations
from services.priority_service import PriorityService
from .._mw_widgets import _LangSwitchDialog, SplashScreen, ModTable, COL_ENABLED, COL_NAME, COL_SOURCE, COL_SIZE, COL_VERSION, COL_ORDER, COL_PKG
from version import __version__
from services.i18n_service import _, tr, I18nNotifier, set_language, current_language, available_languages, language_display_name

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


class _SignalMixin:
    def _schedule_refresh(self, *, order=False, filter=False, counts=False, status=False):
        self._need_refresh_order = self._need_refresh_order or order
        self._need_refresh_filter = self._need_refresh_filter or filter
        self._need_refresh_counts = self._need_refresh_counts or counts
        self._need_refresh_status = self._need_refresh_status or status
        if not self._refresh_debounce_timer.isActive():
            self._refresh_debounce_timer.start()

    def _do_deferred_refresh(self):
        for t in (self.table_all, self.table_active):
            t.setUpdatesEnabled(False)
        try:
            if self._need_refresh_order:
                self._reorder_table_according_to_worklist()
            if self._need_refresh_filter:
                try: self._apply_filter_to_table()
                except Exception: pass
            if self._need_refresh_counts:
                self._refresh_category_counts()
            if self._need_refresh_status:
                self._refresh_status_after_change()
        finally:
            for t in (self.table_all, self.table_active):
                t.setUpdatesEnabled(True)
        self._need_refresh_order = False
        self._need_refresh_filter = False
        self._need_refresh_counts = False
        self._need_refresh_status = False

    def _do_switch_language(self, lang: str):
        """切换语言：弹出模态遮罩 → 切换 → 分三批刷新 → 关闭遮罩。"""
        if lang not in ("zh_CN", "en_US", "ru_RU"):
            return
        # 使用官方API切换语言（内部已处理锁和缓存 + 持久化）
        changed = set_language(lang, emit=False)
        if not changed:
            return
        # 更新菜单勾选状态
        for l, act in getattr(self, "_lang_actions", {}).items():
            act.blockSignals(True)
            act.setChecked(l == lang)
            act.blockSignals(False)
        # 弹出模态遮罩（非阻塞事件循环的 show + processEvents 方式）
        dlg = _LangSwitchDialog(self)
        if self.isVisible():
            fg = self.frameGeometry()
            cp = fg.center()
            dlg.move(cp.x() - dlg.width() // 2, cp.y() - dlg.height() // 2)
        dlg.show()
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        # 分四批刷新，最后一批关闭遮罩
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._retranslate_phase1)
        QTimer.singleShot(30, self._retranslate_phase2)
        QTimer.singleShot(100, self._retranslate_phase3)
        QTimer.singleShot(260, dlg.close_it)

    def _do_language_refresh(self):
        """兼容保留：刷新工作已在 _do_switch_language 中分批调度。"""
        pass

    def _on_language_changed(self, lang: str):
        """保留兼容接口：由 i18n 系统外部触发的语言变化。"""
        if getattr(self, '_lang_switching', False):
            return
        self._lang_switching = True
        try:
            self._do_switch_language(lang)
        finally:
            self._lang_switching = False

    def _retranslate_phase1(self):
        """第1批刷新：窗口标题 + 工具栏（QAction/QToolButton/递归子QMenu）+ 语言菜单。"""
        try:
            # 1. 窗口标题
            self.setWindowTitle(f"{_('app.title')}  v{__version__}")
        except Exception:
            pass
        try:
            def _retranslate_menu(m):
                """递归翻译一个菜单的 title、它的 actions 以及子菜单。"""
                try:
                    if m is None:
                        return
                    key = m.property("i18n_key")
                    if key:
                        m.setTitle(_(key))
                    for act in m.actions():
                        if act is None:
                            continue
                        k = act.property("i18n_key")
                        if k:
                            act.setText(_(k))
                        sub = act.menu()
                        if sub is not None:
                            _retranslate_menu(sub)
                except Exception:
                    pass
            for tb in getattr(self, "_tb_toolbars", []):
                if tb is None:
                    continue
                # 顶层 actions + 各自关联的子菜单
                for act in tb.actions():
                    if act is None:
                        continue
                    k = act.property("i18n_key")
                    if k:
                        act.setText(_(k))
                    sub = act.menu()
                    if sub is not None:
                        _retranslate_menu(sub)
                # _tb_toolbuttons：4 个 QToolButton（模组操作 / 优先级 / 保存 / 工具）
                for btn in getattr(self, "_tb_toolbuttons", []):
                    try:
                        if btn is None:
                            continue
                        k = btn.property("i18n_key")
                        if k:
                            btn.setText(_(k))
                        bm = btn.menu()
                        if bm is not None:
                            _retranslate_menu(bm)
                    except Exception:
                        pass
                # 兜底：toolbar 上的所有 widget
                try:
                    for i in range(tb.count()):
                        w = tb.widgetForAction(tb.actions()[i]) if i < len(tb.actions()) else None
                        if w is None:
                            continue
                        if hasattr(w, "property") and hasattr(w, "setText"):
                            k = w.property("i18n_key")
                            if k:
                                try:
                                    w.setText(_(k))
                                except Exception:
                                    pass
                except Exception:
                    pass
        except Exception:
            pass
        try:
            # 3. 语言菜单：title + 语言项显示名
            for m in getattr(self, "_lang_menus", []):
                if m:
                    m.setTitle(_("menu.lang"))
            for lang, act in getattr(self, "_lang_actions", {}).items():
                act.setText(language_display_name(lang))
        except Exception:
            pass

    def _retranslate_phase2(self):
        """第2批刷新：左栏标签 + 搜索框 + Tab页签 + 详情面板 + 软链接面板。"""
        try:
            # 左栏标签
            if hasattr(self, 'lbl_profiles_title') and self.lbl_profiles_title is not None:
                self.lbl_profiles_title.setText(_("ui.lbl_profiles"))
        except Exception:
            pass
        try:
            if hasattr(self, 'gb_categories') and self.gb_categories is not None:
                self.gb_categories.setTitle(_("ui.gb_categories"))
        except Exception:
            pass
        try:
            # 搜索框 placeholder
            if hasattr(self, 'search_input') and self.search_input is not None:
                self.search_input.setPlaceholderText(_("ui.search_ph"))
        except Exception:
            pass
        try:
            # Tab页签
            if hasattr(self, 'tabs') and self.tabs is not None:
                tab_labels = [_("ui.tab_active"), _("ui.tab_all")]
                for i, txt in enumerate(tab_labels):
                    if i < self.tabs.count():
                        self.tabs.setTabText(i, txt)
        except Exception:
            pass
        try:
            # 详情面板
            if hasattr(self, 'gb_detail') and self.gb_detail is not None:
                self.gb_detail.setTitle(_("ui.gb_detail"))
            lbl_map = [
                ('lbl_det_title', _("ui.det_title")),
                ('lbl_det_author', _("ui.det_author")),
                ('lbl_det_version', _("ui.det_version")),
                ('lbl_det_size', _("ui.det_size")),
                ('lbl_det_source', _("ui.det_source")),
                ('lbl_det_game', _("ui.det_game")),
                ('lbl_det_desc', _("ui.det_desc")),
            ]
            for attr_name, txt in lbl_map:
                if hasattr(self, attr_name):
                    w = getattr(self, attr_name)
                    if w is not None and hasattr(w, 'setText'):
                        w.setText(txt)
        except Exception:
            pass
        try:
            # 软链接面板
            if hasattr(self, 'gb_symlink') and self.gb_symlink is not None:
                self.gb_symlink.setTitle(_("ui.gb_symlink"))
            if hasattr(self, 'lbl_sl_status') and self.lbl_sl_status is not None:
                self.lbl_sl_status.setText(_("ui.sl_status"))
            if hasattr(self, 'btn_sl_fix') and self.btn_sl_fix is not None:
                self.btn_sl_fix.setText(_("ui.btn_sl_fix"))
        except Exception:
            pass

    def _retranslate_phase3(self):
        """第3批刷新：表格列头 + 状态栏 + 其他杂项。"""
        try:
            # 状态栏
            self._refresh_status_after_change()
        except Exception:
            pass
        try:
            # 表格列头
            tables = [getattr(self, 'table', None), getattr(self, 'table_all', None), getattr(self, 'table_active', None)]
            labels = [
                _("tbl.col_check"), _("tbl.col_name"), _("tbl.col_source"),
                _("tbl.col_size"), _("tbl.col_version"), _("tbl.col_order"), "(pkg)"
            ]
            for t in tables:
                try:
                    if t is not None:
                        t.blockSignals(True)
                        t.setHorizontalHeaderLabels(labels)
                        t.blockSignals(False)
                except Exception:
                    pass
        except Exception:
            pass

    def _retranslate_all_ui(self):
        """语言切换后刷新UI文本（极简版：不操作表格，只更新非表格控件）"""
        # 1. 标题
        self.setWindowTitle(f"{_('app.title')}  v{__version__}")
        
        # 2. 工具栏按钮文本
        try:
            for tb in getattr(self, '_tb_toolbars', []):
                if tb:
                    for act in tb.actions():
                        key = act.property("i18n_key")
                        if key:
                            act.setText(_(key))
        except Exception:
            pass
        
        # 3. 语言菜单文本
        try:
            for m in getattr(self, '_lang_menus', []):
                if m:
                    m.setTitle(_("menu.lang"))
            for lang, act in getattr(self, "_lang_actions", {}).items():
                act.setText(language_display_name(lang))
        except Exception:
            pass
        
        # 4. 状态栏
        try:
            self._refresh_status_after_change()
        except Exception:
            pass
        
        # 5. 延迟500ms后再更新表格列头（使用单次定时器，避免阻塞）
        from PySide6.QtCore import QTimer
        if not hasattr(self, '_lang_header_timer') or self._lang_header_timer is None:
            self._lang_header_timer = QTimer(self)
            self._lang_header_timer.setSingleShot(True)
            self._lang_header_timer.timeout.connect(self._do_header_retranslate)
        self._lang_header_timer.start(500)
    
    def _do_header_retranslate(self):
        """单独更新表格列头（延迟执行，避免与语言切换事件冲突）。"""
        tables = [self.table, self.table_all, self.table_active]
        labels = [
            _("tbl.col_check"), _("tbl.col_name"), _("tbl.col_source"),
            _("tbl.col_size"), _("tbl.col_version"), _("tbl.col_order"), "(pkg)"
        ]
        for t in tables:
            try:
                if t is not None:
                    t.blockSignals(True)
                    t.setHorizontalHeaderLabels(labels)
                    t.blockSignals(False)
            except Exception:
                pass
    # ---------- 启动：扫描模组 + 填 Profiles 列表 + 软链接状态 ----------
    
    def _async_check_update(self):
        """异步检查更新（在后台线程中运行）。"""
        from PySide6.QtCore import QThread
        
        class UpdateCheckThread(QThread):
            def __init__(self, update_svc, parent=None):
                super().__init__(parent)
                self._update_svc = update_svc
            
            def run(self):
                try:
                    self._update_svc.check_for_update()
                except Exception:
                    pass
        
        self._update_thread = UpdateCheckThread(self.update_svc, self)
        self._update_thread.start()
    
    def _on_update_available(self, latest_version: str, download_url: str):
        """发现新版本时的处理。"""
        from PySide6.QtWidgets import QMessageBox
        
        reply = QMessageBox.question(
            self,
            _('update.title'),
            _('update.available', version=latest_version),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        if reply == QMessageBox.Yes:
            self.update_svc.download_and_install()
    
    def _on_no_update_needed(self):
        """已是最新版本。"""
        self.statusBar().showMessage(_('update.up_to_date'))
    
    def _on_update_error(self, error_msg: str):
        """更新错误处理。"""
        self.statusBar().showMessage(_('update.error', msg=error_msg))
    
    def _on_update_status_changed(self, status: str):
        """更新状态变化。"""
        self.statusBar().showMessage(status)
    
    def _on_update_progress(self, current: int, total: int, desc: str):
        """更新进度。"""
        if total > 0:
            self.statusBar().showMessage(f"{desc} ({current}/{total})")
        else:
            self.statusBar().showMessage(desc)
    
    def _on_download_finished(self, zip_path: str):
        """下载完成。"""
        self.statusBar().showMessage(_('update.download_done'))
    
    def _on_install_finished(self, install_dir: str):
        """安装完成。"""
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(
            self,
            _('update.title'),
            _('update.install_done', dir=install_dir)
        )

    def _show_update_notes_if_needed(self):
        """版本升级后首次启动时弹窗展示更新内容。通过 behavior.json 的 last_seen_version 判断。"""
        try:
            prefs = self._load_behavior_prefs()
            last_seen = prefs.get("last_seen_version", "")
            if last_seen == __version__:
                return
            # 版本不同（升级或降级），弹窗
            self._show_update_notes_dialog()
            # 记录已展示
            prefs["last_seen_version"] = __version__
            self._save_behavior_prefs(prefs)
        except Exception:
            pass

    def _show_update_notes_dialog(self):
        """弹窗展示当前版本的更新内容。

        展示原则（v1.2.1 修正）：
          - 只作为 application-modal（阻止操作主窗口），不再跨进程置顶；
            否则用户切到浏览器/资源管理器时会被一个不可关的绿色更新条一直挡在前面。
          - 必须保证三种方式都能关：标题栏 [X] / ESC / "知道了" 按钮；
            之前单按钮在 125%~150% DPI 下有时 layout 高度不足导致按钮被截，用户会误判为"点不掉"。
          - 内容与实际当前版本交付一致（R14 filesystem 收束 + cmd popup fix），不再复写旧版的
            Workshop/scan 文案。
        """
        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QLabel, QHBoxLayout, QScrollArea, QWidget,
            QDialogButtonBox, QSizePolicy,
        )
        from PySide6.QtCore import Qt

        # Flags via constructor → never lose native [X] close button.
        dlg = QDialog(
            self,
            Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint
        )
        dlg.setWindowTitle(f"🎉 v{__version__} 更新内容")
        dlg.setModal(True)
        # Minimum instead of fixed so high-DPI scaling never clips the "知道了" button.
        dlg.setMinimumWidth(600)
        dlg.setMinimumHeight(560)
        dlg.resize(600, 560)
        dlg.setSizeGripEnabled(True)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        # 标题
        title = QLabel(f"ETS2 Mod Manager  v{__version__}")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #2da44e;")
        layout.addWidget(title)

        subtitle = QLabel("本次更新（R14 收束阶段）交付内容：")
        subtitle.setStyleSheet("font-size: 13px; color: #555;")
        layout.addWidget(subtitle)

        # 更新内容（滚动区域）— 当前版本 bb364b5 / c29f330 真实交付
        notes = [
            ("🛡️", "文件系统事务不再误删 / 误判冲突（R14 核心关闭）",
             "一键迁移的 move_and_link() 对 Junction/Symlink 真正做成了两阶段事务："
             "先在 target 建等价 link → 删 original link → 失败立刻回滚 target link 并搬回之前已经移动的普通文件；"
             "保证 old 仍在 ↔ new 不在的二择一不变量。即使回滚自己再次失败，错误消息会明确写'仍可能存在，请手动检查'，不再伪称已回滚。"),
            ("🚫", "启动扫描不再狂弹 CMD 黑窗口",
             "扫描加密 mod 时每个 extractor.exe / sxc64.exe / SII_Decrypt.exe / cmd /c dir 子进程统一加 "
             "CREATE_NO_WINDOW + STARTF_USESHOWWINDOW。50+ 加密 mod 的用户从'每次启动几百次黑窗闪动'变'一次都不闪'。"),
            ("🧪", "回归测试 121 条，全部可复现",
             "覆盖 move-and-link 单失败 / 双失败回滚（rollback 自己也 fail 的最坏情况）、Junction 目录 false-positive、"
             "Update partial-install 隔离、backup collision UUID12、命名统一策略等。"),
            ("🗂️", "备份/隔离/让位文件名策略统一 UUID12 后缀",
             "Update backup dir、rollback quarantine 空壳目录、move_and_link 让位 backup 三处都改成"
             " timestamp + uuid4().hex[:12]，同一秒重复操作也不会 false failure。"),
            ("🌍", "测试彻底脱离 F 盘硬编码依赖",
             "test_r14.py 13 处 Path(r'F:\\ETS2ModManager\\src') 全部替换成模块级 "
             "_PROJECT_ROOT = Path(__file__).resolve().parents[1]，任意 clone 路径、GitHub Actions、"
             "另一台电脑都能直接跑出 121 PASS。"),
            ("🧼", "dead-code / broad-exception 收尾清理",
             "删除 quarantine_path = quarantine_path 无意义自赋值行；"
             "rollback 重建 Junction 的 except Exception 分支绑定异常名并拼 type+msg 到返回字符串；"
             "pre-check 之后 original→Junction 的目录 conflict scan 正确跳过（不会把 def/vehicle/material 判成'冲突'）。"),
            ("📦", "自助更新 Release 包（v1.2.0+）",
             "打包流程标准化：PyInstaller onedir → 根目录平级 ETS2ModManager.exe（解包即运行）→ "
             "Compress-Archive Optimal → GitHub Release API 上传。更新后自动弹更新内容说明。"),
            ("♻️", "Update package root 判定保持现状，未扩改",
             "v1.2.0 审查指出的 validate 和 install 各自 resolve wrapper dir 属 architecture P2，"
             "本轮不做结构变动，以免在 R14 收尾引入新的回归。"),
        ]

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        content = QWidget()
        content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 8, 0, 8)
        content_layout.setSpacing(14)

        for icon, title_text, desc_text in notes:
            item_wrap = QVBoxLayout()
            item_wrap.setSpacing(4)
            head = QLabel(f"{icon}  {title_text}")
            head.setStyleSheet("font-size: 14px; font-weight: bold; color: #1f2328;")
            item_wrap.addWidget(head)
            body = QLabel(desc_text)
            body.setWordWrap(True)
            body.setStyleSheet("font-size: 12px; color: #656d76; padding-left: 28px;")
            item_wrap.addWidget(body)
            content_layout.addLayout(item_wrap)

        content_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        # 底部按钮（QDialogButtonBox — 保证 ESC / X / 点按钮三种方式全关）
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok)
        ok_btn = btn_box.button(QDialogButtonBox.Ok)
        ok_btn.setText("知道了")
        ok_btn.setMinimumHeight(36)
        ok_btn.setMinimumWidth(140)
        ok_btn.setStyleSheet("""
            QPushButton {
                background-color: #2da44e;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 24px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #2c974b; }
        """)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.accept)  # ESC still closes
        layout.addWidget(btn_box)

        dlg.exec()

    def _finalize_bootstrap(self):
        """统一收尾：停止UI刷新计时器、关闭Splash、恢复主窗口。"""
        self._stop_ui_refresh_timer()
        self._close_splash()
        self.setEnabled(True)

    def _bootstrap(self):
        self._show_splash()
        self.setEnabled(False)
        self._start_ui_refresh_timer()
        self._update_link_status()
        # 异步检查更新（不阻塞UI）
        QTimer.singleShot(1000, self._async_check_update)
        # 版本更新后首次启动：弹窗告知新版本特性
        QTimer.singleShot(1500, self._show_update_notes_if_needed)
        try:
            restored = self._scan_all_mods()
            if not restored:
                self._splash.set_first_scan(True)
        except Exception:
            self._finalize_bootstrap()
            raise
        finally:
            self._load_profiles()
        # 如果是缓存恢复（同步完成），此处直接收尾
        if restored:
            self._finalize_bootstrap()

    def _on_ui_refresh_timer(self):
        """定时器回调：在扫描期间定期刷新UI，避免在扫描循环中直接调用processEvents导致重入崩溃。"""
        QApplication.processEvents()

    def _start_ui_refresh_timer(self):
        """在耗时操作前启动UI刷新定时器。"""
        if not self._ui_refresh_timer_active:
            self._ui_refresh_timer_active = True
            self._ui_refresh_timer.start()

    def _stop_ui_refresh_timer(self):
        """耗时操作结束后停止UI刷新定时器。"""
        if self._ui_refresh_timer_active:
            self._ui_refresh_timer_active = False
            self._ui_refresh_timer.stop()

    def _show_splash(self):
        if self._splash is not None:
            return
        self._splash = SplashScreen(self._logo_path)
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = geo.center().x() - self._splash.width() // 2
            y = geo.center().y() - self._splash.height() // 2
            self._splash.move(x, y)
        self._splash.show_normal()
        QApplication.processEvents()

    def _close_splash(self):
        if self._splash is not None:
            try:
                self._splash.close()
            except Exception:
                pass
            try:
                self._splash.deleteLater()
            except Exception:
                pass
            self._splash = None

    def _update_link_status(self):
        st = self.symlink.get_status()
        kind = st.get("kind") or "unknown"
        link = st.get("link") or str(self.symlink.original)
        target = st.get("target")
        msg_map = {
            "normal": _("sym.normal", link=link),
            "real_dir": _("sym.normal", link=link),
            "junction": _("sym.junction", link=link, target=target or ""),
            "symlink": _("sym.symlink", link=link, target=target or ""),
            "symlink_broken": _("sym.broken", link=link),
            "not_found": _("sym.not_found"),
            "missing": _("sym.not_found"),
        }
        msg = msg_map.get(kind, _("sym.status_unknown", kind=kind, st=st))
        self.lbl_link_status.setText(msg)
        need_repair = (kind == "symlink_broken")
        self.btn_repair.setVisible(need_repair)
        if need_repair:
            self.lbl_link_status.setStyleSheet("background:#fff3cd; color:#7a4d00; padding:6px; border:1px solid #ffc107; border-radius:4px;")
        else:
            self.lbl_link_status.setStyleSheet("")

    # ---------- 扫描：顶部主进度条控制 + 取消处理 ----------
    def _on_quick_scan_result(self, mods_list: list, new_ids: list):
        """快速扫描完成：填 all_mods → 刷新表格 → 立即保存会话 → 新模组弹窗 → 启动异步解析 + Steam 查询"""
        self.all_mods = list(mods_list)
        self.all_mods_by_pkg = self._build_mod_index(self.all_mods)
        self._all_mods_by_id = {m.mod_id: m for m in self.all_mods}
        self.priority_svc = PriorityService(self.all_mods)
        total_size = sum(m.file_size for m in self.all_mods) / 1024 / 1024
        self.statusBar().showMessage(_("ui.sb_scanned_quick", n=len(self.all_mods), size=f"{total_size:.1f}"))
        try:
            self._refresh_category_counts()
        except Exception:
            pass
        self._profile_fill_pending = False
        if self.current_profile:
            self._fill_table_for_profile(self.current_profile)
        # 立即保存会话 + 快速扫描快照（下次启动直接恢复不用扫）
        try:
            from services.session_service import save_session_state, _dir_signature
            snap = [
                {
                    "mod_id": m.mod_id,
                    "package_path": m.package_path,
                    "package_type": m.package_type or "",
                    "file_size": int(m.file_size or 0),
                    "last_modified": float(m.last_modified or 0.0),
                    "mods_info_timestamp": int(getattr(m, "mods_info_timestamp", 0) or 0),
                    "display_name": getattr(m.manifest, "display_name", "") or "",
                    "package_name": getattr(m.manifest, "package_name", "") or "",
                    "author": getattr(m.manifest, "author", "") or "",
                    "package_version": getattr(m.manifest, "package_version", "") or "",
                    "compatible_versions": list(getattr(m.manifest, "compatible_versions", []) or []),
                    "categories": list(getattr(m.manifest, "categories", []) or []),
                    "icon_filename": getattr(m.manifest, "icon_filename", "") or "",
                    "description_filename": getattr(m.manifest, "description_filename", "") or "",
                    "description": getattr(m, "description", "") or "",
                    "category_tag": getattr(m, "category_tag", "") or "",
                }
                for m in self.all_mods
            ]
            sigs = {
                "mod_dir": _dir_signature(self.paths.mod_dir),
                "workshop_dir": _dir_signature(self.paths.workshop_content_dir),
                "mods_info_path": _dir_signature(self.paths.mods_info_path),
            }
            save_session_state(
                [m.mod_id for m in self.all_mods],
                {},
                mods_snapshot=snap,
                dir_signatures=sigs,
            )
        except Exception:
            pass
        # 新模组弹窗
        if new_ids:
            QTimer.singleShot(250, lambda ids=list(new_ids): self._show_new_mods_dialog(ids))
        self._quick_scan_worker = None
        # 第二阶段：解析加密包 + Steam 标题查询（并行）
        self._start_async_parse()
        QTimer.singleShot(500, self._fetch_workshop_titles_async)
        need_parse = any(
            not m.manifest.display_name or not m.description
            for m in self.all_mods
        )
        if not need_parse:
            self._finalize_bootstrap()

    def _on_quick_scan_failed(self, err_msg: str):
        self._finalize_bootstrap()
        self._hide_scan_progress()
        self.all_mods = []
        self.all_mods_by_pkg = {}
        QMessageBox.critical(self, _("dlg.scan_fail_title"), err_msg)
        self._quick_scan_worker = None
        self.statusBar().showMessage(_("ui.sb_scan_fail"), 5000)

    def _on_async_parse_progress(self, i: int, total: int, mod_id: str) -> None:
        if self._async_parse_progress is not None:
            self._async_parse_progress.setValue(i)
        self.statusBar().showMessage(f"解析加密包 {i}/{total} - {mod_id}")
        # 同步顶部主进度条
        self._show_scan_progress(_("ui.sp_phase_parse"), busy=False, cur=i, total=total, fmt="%v / %m", detail=mod_id)

    def _on_mod_parsed(self, mod_id: str) -> None:
        """一个加密包解析完成：刷新其对应的表格行"""
        m = self._all_mods_by_id.get(mod_id) if getattr(self, "_all_mods_by_id", None) else None
        if m is None:
            return
        # 如果 package_name 从兜底值更新为真实 unit_name，把新 key 加到索引
        new_pkg = getattr(m.manifest, "package_name", "") or ""
        if new_pkg and new_pkg != mod_id and hasattr(self, 'all_mods_by_pkg') and self.all_mods_by_pkg:
            self.all_mods_by_pkg.setdefault(new_pkg, m)
            left = new_pkg.split("|", 1)[0].strip()
            if left and left != new_pkg:
                self.all_mods_by_pkg.setdefault(left, m)
        # 刷新两张表
        for tbl in (self.table_all, self.table_active):
            row = tbl.find_row_by_pkg(mod_id)
            if row is not None:
                tbl.update_row_for_mod(row, m)
        # 详情面板刷新：当前选中行（任何一张表）的 pkg 如果和这个 mod 匹配就刷新
        try:
            import re as _re_fr_om
            cur_tbl = getattr(self, "table", None)
            if cur_tbl is not None:
                rs = cur_tbl.selected_rows()
                if rs:
                    r = rs[-1]
                    # 解析 pkg 是否匹配 mod_id / stripped
                    pkg = cur_tbl.package_at(r) if r < cur_tbl.rowCount() else None
                    if pkg:
                        s_pkg = _re_fr_om.sub(r"_(workshop|copy\d*|local)$", "", pkg)
                        s_mid = _re_fr_om.sub(r"_(workshop|copy\d*|local)$", "", mod_id)
                        if pkg == mod_id or pkg == s_mid or s_pkg == mod_id or s_pkg == s_mid:
                            self._show_mod_detail(pkg, m)
        except Exception:
            pass
        # 刷新分类计数（可能拿到了新的 display_title）
        try: self._refresh_category_counts()
        except Exception: pass

    def _on_async_parse_finished(self) -> None:
        """全部加密包解析完成"""
        self._finalize_bootstrap()
        # 移除顶部主进度条
        self._hide_scan_progress()
        # 移除状态栏小进度条
        if self._async_parse_progress is not None:
            try: self.statusBar().removeWidget(self._async_parse_progress)
            except Exception: pass
            self._async_parse_progress.deleteLater()
            self._async_parse_progress = None
        total_size = sum(m.file_size for m in self.all_mods) / 1024 / 1024
        self.statusBar().showMessage(_("ui.sb_scanned", n=len(self.all_mods), size=f"{total_size:.1f}"))
        self._async_parse_worker = None

    def closeEvent(self, event):
        # 退出时保存当前会话状态
        try:
            from services.session_service import save_session_state
            all_ids = [m.mod_id for m in self.all_mods]
            profiles_state = {}
            for p in getattr(self, "profiles", []):
                pid = getattr(p, "profile_id", str(id(p)))
                try:
                    active = self.profile_svc.get_active_mods(p)
                except Exception:
                    active = []
                profiles_state[pid] = {
                    "name": str(p),
                    "active_mods": list(active or []),
                }
            save_session_state(all_ids, profiles_state)
        except Exception:
            pass
        # 断开 update_svc 信号，避免单例生命周期长于窗口时信号触发已析构对象
        try:
            if getattr(self, "update_svc", None) is not None:
                try: self.update_svc.update_available.disconnect(self._on_update_available)
                except (RuntimeError, TypeError, AttributeError): pass
                try: self.update_svc.no_update_needed.disconnect(self._on_no_update_needed)
                except (RuntimeError, TypeError, AttributeError): pass
                try: self.update_svc.error_occurred.disconnect(self._on_update_error)
                except (RuntimeError, TypeError, AttributeError): pass
                try: self.update_svc.status_changed.disconnect(self._on_update_status_changed)
                except (RuntimeError, TypeError, AttributeError): pass
                try: self.update_svc.progress.disconnect(self._on_update_progress)
                except (RuntimeError, TypeError, AttributeError): pass
            # 收尾扫描/解析 worker
            for attr in ("_quick_scan_worker", "_async_parse_worker"):
                w = getattr(self, attr, None)
                if w is not None and hasattr(w, "stop"):
                    try: w.stop()
                    except Exception: pass
        except Exception as _e:
            import sys as _sys
            print(f"[main_window] closeEvent error: {_e}", file=_sys.stderr)
        QMainWindow.closeEvent(self, event)

    def _on_tree_profile_selected(self):
        items = self.tree_profiles.selectedItems()
        if not items: return
        prof = items[0].data(0, Qt.UserRole)
        if prof is None: return
        self.current_profile = prof
        # 优先用 prof.mod_count；若为 0 再实时查一次（兼容解密延迟场景）
        n_active = getattr(prof, "mod_count", 0) or 0
        if n_active == 0:
            try:
                n_active = len(self.profile_svc.get_active_mods(prof) or [])
            except Exception:
                n_active = 0
        self.statusBar().showMessage(
            _("ui.sb_current_profile", prof=str(prof), n=n_active), 5000
        )
        self._fill_table_for_profile(prof)
        # 切换存档时：对比该存档的 active_mods 与上次会话
        try:
            from services.session_service import get_new_active_in_profile
            pid = getattr(prof, "profile_id", str(id(prof)))
            active = self.profile_svc.get_active_mods(prof)
            new_in_profile = get_new_active_in_profile(pid, list(active or []))
            if new_in_profile:
                QTimer.singleShot(300, lambda ids=list(new_in_profile): self._show_new_mods_dialog(ids))
        except Exception:
            pass
        # 切换存档后刷新分类相关菜单 enabled 状态
        try:
            self._refresh_category_action_enabled()
        except Exception:
            pass

    def _on_tree_profile_menu(self, pos):
        it = self.tree_profiles.itemAt(pos)
        if not it: return
        prof = it.data(0, Qt.UserRole)
        if prof is None: return
        menu = QMenu(self)
        a_lo = menu.addAction(_("menu.load_order"))
        a_bk = menu.addAction(_("menu.backup_profile"))
        a_se = menu.addAction(_("menu.save_editor"))
        menu.addSeparator()
        a_cp = menu.addAction(_("menu.copy_profile"))
        a_del = menu.addAction(_("menu.delete_profile"))
        a_del.setForeground(QBrush(QColor("#ef4444")))
        act = menu.exec(self.tree_profiles.mapToGlobal(pos))
        if act == a_lo:
            # 先把当前存档切到此 profile（以便加载顺序对话框使用）
            self.current_profile = prof
            self._show_load_order_dialog()
        elif act == a_se:
            self._open_save_editor(prof)
        elif act == a_bk:
            try:
                self.backup_svc.backup(getattr(prof, "profile_sii", None), tag="ui-menu-snapshot")
                QMessageBox.information(self, _("dlg.backup_ok_title"), _("dlg.backup_ok2", prof=str(prof)))
            except Exception as e:
                QMessageBox.warning(self, _("dlg.backup_fail_title"), str(e))
        elif act == a_cp:
            prof_name = prof.company_name or prof.display_name or prof.profile_id
            default_name = f"{prof_name} 的副本"
            new_name, ok = QInputDialog.getText(self, _("dlg.copy_profile_title"),
                                                _("dlg.copy_profile_name_prompt"), text=default_name)
            if not ok or not new_name.strip():
                return
            new_name = new_name.strip()
            default_company = new_name
            new_company, ok2 = QInputDialog.getText(self, _("dlg.copy_profile_title"),
                                                     _("dlg.copy_profile_company_prompt"), text=default_company)
            if not ok2:
                return
            new_company = new_company.strip()
            try:
                new_prof = self.profile_svc.copy_profile(prof, new_name, new_company)
                QMessageBox.information(self, _("dlg.copy_profile_title"),
                                        _("dlg.copy_profile_ok", prof=str(new_prof)))
                self._load_profiles()
            except Exception as e:
                QMessageBox.warning(self, _("dlg.copy_profile_title"), str(e))
        elif act == a_del:
            prof_name = prof.company_name or prof.display_name or prof.profile_id
            n = getattr(prof, "mod_count", 0)
            ans1 = QMessageBox.question(self, _("dlg.delete_profile_title"),
                                        _("dlg.delete_profile_warn1", prof=prof_name, n=n),
                                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans1 != QMessageBox.Yes:
                return
            ans2 = QMessageBox.warning(self, _("dlg.delete_profile_title"),
                                       _("dlg.delete_profile_warn2"),
                                       QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans2 != QMessageBox.Yes:
                return
            try:
                self.profile_svc.delete_profile(prof, backup_first=True)
                QMessageBox.information(self, _("dlg.delete_profile_title"),
                                        _("dlg.delete_profile_ok"))
                if self.current_profile is not None and getattr(self.current_profile, "profile_id", None) == prof.profile_id:
                    self.current_profile = None
                    for t in (self.table_all, self.table_active):
                        t.setRowCount(0)
                self._load_profiles()
            except Exception as e:
                QMessageBox.warning(self, _("dlg.delete_profile_title"), str(e))

    def _on_profile_selected(self):
        # 兼容空壳（原先连接到 profiles_list 的信号不再触发）
        pass

    def _on_table_order_changed(self):
        self._sync_worklist_from_table()
        self._refresh_status_after_change()

    def _on_check_changed(self, item: QTableWidgetItem):
        if item.column() != COL_ENABLED: return
        # Bug A 修复：顶层重入 + 行有效性 guard
        # 1) 重入 guard（防止 _sync → 另一表 set_row_enabled → 虽 blockSignals，但 debounce/重填仍可能间接触发）
        guard = getattr(self, "_in_check_changed", False)
        if guard:
            return
        # 2) 行已被表移除（例如 missing mod 禁用后 _fill_table_impl 删掉行）→ 跳过后续刷新+详情
        if item.row() < 0:
            return
        self._in_check_changed = True
        try:
            # 3) 确认 item 仍真实挂在 table 上（避免 row remove 后 item.table() 是 None）
            owner_tbl = item.tableWidget()
            if owner_tbl is None:
                return
            if owner_tbl not in (self.table_all, self.table_active):
                return
            if item.row() >= owner_tbl.rowCount():
                return
            self._sync_worklist_from_table()
            self._schedule_refresh(order=True, filter=True, counts=True, status=True)
            # 详情面板同步（立即执行，不走防抖）—— 重新从 owner_tbl 取一次，避免 row delete 后错位
            try:
                pkg_item = owner_tbl.item(item.row(), COL_PKG)
                if pkg_item:
                    try:
                        pkg = pkg_item.text()
                        mod = self._lookup_mod(pkg)
                        self._show_mod_detail(pkg, mod, {})
                    except Exception:
                        pass
            except Exception:
                pass
        finally:
            self._in_check_changed = False

    def _on_mod_tab_changed(self, idx: int):
        self._current_mod_tab = "active" if idx == 1 else "all"
        self.table = self.table_active if idx == 1 else self.table_all
        # 切换 Tab 时重新构建对应当前 profile 的表格
        if self.current_profile:
            self._fill_table_for_profile(self.current_profile)

    # ---------- 搜索 ----------
    def _on_search_changed(self, text: str):
        """搜索框文本变化：仅更新关键字并重启 debounce 计时器，300ms 内无新输入才真正过滤。"""
        self._search_keyword = text.strip()
        # 重启 debounce 计时器（每次按键都重置 300ms 倒计时）
        self._search_debounce_timer.start()

    def _apply_search_now(self):
        """真正执行表格过滤（由 debounce 计时器或回车触发）。"""
        if hasattr(self, '_search_debounce_timer'):
            self._search_debounce_timer.stop()
        self._apply_filter_to_table()
        self.statusBar().showMessage(
            _("ui.sb_search", kw=self._search_keyword or "-") if self._search_keyword else "",
            3000
        )

    def _apply_preset(self):
        if not self.priority_svc: return
        self._sync_worklist_from_table()
        self.current_worklist = self.priority_svc.apply_preset(self.current_worklist)
        if self.current_profile: self._fill_table_for_profile(self.current_profile)
        QMessageBox.information(self, _("dlg.preset_title"), _("dlg.preset_msg"))

    def _on_tree_category_clicked(self, item, column=0):
        role = item.data(0, Qt.UserRole)
        if not role:
            return
        kind, value = role
        if kind == "__filter_all__":
            self._current_filter_cat = None
            self._apply_filter_to_table()
            self.statusBar().showMessage(_("ui.sb_filter_all"), 3000)
        elif kind == "__filter_cat__":
            self._current_filter_cat = value
            self._apply_filter_to_table()
            from services.category_service import label_of
            if value == "":
                self.statusBar().showMessage(_("ui.sb_filter_uncat"), 3000)
            else:
                self.statusBar().showMessage(_("ui.sb_filter_cat", label=label_of(value)), 3000)

    def _on_tree_category_menu(self, pos):
        it = self.tree_categories.itemAt(pos)
        menu = QMenu(self)
        if it is not None:
            role = it.data(0, Qt.UserRole)
            if role and role[0] == "__filter_cat__":
                cat_key = role[1]
                a_assign = menu.addAction(_("menu.assign_category"))
                menu.addSeparator()
                if cat_key:
                    # ---- 用户自定义分类专属操作 ----
                    a_cat_en = menu.addAction(_("ui.cat_enable"))
                    a_cat_dis = menu.addAction(_("ui.cat_disable"))
                    a_cat_tog = menu.addAction(_("ui.cat_toggle"))
                    menu.addSeparator()
                    # 整体上移子菜单：1/10/50/100 步
                    sm_up = menu.addMenu(_("ui.cat_move_up"))
                    a_up1 = sm_up.addAction(_("ui.tb_up"))
                    a_up10 = sm_up.addAction(_("ui.tb_up10"))
                    a_up50 = sm_up.addAction(_("ui.tb_up50"))
                    a_up100 = sm_up.addAction(_("ui.tb_up100"))
                    # 整体下移子菜单：1/10/50/100 步
                    sm_down = menu.addMenu(_("ui.cat_move_down"))
                    a_down1 = sm_down.addAction(_("ui.tb_down"))
                    a_down10 = sm_down.addAction(_("ui.tb_down10"))
                    a_down50 = sm_down.addAction(_("ui.tb_down50"))
                    a_down100 = sm_down.addAction(_("ui.tb_down100"))
                    a_cat_top = menu.addAction(_("ui.cat_top"))
                    a_cat_bot = menu.addAction(_("ui.cat_bottom"))
                    menu.addSeparator()
                    # ---- 文件夹管理 ----
                    a_rename = menu.addAction(_("menu.rename_folder"))
                    a_delete = menu.addAction(_("menu.delete_folder"))
                act = menu.exec(self.tree_categories.mapToGlobal(pos))
                if act == a_assign:
                    self._assign_checked_rows_to_category(cat_key)
                elif act == a_rename:
                    self._rename_folder(cat_key)
                elif act == a_delete:
                    self._delete_folder(cat_key)
                elif cat_key:
                    # 分类批量操作
                    if act == a_cat_en:
                        self._enable_category(cat_key)
                    elif act == a_cat_dis:
                        self._disable_category(cat_key)
                    elif act == a_cat_tog:
                        self._toggle_category(cat_key)
                    elif act == a_up1:
                        self._move_cat_up(cat_key, steps=1)
                    elif act == a_up10:
                        self._move_cat_up(cat_key, steps=10)
                    elif act == a_up50:
                        self._move_cat_up(cat_key, steps=50)
                    elif act == a_up100:
                        self._move_cat_up(cat_key, steps=100)
                    elif act == a_down1:
                        self._move_cat_down(cat_key, steps=1)
                    elif act == a_down10:
                        self._move_cat_down(cat_key, steps=10)
                    elif act == a_down50:
                        self._move_cat_down(cat_key, steps=50)
                    elif act == a_down100:
                        self._move_cat_down(cat_key, steps=100)
                    elif act == a_cat_top:
                        self._cat_top(cat_key)
                    elif act == a_cat_bot:
                        self._cat_bottom(cat_key)
                return
        a_new = menu.addAction(_("menu.new_folder"))
        act = menu.exec(self.tree_categories.mapToGlobal(pos))
        if act == a_new:
            self._create_folder()

    def _on_relocate(self):
        default_dir = Path.home()
        dest = QFileDialog.getExistingDirectory(self, _("dlg.relocate_pick"),
                                                str(default_dir))
        if not dest: return
        dest_p = Path(dest)
        ret = QMessageBox.question(
            self, _("dlg.relocate_confirm_title"),
            _("dlg.relocate_confirm_msg", orig=str(self.symlink.original), dest=str(dest_p)))
        if ret != QMessageBox.Yes: return
        self.statusBar().showMessage(_("ui.sb_migrating"))
        QApplication.processEvents()
        try:
            r = self.symlink.relocate_to(dest_p)
            if r.ok:
                QMessageBox.information(self, _("dlg.relocate_ok_title"), _("dlg.relocate_ok_msg", msg=r.message, orig=str(r.orig), target=str(r.target), method=str(r.method)))
            else:
                QMessageBox.warning(self, _("dlg.relocate_fail_title"), r.message)
        finally:
            self._update_link_status()

    def _on_unlink_restore(self):
        ret = QMessageBox.question(self, _("dlg.unlink_confirm_title"),
                                   _("dlg.unlink_confirm_msg"))
        if ret != QMessageBox.Yes: return
        try:
            r = self.symlink.unlink_and_restore()
            msg = r.message if r.ok else (_("dlg.unlink_fail_prefix") + r.message)
            QMessageBox.information(self, _("dlg.unlink_title"), msg)
        finally:
            self._update_link_status()

    def _on_repair_broken_link(self):
        default_dir = Path.home()
        dest = QFileDialog.getExistingDirectory(
            self,
            _("dlg.repair_pick"),
            str(default_dir))
        if not dest: return
        dest_p = Path(dest)
        sample = []
        try:
            for p in dest_p.iterdir():
                if p.is_dir() or p.suffix.lower() in (".scs", ".zip"):
                    sample.append(p.name)
                    if len(sample) >= 3: break
        except Exception: pass
        extra = ""
        if sample: extra = _("dlg.repair_samples", sample="、".join(sample))
        ret = QMessageBox.question(self, _("dlg.repair_confirm_title"),
                                   _("dlg.repair_confirm_msg", dest=str(dest_p), extra=extra))
        if ret != QMessageBox.Yes: return
        try:
            r = self.symlink.repair_broken_link(dest_p)
            title = _("dlg.repair_ok_title") if r.ok else _("dlg.repair_fail_title")
            msg = r.message
            if r.ok:
                msg += _("dlg.repair_ok_hint")
            QMessageBox.information(self, title, msg)
        finally:
            self._update_link_status()

    # ---------- 详情面板 ----------
    def _on_selection_changed(self, table_widget=None):
        tbl = table_widget if table_widget is not None else getattr(self, "table", None)
        if tbl is None: return
        rows = tbl.selected_rows()
        if not rows: return
        r = rows[-1]
        pkg = tbl.package_at(r)
        display = tbl.item(r, COL_NAME)
        version = tbl.item(r, COL_VERSION)
        source = tbl.item(r, COL_SOURCE)
        enabled = tbl._row_enabled(r)
        hint = {
            "display": display.text() if display else "",
            "version": version.text() if version else "",
            "source": source.text() if source else "",
            "enabled": enabled,
            "row": r,
        }
        mod = self._lookup_mod(pkg)
        self._show_mod_detail(pkg, mod, hint)

