"""Auto-split MainWindow Mixin（方法级拆分；不持有独立状态，仅把 MainWindow 方法按功能归档）。

Mixin 类本身不做 __init__ / 不 super()，所有 self.xxx 属性都来自 MainWindow 实例自身（已在 MainWindow.__init__ 中初始化）。
唯一注意：closeEvent 位于 _SignalMixin 中，其末尾会直接调用 `QMainWindow.closeEvent(self, event)` 跳过 MRO。
"""
from __future__ import annotations
from ui.l10n_dialog import L10nDialog
from .._mw_widgets import _LangSwitchDialog, SplashScreen, ModTable
from services.profile_service import ProfileService, ProfileInfo
from ui.save_editor_dialog import SaveEditorDialog
from .._mw_workers import _QuickScanWorker, _AsyncParseWorker, _WorkshopFetchWorker, _EnrichProfilesWorker
from services.i18n_service import _, tr, I18nNotifier, set_language, current_language, available_languages, language_display_name
from ..theme import ThemeManager, THEME_DARK, THEME_LIGHT, THEME_AUTO, QTB_DEFAULT, QTB_PRIMARY


# 从 main_window.py 复制的工具栏样式常量（避免循环导入）
QTB_DEFAULT = ""
QTB_PRIMARY = ""


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

from core.models import Mod
from services.i18n_service import _, tr
from ui.crash_check_dialog import CrashCheckDialog
import weakref


class _ToolbarMixin:
    def _build_ui(self):
        central = QWidget(); self.setCentralWidget(central)
        root = QVBoxLayout(central); root.setContentsMargins(8, 4, 8, 8); root.setSpacing(8)

        splitter = QSplitter(Qt.Horizontal)
        # --- 左：Profiles 树 + 分类文件夹树（资源管理器风格） + 软链接状态 ---
        left = QFrame(); left.setFrameShape(QFrame.StyledPanel); left.setObjectName("sidebarPanel")
        lv = QVBoxLayout(left); lv.setContentsMargins(10, 10, 10, 10); lv.setSpacing(8)

        # (1) 🎮 存档 Profiles 树
        self.lbl_profiles_title = QLabel(_("ui.lbl_profiles"))
        self.lbl_profiles_title.setObjectName("sectionLabel")
        lv.addWidget(self.lbl_profiles_title)
        self.tree_profiles = QTreeWidget()
        self.tree_profiles.setHeaderHidden(True)
        self.tree_profiles.setRootIsDecorated(False)
        self.tree_profiles.setItemsExpandable(False)
        self.tree_profiles.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree_profiles.setMinimumHeight(160)
        self.tree_profiles.itemSelectionChanged.connect(self._on_tree_profile_selected)
        self.tree_profiles.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree_profiles.customContextMenuRequested.connect(self._on_tree_profile_menu)
        lv.addWidget(self.tree_profiles, 2)
        # 加载顺序按钮
        self.btn_load_order = QPushButton(_("ui.btn_load_order"))
        self.btn_load_order.setCursor(Qt.PointingHandCursor)
        self.btn_load_order.clicked.connect(self._show_load_order_dialog)
        lv.addWidget(self.btn_load_order)

        # (2) 📂 分类文件夹树（资源管理器风格文件夹视图）
        self.gb_categories = QGroupBox(_("ui.gb_categories"))
        self.gb_categories.setObjectName("gb_categories")
        vb_cat = QVBoxLayout(self.gb_categories)
        self.tree_categories = QTreeWidget()
        self.tree_categories.setHeaderHidden(True)
        self.tree_categories.setRootIsDecorated(False)
        self.tree_categories.setItemsExpandable(False)
        self.tree_categories.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree_categories.setMinimumHeight(220)
        self.tree_categories.setAcceptDrops(True)
        self.tree_categories.setDragDropMode(QAbstractItemView.DropOnly)
        self.tree_categories.installEventFilter(self)
        # QAbstractItemView receives drag/drop events on its viewport.  The
        # filter on the tree alone never sees those events on Qt 6, so install
        # it on the viewport as well.
        self.tree_categories.viewport().setAcceptDrops(True)
        self.tree_categories.viewport().installEventFilter(self)
        self.tree_categories.itemClicked.connect(self._on_tree_category_clicked)
        self.tree_categories.itemChanged.connect(self._on_category_item_changed)
        self.tree_categories.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree_categories.customContextMenuRequested.connect(self._on_tree_category_menu)
        from services.category_service import all_folders
        self._cat_item_all = QTreeWidgetItem([_("ui.cat_all")])
        self._cat_item_all.setData(0, Qt.UserRole, ("__filter_all__", None))
        self.tree_categories.addTopLevelItem(self._cat_item_all)
        self._cat_item_uncategorized = QTreeWidgetItem([_("ui.cat_uncategorized")])
        self._cat_item_uncategorized.setData(0, Qt.UserRole, ("__filter_cat__", ""))
        # Uncategorized is a filter/assignment target only. It must never be
        # treated as a bulk enable/disable group, otherwise toggling it can
        # rewrite the whole active worklist.
        self._cat_item_uncategorized.setFlags(
            self._cat_item_uncategorized.flags() & ~Qt.ItemIsUserCheckable
        )
        self.tree_categories.addTopLevelItem(self._cat_item_uncategorized)
        self._cat_items: dict[str, QTreeWidgetItem] = {}
        for fname in all_folders():
            it = QTreeWidgetItem([_("ui.cat_prefix", label=fname)])
            it.setData(0, Qt.UserRole, ("__filter_cat__", fname))
            self._make_category_item_checkable(it)
            self._cat_items[fname] = it
            self.tree_categories.addTopLevelItem(it)
        # 默认选中"全部模组"
        self.tree_categories.setCurrentItem(self._cat_item_all)
        self._current_filter_cat: str | None = None
        vb_cat.addWidget(self.tree_categories)
        lv.addWidget(self.gb_categories, 3)

        # (3) 💾 软链接 GroupBox
        self.gb_symlink = QGroupBox(_("ui.gb_symlink"))
        self.gb_symlink.setObjectName("gb_symlink")
        vb = QVBoxLayout(self.gb_symlink)
        self.lbl_link_status = QLabel(_("ui.link_checking"))
        self.lbl_link_status.setWordWrap(True)
        vb.addWidget(self.lbl_link_status)
        self.btn_repair = QPushButton(_("ui.btn_repair"))
        self.btn_repair.clicked.connect(self._on_repair_broken_link)
        self.btn_repair.setVisible(False)
        self.btn_repair.setStyleSheet("color:#f38ba8; font-weight:600;")
        vb.addWidget(self.btn_repair)
        row = QHBoxLayout()
        self.btn_relocate = QPushButton(_("ui.btn_relocate"))
        self.btn_relocate.clicked.connect(self._on_relocate)
        self.btn_unlink = QPushButton(_("ui.btn_unlink"))
        self.btn_unlink.clicked.connect(self._on_unlink_restore)
        row.addWidget(self.btn_relocate); row.addWidget(self.btn_unlink)
        vb.addLayout(row)
        lv.addWidget(self.gb_symlink)
        splitter.addWidget(left)
        splitter.setStretchFactor(0, 1)

        # --- 中：模组表 Tab（全部/已启用） + 详情 垂直拆分 ---
        middle_splitter = QSplitter(Qt.Vertical)
        # 顶部"扫描进度条"容器（空闲隐藏，扫描/解析阶段 show）
        self._scan_progress_frame = QFrame()
        self._scan_progress_frame.setFrameShape(QFrame.StyledPanel)
        self._scan_progress_frame.setObjectName("scanProgressPanel")
        spf_lay = QHBoxLayout(self._scan_progress_frame)
        spf_lay.setContentsMargins(8, 6, 8, 6); spf_lay.setSpacing(8)
        self._scan_progress_label = QLabel(_("ui.sp_idle"))
        self._scan_progress_label.setObjectName("dimLabel")
        self._scan_progress_bar = QProgressBar()
        self._scan_progress_bar.setFixedHeight(18)
        self._scan_progress_bar.setTextVisible(True)
        self._scan_progress_bar.setFormat("")
        self._scan_progress_bar.setRange(0, 100)
        self._scan_progress_bar.setValue(0)
        btn_cancel = QPushButton(_("ui.sp_cancel"))
        btn_cancel.setCursor(Qt.PointingHandCursor)
        # btn_cancel 样式由全局主题覆盖
        btn_cancel.clicked.connect(self._cancel_ongoing_scan)
        spf_lay.addWidget(self._scan_progress_label, 1)
        spf_lay.addWidget(self._scan_progress_bar, 3)
        spf_lay.addWidget(btn_cancel, 0)
        self._scan_progress_frame.setVisible(False)
        middle_splitter.addWidget(self._scan_progress_frame)
        # 包裹表格的 QTabWidget（上面 Tab / 下面详情）
        self.tab_mods = QTabWidget()
        self.tab_mods.setTabsClosable(False)
        self.tab_mods.setDocumentMode(False)
        self._tab_page_all = QWidget()
        lay_all = QVBoxLayout(self._tab_page_all); lay_all.setContentsMargins(0, 0, 0, 0)
        self.table_all = ModTable()
        self.table_all.order_changed.connect(self._on_table_order_changed)
        self.table_all.itemSelectionChanged.connect(lambda: self._on_selection_changed(self.table_all))
        self.table_all.itemChanged.connect(self._on_check_changed)
        self.table_all.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table_all.customContextMenuRequested.connect(lambda p: self._on_mod_context_menu(self.table_all, p))
        lay_all.addWidget(self.table_all)
        self.tab_mods.addTab(self._tab_page_all, _("ui.tab_all_mods"))
        self._tab_page_active = QWidget()
        lay_act = QVBoxLayout(self._tab_page_active); lay_act.setContentsMargins(0, 0, 0, 0)
        self.table_active = ModTable()
        self.table_active.order_changed.connect(self._on_table_order_changed)
        self.table_active.itemSelectionChanged.connect(lambda: self._on_selection_changed(self.table_active))
        self.table_active.itemChanged.connect(self._on_check_changed)
        self.table_active.cellClicked.connect(self._on_active_table_cell_clicked)
        self.table_active.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table_active.customContextMenuRequested.connect(lambda p: self._on_mod_context_menu(self.table_active, p))
        lay_act.addWidget(self.table_active)
        self.tab_mods.addTab(self._tab_page_active, _("ui.tab_active_mods"))
        self.tab_mods.currentChanged.connect(self._on_mod_tab_changed)
        # 主 table 引用始终指向当前可见的 table（后续所有逻辑用 self.table）
        self.table = self.table_all
        self._mod_tables = {"all": self.table_all, "active": self.table_active}
        middle_splitter.addWidget(self.tab_mods)

        # --- 下：详情面板（横排，左=预览图，右=标题+作者+版本+描述） ---
        detail = QFrame(); detail.setFrameShape(QFrame.StyledPanel); detail.setObjectName("detailPanel")
        det_h = QHBoxLayout(detail); det_h.setContentsMargins(10, 10, 10, 10); det_h.setSpacing(12)
        self.preview = QLabel(_("ui.preview_empty"))
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedSize(320, 188)   # 276x162 + padding
        self.preview.setStyleSheet("background:#181825; color:#6c7086; border:1px solid #45475a; border-radius:8px;")
        det_h.addWidget(self.preview)
        info_box = QVBoxLayout()
        self.lbl_title = QLabel(_("ui.lbl_no_mod"))
        f = QFont(); f.setPointSize(12); f.setBold(True); self.lbl_title.setFont(f)
        self.lbl_meta = QLabel(_("ui.lbl_meta_dash"))
        self.lbl_meta.setObjectName("dimLabel")
        self.txt_desc = QPlainTextEdit(); self.txt_desc.setReadOnly(True)
        self.txt_desc.setPlaceholderText(_("ui.ph_desc"))
        info_box.addWidget(self.lbl_title); info_box.addWidget(self.lbl_meta); info_box.addWidget(self.txt_desc, 1)
        det_h.addLayout(info_box, 1)
        middle_splitter.addWidget(detail)
        middle_splitter.setStretchFactor(0, 3)
        middle_splitter.setStretchFactor(1, 2)

        splitter.addWidget(middle_splitter)
        splitter.setStretchFactor(1, 4)
        root.addWidget(splitter, 1)

    def _build_toolbar(self):
        tb = QToolBar(""); tb.setIconSize(QSize(20, 20))
        self.addToolBar(tb)
        # ---- (1) 独立按钮：重新扫描 ----
        act_rescan = QAction(_("ui.tb_rescan"), self); act_rescan.setProperty("i18n_key", "ui.tb_rescan"); act_rescan.triggered.connect(self._scan_all_mods)
        tb.addAction(act_rescan)
        tb.addSeparator()
        # ---- (2) 下拉按钮：模组操作 ▼ ----
        btn_mods = QToolButton(); btn_mods.setText(_("ui.tb_drop_mods")); btn_mods.setProperty("i18n_key", "ui.tb_drop_mods")
        btn_mods.setPopupMode(QToolButton.InstantPopup); btn_mods.setCursor(Qt.PointingHandCursor)
        btn_mods.setToolButtonStyle(Qt.ToolButtonTextOnly); btn_mods.setStyleSheet(QTB_DEFAULT)
        m_mods = QMenu(btn_mods)
        a_en = m_mods.addAction(_("ui.tb_enable")); a_en.setProperty("i18n_key", "ui.tb_enable"); a_en.triggered.connect(lambda: self._batch("enable"))
        a_dis = m_mods.addAction(_("ui.tb_disable")); a_dis.setProperty("i18n_key", "ui.tb_disable"); a_dis.triggered.connect(lambda: self._batch("disable"))
        a_tog = m_mods.addAction(_("ui.tb_toggle")); a_tog.setProperty("i18n_key", "ui.tb_toggle"); a_tog.triggered.connect(lambda: self._batch("toggle"))
        m_mods.addSeparator()
        # ---- 按分类批量操作（动态子菜单）----
        sm_cat_en = m_mods.addMenu(_("ui.grp_cat_enable")); sm_cat_en.setProperty("i18n_key", "ui.grp_cat_enable")
        sm_cat_dis = m_mods.addMenu(_("ui.grp_cat_disable")); sm_cat_dis.setProperty("i18n_key", "ui.grp_cat_disable")
        sm_cat_tog = m_mods.addMenu(_("ui.grp_cat_toggle")); sm_cat_tog.setProperty("i18n_key", "ui.grp_cat_toggle")
        self._cat_sm_en = sm_cat_en; self._cat_sm_dis = sm_cat_dis; self._cat_sm_tog = sm_cat_tog
        btn_mods.setMenu(m_mods); tb.addWidget(btn_mods)
        # 显示前动态重填分类子菜单
        m_mods.aboutToShow.connect(lambda: self._rebuild_mods_menu_categories())
        tb.addSeparator()
        # ---- (3) 下拉按钮：优先级 ▼ ----
        btn_prio = QToolButton(); btn_prio.setText(_("ui.tb_drop_prio")); btn_prio.setProperty("i18n_key", "ui.tb_drop_prio")
        btn_prio.setPopupMode(QToolButton.InstantPopup); btn_prio.setCursor(Qt.PointingHandCursor)
        btn_prio.setToolButtonStyle(Qt.ToolButtonTextOnly); btn_prio.setStyleSheet(QTB_DEFAULT)
        m_prio = QMenu(btn_prio)
        sm_step = m_prio.addMenu(_("ui.prio_grp_step"))
        a_up = sm_step.addAction(_("ui.tb_up")); a_up.setProperty("i18n_key", "ui.tb_up"); a_up.triggered.connect(self._move_up)
        a_down = sm_step.addAction(_("ui.tb_down")); a_down.setProperty("i18n_key", "ui.tb_down"); a_down.triggered.connect(self._move_down)
        sm_step.addSeparator()
        a_top = sm_step.addAction(_("ui.tb_top")); a_top.setProperty("i18n_key", "ui.tb_top"); a_top.triggered.connect(self._move_top)
        a_bot = sm_step.addAction(_("ui.tb_bot")); a_bot.setProperty("i18n_key", "ui.tb_bot"); a_bot.triggered.connect(self._move_bottom)
        sm_up = m_prio.addMenu(_("ui.prio_grp_up"))
        a_up100 = sm_up.addAction(_("ui.tb_up100")); a_up100.setProperty("i18n_key", "ui.tb_up100"); a_up100.triggered.connect(lambda: self._move_up_n(100))
        a_up50 = sm_up.addAction(_("ui.tb_up50")); a_up50.setProperty("i18n_key", "ui.tb_up50"); a_up50.triggered.connect(lambda: self._move_up_n(50))
        a_up10 = sm_up.addAction(_("ui.tb_up10")); a_up10.setProperty("i18n_key", "ui.tb_up10"); a_up10.triggered.connect(lambda: self._move_up_n(10))
        sm_down = m_prio.addMenu(_("ui.prio_grp_down"))
        a_down10 = sm_down.addAction(_("ui.tb_down10")); a_down10.setProperty("i18n_key", "ui.tb_down10"); a_down10.triggered.connect(lambda: self._move_down_n(10))
        a_down50 = sm_down.addAction(_("ui.tb_down50")); a_down50.setProperty("i18n_key", "ui.tb_down50"); a_down50.triggered.connect(lambda: self._move_down_n(50))
        a_down100 = sm_down.addAction(_("ui.tb_down100")); a_down100.setProperty("i18n_key", "ui.tb_down100"); a_down100.triggered.connect(lambda: self._move_down_n(100))
        m_prio.addSeparator()
        a_pre = m_prio.addAction(_("ui.tb_preset")); a_pre.setProperty("i18n_key", "ui.tb_preset"); a_pre.triggered.connect(self._apply_preset)
        m_prio.addSeparator()
        # ---- 按分类整体排序（动态子菜单）----
        sm_cup = m_prio.addMenu(_("ui.grp_cat_move_up")); sm_cup.setProperty("i18n_key", "ui.grp_cat_move_up")
        sm_cdown = m_prio.addMenu(_("ui.grp_cat_move_down")); sm_cdown.setProperty("i18n_key", "ui.grp_cat_move_down")
        sm_ctop = m_prio.addMenu(_("ui.grp_cat_top")); sm_ctop.setProperty("i18n_key", "ui.grp_cat_top")
        sm_cbot = m_prio.addMenu(_("ui.grp_cat_bottom")); sm_cbot.setProperty("i18n_key", "ui.grp_cat_bottom")
        self._cat_sm_up = sm_cup; self._cat_sm_down = sm_cdown; self._cat_sm_top = sm_ctop; self._cat_sm_bottom = sm_cbot
        btn_prio.setMenu(m_prio); tb.addWidget(btn_prio)
        m_prio.aboutToShow.connect(lambda: self._rebuild_prio_menu_categories())
        tb.addSeparator()
        # ---- 汉化按钮 ----
        act_l10n = QAction(_("ui.tb_l10n"), self)
        act_l10n.setProperty("i18n_key", "ui.tb_l10n")
        act_l10n.triggered.connect(self._open_l10n_dialog)
        tb.addAction(act_l10n)
        tb.addSeparator()
        # ---- (4) 下拉按钮：保存 ▼（加粗，保留高优先级按钮样式）----
        btn_save = QToolButton(); btn_save.setText(_("ui.tb_drop_save")); btn_save.setProperty("i18n_key", "ui.tb_drop_save")
        btn_save.setPopupMode(QToolButton.MenuButtonPopup); btn_save.setCursor(Qt.PointingHandCursor)
        f = QFont(); f.setBold(True); btn_save.setFont(f)
        btn_save.setObjectName("primaryButton"); btn_save.setToolButtonStyle(Qt.ToolButtonTextOnly); btn_save.setStyleSheet("")
        btn_save.clicked.connect(self._save_profile)
        m_save = QMenu(btn_save)
        a_save = m_save.addAction(_("ui.tb_save")); a_save.setProperty("i18n_key", "ui.tb_save"); a_save.triggered.connect(self._save_profile)
        a_backup = m_save.addAction(_("ui.tb_backup")); a_backup.setProperty("i18n_key", "ui.tb_backup"); a_backup.triggered.connect(self._do_backup_now)
        btn_save.setMenu(m_save); tb.addWidget(btn_save)
        tb.addSeparator()
        # ---- (5) 存档编辑器按钮（功能3-7：重命名/复制/金钱经验/解锁/维修加油）----
        act_se = QAction(_("menu.save_editor"), self); act_se.setProperty("i18n_key", "menu.save_editor")
        act_se.triggered.connect(lambda: self._open_save_editor(self.current_profile))
        tb.addAction(act_se)
        tb.addSeparator()
        # ---- (6) 「工具」dropdown（城市反查等）
        btn_tools = QToolButton(); btn_tools.setText(_("ui.tb_drop_tools")); btn_tools.setProperty("i18n_key", "ui.tb_drop_tools")
        btn_tools.setPopupMode(QToolButton.InstantPopup); btn_tools.setCursor(Qt.PointingHandCursor)
        btn_tools.setToolButtonStyle(Qt.ToolButtonTextOnly); btn_tools.setStyleSheet(QTB_DEFAULT)
        m_tools = QMenu(btn_tools)
        a_city = m_tools.addAction(_("menu.city_lookup")); a_city.setProperty("i18n_key", "menu.city_lookup"); a_city.triggered.connect(self._open_city_lookup)
        btn_tools.setMenu(m_tools); tb.addWidget(btn_tools)
        tb.addSeparator()
        # ---- (7) 🛡️ 崩溃排查按钮（Mod 加载预检 + Crashlog 解析）----
        btn_crash = QToolButton(); btn_crash.setText("🛡️"); btn_crash.setToolTip("崩溃排查：启动游戏监控 / Crashlog 解析")
        btn_crash.setCursor(Qt.PointingHandCursor)
        btn_crash.setToolButtonStyle(Qt.ToolButtonTextOnly); btn_crash.setStyleSheet(QTB_DEFAULT)
        btn_crash.clicked.connect(self._open_crash_dialog)
        tb.addWidget(btn_crash)
        # 保留工具栏动作引用供 retranslate 遍历
        self._tb_toolbuttons = [btn_mods, btn_prio, btn_save, btn_tools, btn_crash]
        self.btn_save = btn_save
        self._btn_priority = btn_prio
        self._action_save_editor = act_se
        self._tb_toolbars = [tb]  # 缓存工具栏引用，避免 findChildren 遍历
        # ---- 主题切换按钮 ----
        btn_theme = QToolButton(); btn_theme.setText("🎨"); btn_theme.setToolTip("主题切换")
        btn_theme.setCursor(Qt.PointingHandCursor)
        btn_theme.setToolButtonStyle(Qt.ToolButtonTextOnly); btn_theme.setStyleSheet(QTB_DEFAULT)
        m_theme = QMenu(btn_theme)
        act_dark  = m_theme.addAction("🌙 深色")
        act_light = m_theme.addAction("☀️ 浅色")
        act_auto  = m_theme.addAction("🖥️ 跟随系统")
        def _switch_theme(mode):
            from PySide6.QtWidgets import QApplication
            ThemeManager.instance().apply(QApplication.instance(), mode)
        act_dark.triggered.connect(lambda: _switch_theme(THEME_DARK))
        act_light.triggered.connect(lambda: _switch_theme(THEME_LIGHT))
        act_auto.triggered.connect(lambda: _switch_theme(THEME_AUTO))
        btn_theme.setMenu(m_theme)
        btn_theme.setPopupMode(QToolButton.InstantPopup)
        tb.addWidget(btn_theme)
        # ---- 搜索框（右对齐）----
        spacer = QWidget(); spacer.setObjectName("toolbarSpacer")
        spacer.setStyleSheet("#toolbarSpacer { background: transparent; border: none; }")
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.search_input = QLineEdit()
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setPlaceholderText(_("ui.ph_search"))
        self.search_input.setFixedWidth(280)
        # 性能优化：搜索 debounce 300ms，避免每次按键都全表过滤
        self._search_debounce_timer = QTimer(self)
        self._search_debounce_timer.setSingleShot(True)
        self._search_debounce_timer.setInterval(300)
        self._search_debounce_timer.timeout.connect(self._apply_search_now)
        self.search_input.textChanged.connect(self._on_search_changed)
        self.search_input.returnPressed.connect(self._apply_search_now)
        tb.addWidget(self.search_input)

    @staticmethod
    def _make_category_item_checkable(item: QTreeWidgetItem):
        """让分类节点像 Mod 行一样支持勾选，并允许显示部分勾选状态。"""
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(0, Qt.Unchecked)


    def _open_city_lookup(self):
        """打开城市反查 mod 对话框。"""
        if not self.current_profile:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, _("city_lookup.title"), _("city_lookup.no_profile"))
            return

        # 收集当前已启用 mod（按 priority_index 升序：越小优先级越高）
        enabled_mods = []
        try:
            for m in getattr(self, "all_mods", []) or []:
                if getattr(m, "is_enabled", False):
                    enabled_mods.append(m)
        except Exception:
            enabled_mods = []

        # 按 priority_index 升序（保证扫描顺序与游戏加载顺序一致）
        enabled_mods.sort(key=lambda m: getattr(m, "priority_index", 1 << 30))

        try:
            from ui.city_lookup_dialog import CityLookupDialog
            dlg = CityLookupDialog(
                parent=self,
                enabled_mods=enabled_mods,
                
                on_locate_mod=self._locate_mod_in_table,
            )
            dlg.exec()
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, _("city_lookup.title"), f"{{e}}")

    def _locate_mod_in_table(self, mod_id: str):
        """在主表格中定位并选中指定 mod_id 的行。"""
        try:
            tbl = self.table
            rows = tbl.rowCount()
            for r in range(rows):
                # ModTable 行数据通常存 mod_id 在 Qt.UserRole
                it = tbl.item(r, 0)
                if it is None:
                    continue
                row_mod_id = it.data(Qt.UserRole) or ""
                # 兼容 ModTable 不同的存储位置：先 UserRole 再 UserRole+1
                if not row_mod_id:
                    it2 = tbl.item(r, 0)
                    if it2:
                        row_mod_id = it2.data(Qt.UserRole + 1) or ""
                if row_mod_id == mod_id:
                    tbl.selectRow(r)
                    tbl.scrollToItem(tbl.item(r, 0), QAbstractItemView.PositionAtCenter)
                    return True
        except Exception:
            pass
        return False

    def _open_l10n_dialog(self):
        """打开汉化管理对话框"""
        if not self.current_profile:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, _("ui.l10n_title"), _("ui.l10n_no_profile"))
            return

        # 获取已启用mod列表（按优先级排序）
        try:
            active_mods = self.profile_svc.get_active_mods(self.current_profile)
        except Exception:
            active_mods = []

        if not active_mods:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, _("ui.l10n_title"), _("ui.l10n_no_mods"))
            return

        # 构建 (mod_path, display_name) 列表
        mod_list = []
        for active_entry in active_mods:
            # profile.sii may store entries as `package_name|display_name`.
            # Only the package portion maps to an on-disk .scs file.
            raw_entry = str(active_entry or "")
            pkg_name, sep, saved_title = raw_entry.partition("|")
            pkg_name = pkg_name.strip() if sep else Path(raw_entry).stem
            display_name = saved_title.strip() if sep else pkg_name
            if not pkg_name:
                continue
            # 复用主界面的多级索引，兼容 package_name、mod_id、Workshop 后缀及 `pkg|title`。
            mod = None
            try:
                mod = self._lookup_mod(pkg_name)
            except Exception:
                mod = self.all_mods_by_pkg.get(pkg_name)
            if mod is None:
                # 最后按 mod_id / manifest 名称 / 文件名做大小写不敏感匹配，
                # 兼容旧 profile 与扫描缓存之间的命名差异。
                needle = pkg_name.casefold()
                for candidate_mod in (getattr(self, "all_mods", []) or []):
                    names = {
                        str(getattr(candidate_mod, "mod_id", "") or ""),
                        str(getattr(getattr(candidate_mod, "manifest", None), "package_name", "") or "").split("|", 1)[0],
                        Path(str(getattr(candidate_mod, "package_path", "") or "")).stem,
                    }
                    if any(n.casefold() == needle for n in names if n):
                        mod = candidate_mod
                        break
            if mod is not None:
                package_path = str(getattr(mod, 'package_path', '') or '')
                if not Path(package_path).exists():
                    # Workshop 模组可能把实际内容放在扫描器保留的备用路径中。
                    package_path = str(getattr(mod, '_workshop_path', '') or package_path)
                mod_info = (
                    package_path or pkg_name,
                    getattr(mod, 'display_title', display_name) or display_name,
                )
            else:
                candidate = Path(pkg_name)
                if not candidate.is_absolute():
                    candidate = Path(self.paths.mod_dir) / candidate
                # Profiles often omit the extension; prefer the real SCS file.
                if not candidate.exists() and not candidate.suffix:
                    scs_candidate = candidate.with_suffix(".scs")
                    if scs_candidate.exists():
                        candidate = scs_candidate
                mod_info = (str(candidate), display_name)
            # 不把不存在的路径交给后台线程，否则异常会被跳过后显示“空汉化管理”。
            if Path(mod_info[0]).exists():
                mod_list.append(mod_info)

        if not mod_list:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self,
                _("ui.l10n_title"),
                "当前启用的 Mod 没有找到可读取的本地文件。请先完成 Mod 扫描后再打开汉化管理。",
            )
            return

        dialog = L10nDialog(self._l10n_service, self)
        dialog.start_extract(mod_list, self.paths.mod_dir)
        dialog.exec()

    def _build_menubar(self):
        """顶部菜单栏：语言切换（简化版，避免 QActionGroup 信号循环）"""
        try:
            mb = self.menuBar()
            lang_menu = mb.addMenu(_("menu.lang"))
            self._lang_menus = [lang_menu]
            self._lang_actions = {}
            for lang in available_languages():
                act = lang_menu.addAction(language_display_name(lang))
                act.setCheckable(True)
                if lang == current_language():
                    act.setChecked(True)
                # 直接连接，不经过 QActionGroup
                act.triggered.connect(lambda _c=False, l=lang: self._do_switch_language(l))
                self._lang_actions[lang] = act
        except Exception:
            pass
        # ---- Tools 菜单：城市反查 + 崩溃排查入口（预检 / Crashlog）----
        try:
            _mb = self.menuBar()
            _tools_menu = _mb.addMenu("工具(&T)")
            _a_city_m = _tools_menu.addAction(_("menu.city_lookup"))
            _a_city_m.setProperty("i18n_key", "menu.city_lookup")
            _a_city_m.triggered.connect(self._open_city_lookup)
            _tools_menu.addSeparator()
            _a_crash = _tools_menu.addAction("🛡️ 崩溃排查…")
            _a_crash.triggered.connect(self._open_crash_dialog)
        except Exception:
            pass

    def _show_scan_progress(self, phase_label: str, busy: bool = False, cur: int = 0, total: int = 0, fmt: str = "", detail: str = ""):
        """控制主进度条 + SplashScreen 同步更新。"""
        if self._splash is not None:
            self._splash.update_progress(phase_label, busy, cur, total, detail)
        if self._scan_progress_frame is not None:
            self._scan_progress_frame.setVisible(True)
        if self._scan_progress_label is not None:
            label = phase_label + (f"  ·  {detail}" if detail else "")
            self._scan_progress_label.setText(label)
            self._scan_progress_label.setToolTip(detail or phase_label)
        if self._scan_progress_bar is not None:
            if busy:
                self._scan_progress_bar.setRange(0, 0)
                self._scan_progress_bar.setFormat("")
            else:
                self._scan_progress_bar.setRange(0, max(total, 1))
                self._scan_progress_bar.setValue(max(0, min(cur, max(total, 1))))
                self._scan_progress_bar.setFormat(fmt or ("%v / %m" if total > 0 else ""))

    def _hide_scan_progress(self):
        if self._scan_progress_frame is not None:
            self._scan_progress_frame.setVisible(False)
        if self._scan_progress_bar is not None:
            self._scan_progress_bar.setRange(0, 100); self._scan_progress_bar.setValue(0); self._scan_progress_bar.setFormat("")
        if self._scan_progress_label is not None:
            self._scan_progress_label.setText(_("ui.sp_idle"))

    def _cancel_ongoing_scan(self):
        """取消按钮：尝试停止快速扫描或异步解析（尽力而为）"""
        try:
            if self._quick_scan_worker is not None:
                self._quick_scan_worker.stop()
        except Exception:
            pass
        try:
            if self._async_parse_worker is not None:
                self._async_parse_worker.stop()
        except Exception:
            pass
        self._hide_scan_progress()
        self.statusBar().showMessage(_("ui.sb_scan_cancelled"), 4000)

    # ---------- 启动快速扫描（异步后台线程） ----------
    def _start_quick_scan(self):
        """启动快速扫描后台线程（替代原同步 scanner.scan），避免 UI 阻塞。"""
        self._show_scan_progress(_("ui.sp_phase_quick"), busy=True, detail=_("ui.sp_preparing"))
        self.statusBar().showMessage(_("ui.sb_scanning_quick"))
        w = _QuickScanWorker(self.scanner)
        self._quick_scan_worker = w
        w.progress_filename.connect(lambda fn: self._show_scan_progress(_("ui.sp_phase_quick"), busy=True, detail=fn))
        w.result_ready.connect(self._on_quick_scan_result)
        w.failed.connect(self._on_quick_scan_failed)
        w.start()

    def _scan_all_mods(self) -> bool:
        """尝试会话缓存恢复；失败则启动后台快速扫描线程。
        返回 True=已从缓存恢复，False=真实扫描（首次）。"""
        if self._try_restore_from_session():
            return True
        self._start_quick_scan()
        return False

    def _try_restore_from_session(self) -> bool:
        """
        启动阶段：若上次会话保存了扫描快照且目录签名一致，
        直接恢复 all_mods 列表显示，跳过快速扫描。
        返回 True=已恢复，False=需走真实扫描。
        """
        try:
            from services.session_service import load_scan_snapshot, load_last_session, is_recent_scan_snapshot
            from core.models import Mod
            from pathlib import Path as _P
        except Exception:
            return False
        # Make the potentially slow signature check visible on the startup
        # splash instead of leaving the user with a static label.
        try:
            if getattr(self, "_splash", None) is not None:
                self._splash.mark_phase_start("quick_scan", "读取模组目录签名…")
                QApplication.processEvents()
        except Exception:
            pass
        session_data = load_last_session()
        fast_restore = is_recent_scan_snapshot(session_data)
        # 兼容旧版本没有 metadata_ready 字段的快照：短时内视为已完成，
        # 避免升级后第一次启动又把全部 Mod 重解析一遍。
        # A recent, signature-matching snapshot is safe to restore even when
        # the previous process was interrupted before it flipped the flag to
        # True. Do not make a crashed/aborted startup re-parse every archive.
        metadata_ready = bool((session_data or {}).get("metadata_ready", False) or fast_restore)
        snap = load_scan_snapshot(
            self.paths.mod_dir,
            self.paths.workshop_content_dir,
            self.paths.mods_info_path,
        )
        try:
            if getattr(self, "_splash", None) is not None:
                self._splash._detail_label.setText("目录签名完成 · 正在恢复扫描缓存…" if snap else "目录签名完成 · 准备扫描模组…")
                if snap:
                    self._splash._log(f"发现缓存快照，共 {len(snap)} 个模组，开始恢复…", "info")
                else:
                    self._splash._log("未找到可用扫描缓存，将执行完整扫描。", "info")
                QApplication.processEvents()
        except Exception:
            pass
        if not snap:
            return False
        mods: list = []
        total_snap = len(snap)
        for idx, md in enumerate(snap, 1):
            try:
                m = Mod(
                    mod_id=str(md["mod_id"]),
                    package_path=str(md["package_path"]),
                    package_type=str(md.get("package_type") or ""),
                    file_size=int(md.get("file_size") or 0),
                    last_modified=float(md.get("last_modified") or 0.0),
                    mods_info_timestamp=int(md.get("mods_info_timestamp") or 0),
                )
                # 回填 manifest 元数据（避免重启后重新解析加密包）
                if md.get("display_name"):
                    m.manifest.display_name = md["display_name"]
                if md.get("package_name"):
                    m.manifest.package_name = md["package_name"]
                if md.get("author"):
                    m.manifest.author = md["author"]
                if md.get("package_version"):
                    m.manifest.package_version = md["package_version"]
                if md.get("compatible_versions"):
                    m.manifest.compatible_versions = list(md["compatible_versions"])
                if md.get("categories"):
                    m.manifest.categories = list(md["categories"])
                if md.get("icon_filename"):
                    m.manifest.icon_filename = md["icon_filename"]
                if md.get("description_filename"):
                    m.manifest.description_filename = md["description_filename"]
                if md.get("description"):
                    m.description = md["description"]
                # 直接恢复已下载的 Workshop 预览图，避免每次启动重新等待图片线程。
                if m.package_type == "workshop":
                    try:
                        from services.steam_workshop_service import get_cached_preview_bytes
                        from core.models import ModIcon
                        cached_icon = get_cached_preview_bytes(m.mod_id)
                        if cached_icon:
                            m.icon = ModIcon(raw_bytes=cached_icon, format="jpg", source_path="workshop-cache")
                    except Exception:
                        pass
                else:
                    try:
                        from services.session_service import load_mod_icon_cache
                        from core.models import ModIcon
                        cached_icon = load_mod_icon_cache(m.mod_id, m.last_modified)
                        if cached_icon:
                            raw_icon, icon_fmt = cached_icon
                            m.icon = ModIcon(
                                raw_bytes=raw_icon,
                                format=icon_fmt,
                                source_path="local-icon-cache",
                            )
                    except Exception:
                        pass
                # Do not synchronously reopen every archive here. Cached
                # preview bytes are restored above; only genuinely unresolved
                # local icons are queued for the post-startup repair pass.
                if md.get("category_tag"):
                    m.category_tag = md["category_tag"]
                mods.append(m)
            except Exception:
                continue
            # Keep the splash responsive and expose periodic progress while
            # rebuilding the cached Mod objects (including icon cache lookup).
            if idx == 1 or idx == total_snap or idx % 25 == 0:
                try:
                    if getattr(self, "_splash", None) is not None:
                        self._splash.set_phase_progress_ratio("quick_scan", idx, total_snap)
                        self._splash._detail_label.setText(f"正在恢复扫描缓存… {idx}/{total_snap}")
                        self._splash._log(f"缓存恢复进度：{idx}/{total_snap}", "info")
                    QApplication.processEvents()
                except Exception:
                    pass
        if not mods:
            return False
        try:
            if getattr(self, "_splash", None) is not None:
                self._splash._log(f"扫描缓存恢复完成，共恢复 {len(mods)} 个模组。", "success")
                self._splash._detail_label.setText(f"缓存恢复完成 · {len(mods)} 个模组")
                QApplication.processEvents()
        except Exception:
            pass
        # 恢复显示
        self.all_mods = mods
        self.all_mods_by_pkg = self._build_mod_index(mods)
        self._all_mods_by_id = {m.mod_id: m for m in mods}
        from services.priority_service import PriorityService
        self.priority_svc = PriorityService(mods)
        total_size = sum(m.file_size for m in mods) / 1024 / 1024
        # 使用缓存：同样启动异步解析 + Steam 查询（让 display_name/icon 慢慢补全）
        try:
            self._refresh_category_counts()
        except Exception:
            pass
        self._profile_fill_pending = False
        if self.current_profile:
            # Cache restore is the first data source for this profile, so build
            # the worklist from profile.sii once before subsequent callbacks
            # switch to render-only updates.
            self._fill_table_for_profile(self.current_profile, force=True)
        # 算新 mod：和上次会话对比（逻辑与真实扫描时一致）
        new_ids: list = []
        try:
            from services.session_service import get_new_mod_ids_vs_last_session
            new_ids = get_new_mod_ids_vs_last_session([m.mod_id for m in mods])
            # 过滤：存在于当前快照 mod_id 中才算"有"
            cur_ids = {m.mod_id for m in mods}
            new_ids = [mid for mid in new_ids if mid in cur_ids]
        except Exception:
            new_ids = []
        if new_ids:
            # During installer startup defer categorization until MainWindow is visible.
            if getattr(self, "_bootstrap_after_installer_splash", False):
                self._startup_pending_new_mod_ids = list(new_ids)
            elif not getattr(self, "_startup_new_mods_dialog_shown", False):
                from PySide6.QtCore import QTimer
                self._startup_new_mods_dialog_shown = True
                QTimer.singleShot(350, lambda ids=list(new_ids): self._show_new_mods_dialog(ids))
        self.statusBar().showMessage(
            _("ui.sb_from_cache", n=len(mods), size=f"{total_size:.1f}")
        )
        # A completed snapshot is authoritative for startup. Empty optional
        # fields (description/version) are common and must not trigger a full
        # re-parse of hundreds of archives on every launch. Icons are repaired
        # separately in the background, once per package revision.
        if metadata_ready:
            try:
                from services.session_service import load_mod_icon_probe
                icon_repair = [
                    m for m in mods
                    if m.package_type != "workshop"
                    and not m.icon.is_available
                    and load_mod_icon_probe(m.mod_id, m.last_modified) is not False
                ]
                if icon_repair:
                    from PySide6.QtCore import QTimer
                    QTimer.singleShot(250, lambda pending=list(icon_repair): self._start_async_parse(pending_mods=pending, icon_only=True))
            except Exception:
                pass
        else:
            # Older/incomplete snapshots still need one enrichment pass, but
            # this path is taken only when the snapshot was not marked ready.
            self._start_async_parse()
        try:
            from PySide6.QtCore import QTimer
            # A ready snapshot can still have Workshop entries whose preview
            # bytes were never downloaded (or whose old cache lacks a URL).
            # Let the existing worker repair only those entries in the
            # background; never block cache restore on Steam/network I/O.
            workshop_needs_fetch = any(
                m.package_type == "workshop" and not m.icon.is_available
                for m in mods
            )
            if not metadata_ready or workshop_needs_fetch:
                QTimer.singleShot(500, self._fetch_workshop_titles_async)
        except Exception:
            pass
        return True



    def _start_async_parse(self, pending_mods=None, icon_only: bool = False) -> None:
        """启动后台异步解析：解析加密包 + 回填目录型/scs/zip 的 icon/description/display_name"""
        self._async_parse_started = False
        self._async_parse_batch_refresh = True
        self._async_parse_icon_only = bool(icon_only)
        from services.external_extractor_service import supports_archive
        from pathlib import Path as _P
        pending = []
        source_mods = list(pending_mods) if pending_mods is not None else self.all_mods
        for m in source_mods:
            if icon_only and (m.package_type == "workshop" or m.icon.is_available):
                continue
            # 三个关键字段都齐全才跳过；任一缺失都送入解析队列
            if not icon_only and m.manifest.display_name and m.description and (
                m.package_type == "workshop" or (m.manifest.compatible_versions and m.icon.is_available)
            ):
                continue
            pp = _P(m.package_path)
            need = False
            if pp.is_dir():
                # 目录型：通常需要解析 workshop 子目录(universal/等)或嵌套 .scs 子包
                need = True
            elif pp.is_file():
                # 文件型 scs/zip：非加密解析很快(几ms)，加密则走外部解包
                need = True
            if need:
                pending.append(m)
        if not pending:
            # 无加密包，直接显示最终状态
            total_size = sum(m.file_size for m in self.all_mods) / 1024 / 1024
            self.statusBar().showMessage(_("ui.sb_scanned", n=len(self.all_mods), size=f"{total_size:.1f}"))
            return
        # 顶部主进度条 + 状态栏小进度条双显示
        if self._async_parse_progress is None:
            pb = QProgressBar()
            pb.setFixedWidth(240)
            pb.setTextVisible(True)
            self.statusBar().addPermanentWidget(pb)
            self._async_parse_progress = pb
        self._async_parse_progress.setRange(0, len(pending))
        self._async_parse_progress.setValue(0)
        self._async_parse_progress.setFormat("补全预览图 %v/%m" if icon_only else "解析加密包 %v/%m")
        # 顶部大进度条
        self._show_scan_progress(_("ui.sp_phase_parse"), busy=False, cur=0, total=len(pending), fmt="%v / %m", detail="")
        # 启动 QThread 工作线程
        worker_count = 4
        try:
            if self._splash is not None:
                worker_count = self._splash.worker_count()
        except Exception:
            pass
        worker = _AsyncParseWorker(
            pending, self.paths, max_workers=worker_count,
            worker_count_getter=lambda: self._splash.worker_count() if self._splash is not None else worker_count,
        )
        self._async_parse_worker = worker
        worker.progress.connect(self._on_async_parse_progress)
        worker.one_parsed.connect(self._on_mod_parsed)
        worker.finished.connect(self._on_async_parse_finished)
        self._async_parse_started = True
        worker.start()
        # Installer startup normally waits for complete enrichment.  Keep a
        # bounded fallback as well so a single encrypted archive cannot leave
        # the whole window disabled forever (fresh scans do not pass through
        # the restore branch in _bootstrap).
        if getattr(self, "_bootstrap_after_installer_splash", False):
            from PySide6.QtCore import QTimer
            QTimer.singleShot(8000, self._release_startup_lock_after_timeout)

    def _fetch_workshop_titles_async(self):
        """QThread 查询 Steam Workshop 标题，完成后刷新表格。"""
        def _refresh_table_after_fetch():
            if self.current_profile:
                self._render_current_worklist()
            self._refresh_category_counts()

        self._refresh_table_after_fetch = _refresh_table_after_fetch
        self._ws_fetch_worker = _WorkshopFetchWorker(self.all_mods, save_cache=True, parent=self)
        self._ws_fetch_worker.fetch_done.connect(self._refresh_table_after_fetch)
        self._ws_fetch_worker.start()

    def _load_profiles(self):
        # 性能优化：先用 quick=True 快速列出 profile 骨架（不解密不解 SII），
        # 立即更新 UI 显示 profile_id；再后台异步填充 display_name/company_name。
        # 原实现同步解密每个 profile.sii，数量多时启动明显卡顿。
        # Show every profile source that the game can use.  Hiding Steam/Cloud
        # entries made it possible to edit a local profile while ETS2 was
        # actually loading the Steam Cloud copy, so the game appeared to keep
        # the old Mod list after a successful save.
        self.profiles = list(self.profile_svc.list_profiles(quick=True))
        for i in range(self.tree_profiles.topLevelItemCount() - 1, -1, -1):
            self.tree_profiles.takeTopLevelItem(i)
        self._profile_tree_items: dict[str, QTreeWidgetItem] = {}
        first_with_mods: object | None = None
        first_any: object | None = None
        for p in self.profiles:
            name = p.display_name or p.save_name or "正在读取存档名称…"
            count = int(getattr(p, "mod_count", 0) or 0)
            source = {"local": "本地", "steam": "Steam", "cloud": "Steam Cloud"}.get(
                getattr(p, "location", ""), getattr(p, "location", "")
            )
            label = f"{name}（{source}，已启用 {count} 个 Mod）"
            it = QTreeWidgetItem([label])
            it.setData(0, Qt.UserRole, p)
            if getattr(p, "location", None) == "cloud":
                it.setForeground(0, QBrush(QColor("#1a6ab0")))
            self.tree_profiles.addTopLevelItem(it)
            key = str(getattr(p, "profile_sii", "") or getattr(p, "profile_id", str(id(p))))
            self._profile_tree_items[key] = it
            if first_any is None: first_any = it
            # 取 mod_count（来自 _enrich 解析结果）判断
            n_active = getattr(p, "mod_count", 0) or 0
            if first_with_mods is None and n_active > 0:
                first_with_mods = it
        if first_with_mods is not None:
            self.tree_profiles.setCurrentItem(first_with_mods)
            self._on_tree_profile_selected()
        elif first_any is not None:
            self.tree_profiles.setCurrentItem(first_any)
            self._on_tree_profile_selected()
        # 异步后台填充 display_name/company_name 并刷新树节点标签
        self._enrich_profiles_async()

    def _set_profile_editable_state(self, prof) -> None:
        """Make non-local profiles strictly read-only in the main window."""
        editable = bool(prof is not None and getattr(prof, "location", "") == "local")
        for widget in (getattr(self, "table_all", None),
                       getattr(self, "table_active", None),
                       getattr(self, "tree_categories", None)):
            if widget is not None:
                widget.setEnabled(editable)
        btn = getattr(self, "btn_save", None)
        if btn is not None:
            btn.setEnabled(editable)
            btn.setToolTip("仅本地存档可修改" if not editable else "")
        priority_btn = getattr(self, "_btn_priority", None)
        if priority_btn is not None:
            priority_btn.setEnabled(editable)
            priority_btn.setToolTip("仅本地存档可修改" if not editable else "")
        action = getattr(self, "_action_save_editor", None)
        if action is not None:
            action.setEnabled(editable)

    def _enrich_profiles_async(self):
        """QThread 逐个 enrich_profile，通过 Signal one_enriched 通知主线程更新树节点。"""
        if not self.profiles:
            return
        self._enrich_profiles_worker = _EnrichProfilesWorker(self.profile_svc, self.profiles, self)
        self._enrich_profiles_worker.one_enriched.connect(self._update_profile_tree_label)
        self._enrich_profiles_worker.start()

    def _update_profile_tree_label(self, pid: str, label: str, prof) -> None:
        """主线程更新单个 profile 树节点的显示文本。"""
        key = str(getattr(prof, "profile_sii", "") or pid)
        it = self._profile_tree_items.get(key) if hasattr(self, "_profile_tree_items") else None
        if it is not None:
            it.setText(0, label)
            # 若当前选中的就是这个 profile 且是其首次填充，触发一次选中刷新
            if self.tree_profiles.currentItem() is it:
                # 仅当原来 label 是 profile_id（未填充）时才触发选中事件
                try:
                    self._on_tree_profile_selected()
                except Exception:
                    pass

    # ---------- profile 切换 / 表格填装 ----------
    def _open_save_editor(self, prof: Optional[ProfileInfo]):
        """打开存档编辑器对话框（功能 3-7：重命名/复制设置/金钱经验/解锁/维修加油）。"""
        if not self.profiles:
            QMessageBox.information(self, _("se.title"), _("se.no_profile"))
            return
        try:
            dlg = SaveEditorDialog(
                profile_svc=self.profile_svc,
                profiles=self.profiles,
                initial_profile=prof,
                parent=self,
            )
            dlg.exec()
        except Exception as e:
            QMessageBox.warning(self, _("se.error"), str(e))

    # ===========================================================
    # 崩溃排查：CrashCheckDialog 入口 + Signal 委托处理
    # ===========================================================
    def _open_crash_dialog(self) -> None:
        """打开崩溃排查对话框（启动游戏监控 + Crashlog 解析）。
        使用 weakref 防止重复打开：若旧实例仍存活，直接展示并前置。"""
        ref = getattr(self, "_crash_dialog_ref", None)
        if ref is not None:
            existing = ref()
            if existing is not None:
                try:
                    existing.show()
                    existing.raise_()
                    existing.activateWindow()
                    return
                except Exception:
                    pass
        # 构造新实例
        try:
            dlg = CrashCheckDialog(
                profile=self.current_profile,
                all_mods=getattr(self, "all_mods", []) or [],
                parent=self,
            )
        except Exception as e:
            QMessageBox.warning(self, "崩溃排查", f"无法打开对话框: {e}")
            return
        # 3 Signal 连线：locate → 现有方法；disable/move → 新增方法
        try:
            dlg.signals.locate_mod_requested.connect(self._locate_mod_in_table)
            dlg.signals.disable_mods_requested.connect(self._disable_mods_and_save)
            dlg.signals.move_to_bottom_requested.connect(self._move_mod_to_bottom)
        except Exception:
            pass
        self._crash_dialog_ref = weakref.ref(dlg)
        dlg.exec()

    def _find_active_entry_for_mod_id(self, mod_id: str, active: list, all_mods: list):
        """4 层匹配 mod_id → active_mods 条目：
        L1 active 条目 stem 直接命中 / L2 Mod.mod_id / L3 manifest.package_name / L4 display_title。
        返回匹配到的 active_mods 条目字符串，未命中返回 None。"""
        if not mod_id or not active:
            return None
        from pathlib import Path as _P
        # L1：active_mods 条目 stem == mod_id（直接命中）
        for entry in active:
            if entry == mod_id or _P(entry).stem == mod_id:
                return entry
        # L2~L4：通过 Mod 对象反查 package_path stem，再匹配 active 条目
        target_stem = None
        for m in all_mods:
            if m.mod_id == mod_id:
                target_stem = _P(getattr(m, "package_path", "") or "").stem or m.mod_id
                break
        if target_stem is None:
            for m in all_mods:
                if (getattr(m.manifest, "package_name", "") or "") == mod_id:
                    target_stem = _P(getattr(m, "package_path", "") or "").stem or m.mod_id
                    break
        if target_stem is None:
            for m in all_mods:
                try:
                    if (m.display_title or "") == mod_id:
                        target_stem = _P(getattr(m, "package_path", "") or "").stem or m.mod_id
                        break
                except Exception:
                    continue
        if target_stem is not None:
            for entry in active:
                if _P(entry).stem == target_stem:
                    return entry
        return None

    def _disable_mods_and_save(self, mod_ids: list) -> None:
        """批量禁用 mod（4 层匹配）→ 从 active_mods 移除 → set_active_mods + 刷新表格。
        由 CrashCheckDialog.signals.disable_mods_requested(list) 触发。"""
        if not mod_ids:
            return
        if not self.current_profile:
            QMessageBox.information(self, "禁用 mod", "当前未选中任何 profile。")
            return
        try:
            active = list(self.profile_svc.get_active_mods(self.current_profile))
        except Exception as e:
            QMessageBox.warning(self, "禁用 mod", f"读取 active_mods 失败: {e}")
            return
        if not active:
            return
        all_mods = getattr(self, "all_mods", []) or []
        to_remove = set()
        for mid in mod_ids:
            if not mid:
                continue
            entry = self._find_active_entry_for_mod_id(mid, active, all_mods)
            if entry is not None:
                to_remove.add(entry)
        if not to_remove:
            return
        new_active_entries = [e for e in active if e not in to_remove]
        # P2 async priority: memory-only + dirty, user must click 保存
        self._sync_worklist_from_table()
        try:
            rebuild = PriorityService.rebuild_from_active(self.priority_svc, self.current_worklist, new_active_entries)
            if rebuild:
                self.current_worklist = rebuild
        except Exception:
            pass
        try: self._mark_priority_dirty(f"检测到 {len(to_remove)} 个 RED 告警 mod 已建议禁用 · 请点「保存」写回 profile")
        except Exception: pass
        try:
            self._render_current_worklist()
        except Exception:
            pass
        QMessageBox.information(
            self, "已禁用（未保存）",
            f"已禁用 {len(to_remove)} 个 RED 告警 mod。所有变更都还在内存中，\n"
            f"请点工具栏「保存 ▼」→「保存 profile」后才真正写回游戏 profile。"
        )

    def _move_mod_to_bottom(self, mod_id: str) -> None:
        """将指定 mod 移到 active_mods 末尾（加载顺序最底）→ set_active_mods + 刷新。
        由 CrashCheckDialog.signals.move_to_bottom_requested(str) 触发。"""
        if not mod_id:
            return
        if not self.current_profile:
            QMessageBox.information(self, "移动 mod", "当前未选中任何 profile。")
            return
        try:
            active = list(self.profile_svc.get_active_mods(self.current_profile))
        except Exception as e:
            QMessageBox.warning(self, "移动 mod", f"读取 active_mods 失败: {e}")
            return
        if not active:
            return
        all_mods = getattr(self, "all_mods", []) or []
        entry = self._find_active_entry_for_mod_id(mod_id, active, all_mods)
        if entry is None:
            return
        try:
            idx = active.index(entry)
        except ValueError:
            return
        if idx == len(active) - 1:
            return  # 已在最底
        active.pop(idx)
        active.append(entry)
        # P2 async priority: memory-only + dirty, user must click 保存
        self._sync_worklist_from_table()
        try:
            rebuild = PriorityService.rebuild_from_active(self.priority_svc, self.current_worklist, active)
            if rebuild:
                self.current_worklist = rebuild
        except Exception:
            pass
        try: self._mark_priority_dirty("已将崩溃嫌疑 mod 移到加载最末尾 · 请点工具栏「保存」写回 profile")
        except Exception: pass
        try:
            self._render_current_worklist()
        except Exception:
            pass
        QMessageBox.information(
            self, "已移动（未保存）",
            f"已将 mod 移至加载顺序最底: {mod_id}。所有变更都还在内存中，\n"
            f"请点工具栏「保存 ▼」→「保存 profile」后才真正写回游戏 profile。"
        )

