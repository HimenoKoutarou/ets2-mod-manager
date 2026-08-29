"""
ETS2 存档编辑器对话框（功能 3-7）
================================
功能：
  3. 重命名 profile（profile_name / company_name）
  4. 复制 profile 设置（active_mods / controls）
  5. 修改金钱 / 经验 / 等级
  6. 解锁地图 / 车库 / 经销商
  7. 卡车维修 / 加油

UI 结构：
  +---------------------------------------------------------------+
  |  存档选择：[Profile ▼]   存档槽位：[Slot ▼]   [刷新]           |
  +----------------------- TabWidget ----------------------------+
  |  💰 金钱经验 | 🏷 重命名 | 📋 复制设置 | 🔓 解锁 | 🚚 维修加油 |
  +---------------------------------------------------------------+
  |  状态栏：当前金钱 / 经验 / 等级                                 |
  +---------------------------------------------------------------+
"""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout, QLabel,
    QPushButton, QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QCheckBox,
    QTabWidget, QWidget, QGroupBox, QMessageBox, QFrame, QSizePolicy,
    QApplication, QProgressBar,
)

from services.profile_service import ProfileService, ProfileInfo
from services.save_editor_service import SaveEditorService, SaveSlotInfo
from services.i18n_service import _


# =========================================================================
#  后台线程：读取/修改存档（防止 UI 卡顿）
# =========================================================================

class _ReadStatsWorker(QThread):
    """后台读取当前金钱/经验/等级。"""
    result_ready = Signal(object, object, object)  # money, xp, level

    def __init__(self, svc: SaveEditorService, slot: SaveSlotInfo, parent=None):
        super().__init__(parent)
        self._svc = svc
        self._slot = slot

    def run(self):
        try:
            money = self._svc.read_current_money(self._slot)
            xp = self._svc.read_current_xp(self._slot)
            level = self._svc.read_current_level(self._slot)
            self.result_ready.emit(money, xp, level)
        except Exception:
            self.result_ready.emit(None, None, None)


class _ApplyWorker(QThread):
    """后台执行修改操作。"""
    result_ready = Signal(bool, str)  # success, message

    def __init__(self, fn, *args, parent=None, **kwargs):
        super().__init__(parent)
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self):
        try:
            result = self._fn(*self._args, **self._kwargs)
            # 兼容不同返回类型
            if isinstance(result, tuple):
                success, msg = result
            elif isinstance(result, bool):
                success, msg = result, ""
            elif isinstance(result, int):
                success, msg = (result > 0), str(result)
            else:
                success, msg = True, str(result) if result is not None else ""
            self.result_ready.emit(success, msg)
        except Exception as e:
            self.result_ready.emit(False, str(e))


# =========================================================================
#  主对话框
# =========================================================================

class SaveEditorDialog(QDialog):
    """存档编辑器主对话框。"""

    def __init__(self, profile_svc: ProfileService, profiles: List[ProfileInfo],
                 initial_profile: Optional[ProfileInfo] = None, parent=None):
        super().__init__(parent)
        self.ps = profile_svc
        self.profiles = profiles
        self.svc = SaveEditorService(profile_svc)
        self._current_slots: List[SaveSlotInfo] = []
        self._current_slot: Optional[SaveSlotInfo] = None
        self._worker: Optional[QThread] = None
        self._apply_worker: Optional[QThread] = None
        self._stats_generation = 0
        self._live_workers = set()
        self._closing_for_workers = False

        self.setWindowTitle(_("se.title"))
        self.resize(680, 560)
        self.setStyleSheet(ThemeManager.instance().effective_theme == 'dark' and DARK_THEME or LIGHT_THEME)

        self._build_ui()

        # 填充 profile 下拉
        for prof in profiles:
            label = prof.display_name or prof.save_name or prof.company_name or prof.profile_id
            self.cb_profile.addItem(label, userData=prof)
        if initial_profile is not None:
            for i in range(self.cb_profile.count()):
                if self.cb_profile.itemData(i) is initial_profile:
                    self.cb_profile.setCurrentIndex(i)
                    break
        if self.cb_profile.count() > 0:
            self._on_profile_changed(self.cb_profile.currentIndex())

    # ---------------- 样式 ----------------

    @staticmethod
    def _stylesheet() -> str:
        return ThemeManager.instance().effective_theme == 'dark' and DARK_THEME or LIGHT_THEME

    def _stylesheet_old() -> str:
        return """
        QDialog { background: #f6f8fa; }
        QGroupBox {
            font-weight: 600; border: 1px solid #d0d7de; border-radius: 8px;
            margin-top: 12px; padding: 14px 12px 10px 12px; background: #ffffff;
        }
        QGroupBox::title { left: 12px; padding: 0 6px; }
        QLabel { color: #24292f; }
        QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
            padding: 5px 8px; border: 1px solid #d0d7de; border-radius: 4px;
            background: #ffffff;
        }
        QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
            border-color: #2da44e;
        }
        QPushButton {
            padding: 6px 14px; border: 1px solid #d0d7de; border-radius: 4px;
            background: #ffffff; color: #24292f;
        }
        QPushButton:hover { background: #f3f4f6; }
        QPushButton:disabled { color: #8c959f; background: #f6f8fa; }
        QPushButton#primary {
            background: #2da44e; color: #ffffff; border-color: #1a7f37;
            font-weight: 600;
        }
        QPushButton#primary:hover { background: #2c974b; }
        QPushButton#danger {
            background: #ffffff; color: #cf222e; border-color: #cf222e;
        }
        QPushButton#danger:hover { background: #fef2f2; }
        QTabWidget::pane { border: 1px solid #d0d7de; border-radius: 4px; top: -1px; }
        QTabBar::tab {
            padding: 8px 14px; border: 1px solid transparent; border-bottom: none;
            background: #f6f8fa; color: #57606a;
        }
        QTabBar::tab:selected { background: #ffffff; color: #24292f; border-color: #d0d7de; }
        QTabBar::tab:hover:!selected { background: #eaeef2; }
        """

    # ---------------- 构建 UI ----------------

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(12)

        # ---- 顶部选择栏 ----
        sel_box = QGroupBox(_("se.sel_group"))
        sel_form = QFormLayout(sel_box)
        sel_form.setSpacing(8)

        prof_row = QHBoxLayout()
        self.cb_profile = QComboBox()
        self.cb_profile.setMinimumWidth(240)
        self.cb_profile.currentIndexChanged.connect(self._on_profile_changed)
        prof_row.addWidget(self.cb_profile, 1)
        self.btn_refresh = QPushButton(_("se.btn_refresh"))
        self.btn_refresh.clicked.connect(self._refresh_slots)
        prof_row.addWidget(self.btn_refresh)
        sel_form.addRow(QLabel(_("se.profile")), prof_row)

        slot_row = QHBoxLayout()
        self.cb_slot = QComboBox()
        self.cb_slot.setMinimumWidth(240)
        self.cb_slot.currentIndexChanged.connect(self._on_slot_changed)
        slot_row.addWidget(self.cb_slot, 1)
        self.lbl_slot_info = QLabel("")
        self.lbl_slot_info.setStyleSheet("color:#a6adc8;")
        slot_row.addWidget(self.lbl_slot_info, 1)
        sel_form.addRow(QLabel(_("se.save_slot")), slot_row)

        outer.addWidget(sel_box)

        # ---- Tab 区 ----
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_stats_tab(), _("se.tab_stats"))
        self.tabs.addTab(self._build_rename_tab(), _("se.tab_rename"))
        self.tabs.addTab(self._build_copy_tab(), _("se.tab_copy"))
        self.tabs.addTab(self._build_unlock_tab(), _("se.tab_unlock"))
        self.tabs.addTab(self._build_repair_tab(), _("se.tab_repair"))
        outer.addWidget(self.tabs, 1)

        # ---- 状态栏 ----
        status_row = QHBoxLayout()
        self.lbl_status = QLabel(_("se.tip_select_profile"))
        self.lbl_status.setStyleSheet("color:#a6adc8; padding:4px 2px;")
        status_row.addWidget(self.lbl_status, 1)
        self.progress = QProgressBar()
        self.progress.setFixedHeight(8)
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 0)
        self.progress.hide()
        status_row.addWidget(self.progress, 0)
        outer.addLayout(status_row)

        # ---- 关闭按钮 ----
        close_row = QHBoxLayout()
        close_row.addStretch(1)
        self.btn_close = QPushButton(_("se.btn_close"))
        self.btn_close.clicked.connect(self.accept)
        close_row.addWidget(self.btn_close)
        outer.addLayout(close_row)

    # ---- Tab 1: 金钱 / 经验 / 等级 ----

    def _build_stats_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(12)

        # 当前值显示
        cur_box = QGroupBox(_("se.current_values"))
        cur_grid = QGridLayout(cur_box)
        cur_grid.setSpacing(8)
        self.lbl_cur_money = QLabel("—")
        self.lbl_cur_xp = QLabel("—")
        self.lbl_cur_level = QLabel("—")
        cur_grid.addWidget(QLabel(_("se.money")), 0, 0)
        cur_grid.addWidget(self.lbl_cur_money, 0, 1)
        cur_grid.addWidget(QLabel(_("se.xp")), 0, 2)
        cur_grid.addWidget(self.lbl_cur_xp, 0, 3)
        cur_grid.addWidget(QLabel(_("se.level")), 0, 4)
        cur_grid.addWidget(self.lbl_cur_level, 0, 5)
        v.addWidget(cur_box)

        # 修改区
        edit_box = QGroupBox(_("se.edit_values"))
        form = QFormLayout(edit_box)
        form.setSpacing(8)

        self.spn_money = QDoubleSpinBox()
        self.spn_money.setRange(-1e9, 1e12)
        self.spn_money.setDecimals(0)
        self.spn_money.setSingleStep(100000)
        self.spn_money.setSuffix(" €")
        form.addRow(_("se.target_money"), self.spn_money)

        money_hint_row = QHBoxLayout()
        self.chk_money_hint = QCheckBox(_("se.use_current_as_hint"))
        self.chk_money_hint.setChecked(True)
        money_hint_row.addWidget(self.chk_money_hint)
        self.btn_set_money = QPushButton(_("se.btn_set_money"))
        self.btn_set_money.setObjectName("primary")
        self.btn_set_money.clicked.connect(self._on_set_money)
        money_hint_row.addStretch(1)
        money_hint_row.addWidget(self.btn_set_money)
        form.addRow("", money_hint_row)

        # 经验
        self.spn_xp = QDoubleSpinBox()
        self.spn_xp.setRange(0, 1e10)
        self.spn_xp.setDecimals(0)
        self.spn_xp.setSingleStep(10000)
        self.spn_xp.setSuffix(" XP")
        form.addRow(_("se.target_xp"), self.spn_xp)

        xp_row = QHBoxLayout()
        self.chk_xp_hint = QCheckBox(_("se.use_current_as_hint"))
        self.chk_xp_hint.setChecked(True)
        xp_row.addWidget(self.chk_xp_hint)
        self.btn_set_xp = QPushButton(_("se.btn_set_xp"))
        self.btn_set_xp.setObjectName("primary")
        self.btn_set_xp.clicked.connect(self._on_set_xp)
        xp_row.addStretch(1)
        xp_row.addWidget(self.btn_set_xp)
        form.addRow("", xp_row)

        # 等级
        self.spn_level = QSpinBox()
        self.spn_level.setRange(1, 200)
        form.addRow(_("se.target_level"), self.spn_level)

        lvl_row = QHBoxLayout()
        self.chk_level_hint = QCheckBox(_("se.use_current_as_hint"))
        self.chk_level_hint.setChecked(True)
        lvl_row.addWidget(self.chk_level_hint)
        self.btn_set_level = QPushButton(_("se.btn_set_level"))
        self.btn_set_level.setObjectName("primary")
        self.btn_set_level.clicked.connect(self._on_set_level)
        lvl_row.addStretch(1)
        lvl_row.addWidget(self.btn_set_level)
        form.addRow("", lvl_row)

        v.addWidget(edit_box)
        v.addStretch(1)

        # 快捷按钮
        quick_box = QGroupBox(_("se.quick_actions"))
        qh = QHBoxLayout(quick_box)
        self.btn_money_1m = QPushButton("+1M €")
        self.btn_money_1m.clicked.connect(lambda: self._quick_money(1_000_000))
        self.btn_money_10m = QPushButton("+10M €")
        self.btn_money_10m.clicked.connect(lambda: self._quick_money(10_000_000))
        self.btn_money_100m = QPushButton("+100M €")
        self.btn_money_100m.clicked.connect(lambda: self._quick_money(100_000_000))
        self.btn_xp_to_level = QPushButton(_("se.btn_xp_for_next_level"))
        self.btn_xp_to_level.clicked.connect(self._on_xp_for_level)
        qh.addWidget(self.btn_money_1m)
        qh.addWidget(self.btn_money_10m)
        qh.addWidget(self.btn_money_100m)
        qh.addStretch(1)
        qh.addWidget(self.btn_xp_to_level)
        v.addWidget(quick_box)

        return w

    # ---- Tab 2: 重命名 ----

    def _build_rename_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(12)

        box = QGroupBox(_("se.rename_group"))
        form = QFormLayout(box)
        form.setSpacing(8)

        self.lbl_cur_name = QLabel("—")
        self.lbl_cur_company = QLabel("—")
        form.addRow(QLabel(_("se.cur_profile_name")), self.lbl_cur_name)
        form.addRow(QLabel(_("se.cur_company")), self.lbl_cur_company)

        self.edt_new_name = QLineEdit()
        self.edt_new_name.setPlaceholderText(_("se.ph_new_name"))
        form.addRow(QLabel(_("se.new_profile_name")), self.edt_new_name)

        self.edt_new_company = QLineEdit()
        self.edt_new_company.setPlaceholderText(_("se.ph_new_company"))
        form.addRow(QLabel(_("se.new_company")), self.edt_new_company)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_rename = QPushButton(_("se.btn_rename"))
        self.btn_rename.setObjectName("primary")
        self.btn_rename.clicked.connect(self._on_rename)
        btn_row.addWidget(self.btn_rename)
        form.addRow("", btn_row)

        v.addWidget(box)
        v.addStretch(1)

        # 说明
        tip = QLabel(_("se.rename_tip"))
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#a6adc8; padding:8px; background:#fff8e1; border:1px solid #ffe082; border-radius:4px;")
        v.addWidget(tip)
        return w

    # ---- Tab 3: 复制设置 ----

    def _build_copy_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(12)

        box = QGroupBox(_("se.copy_group"))
        form = QFormLayout(box)
        form.setSpacing(8)

        self.cb_copy_src = QComboBox()
        self.cb_copy_src.setMinimumWidth(240)
        form.addRow(QLabel(_("se.copy_src")), self.cb_copy_src)

        self.cb_copy_dst = QComboBox()
        self.cb_copy_dst.setMinimumWidth(240)
        form.addRow(QLabel(_("se.copy_dst")), self.cb_copy_dst)

        self.chk_copy_mods = QCheckBox(_("se.copy_active_mods"))
        self.chk_copy_mods.setChecked(True)
        form.addRow("", self.chk_copy_mods)

        self.chk_copy_controls = QCheckBox(_("se.copy_controls"))
        form.addRow("", self.chk_copy_controls)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_copy = QPushButton(_("se.btn_copy"))
        self.btn_copy.setObjectName("primary")
        self.btn_copy.clicked.connect(self._on_copy_settings)
        btn_row.addWidget(self.btn_copy)
        form.addRow("", btn_row)

        v.addWidget(box)
        v.addStretch(1)

        tip = QLabel(_("se.copy_tip"))
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#a6adc8; padding:8px; background:#e7f5ff; border:1px solid #74c0fc; border-radius:4px;")
        v.addWidget(tip)

        # 填充 profile 列表
        for prof in self.profiles:
            label = prof.display_name or prof.save_name or prof.company_name or prof.profile_id
            self.cb_copy_src.addItem(label, userData=prof)
            self.cb_copy_dst.addItem(label, userData=prof)
        return w

    # ---- Tab 4: 解锁 ----

    def _build_unlock_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(12)

        box = QGroupBox(_("se.unlock_group"))
        form = QFormLayout(box)
        form.setSpacing(10)

        tip = QLabel(_("se.unlock_warning"))
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#f38ba8; padding:6px;")
        form.addRow(tip)

        self.btn_unlock_dealers = QPushButton(_("se.btn_unlock_dealers"))
        self.btn_unlock_dealers.setObjectName("primary")
        self.btn_unlock_dealers.clicked.connect(lambda: self._on_unlock("dealers"))
        form.addRow("", self.btn_unlock_dealers)

        self.btn_unlock_garages = QPushButton(_("se.btn_unlock_garages"))
        self.btn_unlock_garages.setObjectName("primary")
        self.btn_unlock_garages.clicked.connect(lambda: self._on_unlock("garages"))
        form.addRow("", self.btn_unlock_garages)

        self.btn_unlock_all = QPushButton(_("se.btn_unlock_all"))
        self.btn_unlock_all.setObjectName("danger")
        self.btn_unlock_all.clicked.connect(self._on_unlock_all)
        form.addRow("", self.btn_unlock_all)

        v.addWidget(box)
        v.addStretch(1)
        return w

    # ---- Tab 5: 维修 / 加油 ----

    def _build_repair_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(12)

        box = QGroupBox(_("se.repair_group"))
        form = QFormLayout(box)
        form.setSpacing(10)

        self.btn_repair = QPushButton(_("se.btn_repair"))
        self.btn_repair.setObjectName("primary")
        self.btn_repair.clicked.connect(self._on_repair)
        form.addRow("", self.btn_repair)

        # 加油
        fuel_row = QHBoxLayout()
        self.spn_fuel = QDoubleSpinBox()
        self.spn_fuel.setRange(0, 10000)
        self.spn_fuel.setDecimals(1)
        self.spn_fuel.setSingleStep(10)
        self.spn_fuel.setSuffix(" L")
        self.spn_fuel.setValue(100.0)
        fuel_row.addWidget(self.spn_fuel)
        self.btn_refuel = QPushButton(_("se.btn_refuel"))
        self.btn_refuel.setObjectName("primary")
        self.btn_refuel.clicked.connect(self._on_refuel)
        fuel_row.addWidget(self.btn_refuel)
        form.addRow(_("se.target_fuel"), fuel_row)

        v.addWidget(box)
        v.addStretch(1)

        tip = QLabel(_("se.repair_tip"))
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#a6adc8; padding:8px; background:#fff8e1; border:1px solid #ffe082; border-radius:4px;")
        v.addWidget(tip)
        return w

    # =========================================================================
    #  事件处理
    # =========================================================================

    def _on_profile_changed(self, idx: int):
        if idx < 0 or idx >= self.cb_profile.count():
            self._current_slots = []
            self.cb_slot.clear()
            return
        prof = self.cb_profile.itemData(idx)
        if prof is None:
            return
        # 同时更新重命名 Tab 的当前值显示
        self.lbl_cur_name.setText(prof.display_name or "—")
        self.lbl_cur_company.setText(prof.company_name or "—")
        self.edt_new_name.setText(prof.display_name or "")
        self.edt_new_company.setText(prof.company_name or "")
        self._refresh_slots()

    def _refresh_slots(self):
        idx = self.cb_profile.currentIndex()
        if idx < 0:
            return
        prof = self.cb_profile.itemData(idx)
        if prof is None:
            return
        self.cb_slot.blockSignals(True)
        self.cb_slot.clear()
        try:
            slots = self.svc.list_save_slots(prof)
        except Exception as e:
            self.cb_slot.blockSignals(False)
            QMessageBox.warning(self, _("se.error"), _("se.err_list_slots", err=str(e)))
            return
        self._current_slots = slots
        for slot in slots:
            label = slot.slot_name
            if slot.file_time:
                try:
                    import datetime as _dt
                    label = f"{slot.slot_name}  ({_dt.datetime.fromtimestamp(slot.file_time).strftime('%Y-%m-%d %H:%M')})"
                except Exception:
                    pass
            self.cb_slot.addItem(label, userData=slot)
        self.cb_slot.blockSignals(False)
        if slots:
            self._on_slot_changed(0)
        else:
            self._current_slot = None
            self.lbl_slot_info.setText(_("se.no_slots"))
            self.lbl_status.setText(_("se.no_slots"))

    def _on_slot_changed(self, idx: int):
        if idx < 0 or idx >= len(self._current_slots):
            self._current_slot = None
            return
        self._current_slot = self._current_slots[idx]
        # 显示存档信息
        try:
            size_kb = self._current_slot.game_sii.stat().st_size // 1024
            self.lbl_slot_info.setText(f"{size_kb} KB")
        except Exception:
            self.lbl_slot_info.setText("")
        # 后台读取当前值
        self._refresh_stats()

    def _refresh_stats(self):
        if self._current_slot is None:
            self.lbl_cur_money.setText("—")
            self.lbl_cur_xp.setText("—")
            self.lbl_cur_level.setText("—")
            return
        # 禁用按钮 + 显示进度
        self._set_apply_buttons_enabled(False)
        self.progress.show()
        self.lbl_status.setText(_("se.status_reading"))
        # Do not overwrite/destroy a previous QThread while it is still
        # running. Results carry a generation so a slow old slot cannot update
        # the newly selected slot after the user switches quickly.
        self._stats_generation += 1
        generation = self._stats_generation
        worker = _ReadStatsWorker(self.svc, self._current_slot, self)
        self._worker = worker
        self._live_workers.add(worker)
        worker.result_ready.connect(
            lambda money, xp, level, g=generation, w=worker:
            self._on_stats_worker_result(g, w, money, xp, level)
        )
        worker.finished.connect(lambda w=worker: self._on_worker_finished(w))
        worker.start()

    def _on_worker_finished(self, worker):
        self._live_workers.discard(worker)
        if self._worker is worker:
            self._worker = None
        if self._apply_worker is worker:
            self._apply_worker = None
        worker.deleteLater()

    def _on_stats_worker_result(self, generation, worker, money, xp, level):
        if self._closing_for_workers or generation != self._stats_generation:
            return
        self._on_stats_read(money, xp, level)

    def _on_stats_read(self, money, xp, level):
        self.progress.hide()
        self._set_apply_buttons_enabled(True)
        if money is not None:
            self.lbl_cur_money.setText(f"€ {money:,.0f}")
            self.spn_money.setValue(float(money))
        else:
            self.lbl_cur_money.setText(_("se.read_failed"))
        if xp is not None:
            self.lbl_cur_xp.setText(f"{xp:,.0f} XP")
            self.spn_xp.setValue(float(xp))
        else:
            self.lbl_cur_xp.setText(_("se.read_failed"))
        if level is not None:
            self.lbl_cur_level.setText(f"Lv. {level}")
            self.spn_level.setValue(int(level))
        else:
            self.lbl_cur_level.setText(_("se.read_failed"))
        self.lbl_status.setText(_("se.status_ready"))

    def _set_apply_buttons_enabled(self, enabled: bool):
        for btn in (self.btn_set_money, self.btn_set_xp, self.btn_set_level,
                    self.btn_rename, self.btn_copy, self.btn_unlock_dealers,
                    self.btn_unlock_garages, self.btn_unlock_all,
                    self.btn_repair, self.btn_refuel,
                    self.btn_money_1m, self.btn_money_10m, self.btn_money_100m,
                    self.btn_xp_to_level):
            btn.setEnabled(enabled)

    # ---------- 功能 5: 金钱 / 经验 / 等级 ----------

    def _on_set_money(self):
        if not self._require_slot():
            return
        new_money = self.spn_money.value()
        hint = None
        if self.chk_money_hint.isChecked():
            try:
                hint = float(self.lbl_cur_money.text().replace("€", "").replace(",", "").strip())
            except Exception:
                hint = None
        self._run_apply(
            _("se.status_setting_money"),
            self.svc.set_player_money, self._current_slot, new_money, hint,
            success_msg=_("se.ok_money", val=f"{new_money:,.0f}"),
            fail_msg=_("se.fail_money"),
        )

    def _on_set_xp(self):
        if not self._require_slot():
            return
        new_xp = self.spn_xp.value()
        hint = None
        if self.chk_xp_hint.isChecked():
            try:
                hint = float(self.lbl_cur_xp.text().replace("XP", "").replace(",", "").strip())
            except Exception:
                hint = None
        self._run_apply(
            _("se.status_setting_xp"),
            self.svc.set_player_experience, self._current_slot, new_xp, hint,
            success_msg=_("se.ok_xp", val=f"{new_xp:,.0f}"),
            fail_msg=_("se.fail_xp"),
        )

    def _on_set_level(self):
        if not self._require_slot():
            return
        new_level = self.spn_level.value()
        # 设置等级时，同步设置 XP（达到对应等级所需的累计 XP）
        target_xp = SaveEditorService.xp_for_level(new_level)
        hint = None
        if self.chk_level_hint.isChecked():
            try:
                hint = int(self.lbl_cur_level.text().replace("Lv.", "").strip())
            except Exception:
                hint = None
        self._run_apply(
            _("se.status_setting_level"),
            self.svc.set_player_level, self._current_slot, new_level, hint,
            success_msg=_("se.ok_level", val=new_level),
            fail_msg=_("se.fail_level"),
            post_callback=lambda: self._on_set_xp_for_level_done(target_xp),
        )

    def _on_set_xp_for_level_done(self, target_xp: float):
        """设置等级后自动同步 XP（异步调用）。"""
        if self._current_slot is None:
            return
        # 直接调用，不走 apply worker（避免双重进度条）
        try:
            self.svc.set_player_experience(self._current_slot, target_xp, None)
        except Exception:
            pass

    def _on_xp_for_level(self):
        """快捷：将 XP 设为当前等级的下一级所需 XP。"""
        if not self._require_slot():
            return
        cur_lvl = self.spn_level.value()
        target_xp = SaveEditorService.xp_for_level(cur_lvl + 1)
        self.spn_xp.setValue(float(target_xp))
        self.lbl_status.setText(_("se.tip_xp_set", val=f"{target_xp:,.0f}"))

    def _quick_money(self, delta: float):
        """快捷加钱：在当前值基础上增加 delta。"""
        if not self._require_slot():
            return
        try:
            cur_text = self.lbl_cur_money.text().replace("€", "").replace(",", "").strip()
            cur = float(cur_text) if cur_text else 0.0
        except Exception:
            cur = self.spn_money.value()
        self.spn_money.setValue(cur + delta)

    # ---------- 功能 3: 重命名 ----------

    def _on_rename(self):
        idx = self.cb_profile.currentIndex()
        if idx < 0:
            QMessageBox.warning(self, _("se.error"), _("se.no_profile"))
            return
        prof = self.cb_profile.itemData(idx)
        if prof is None:
            return
        new_name = self.edt_new_name.text().strip()
        new_company = self.edt_new_company.text().strip()
        if not new_name and not new_company:
            QMessageBox.warning(self, _("se.error"), _("se.rename_empty"))
            return
        # 二次确认
        ans = QMessageBox.question(
            self, _("se.confirm_title"),
            _("se.confirm_rename", prof=prof.company_name or prof.display_name),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        self._run_apply(
            _("se.status_renaming"),
            self.svc.rename_profile, prof, new_name, new_company,
            success_msg=_("se.ok_rename"),
            fail_msg=_("se.fail_rename"),
            post_callback=self._refresh_slots,
        )

    # ---------- 功能 4: 复制设置 ----------

    def _on_copy_settings(self):
        src = self.cb_copy_src.currentData()
        dst = self.cb_copy_dst.currentData()
        if src is None or dst is None:
            QMessageBox.warning(self, _("se.error"), _("se.no_profile"))
            return
        if src is dst:
            QMessageBox.warning(self, _("se.error"), _("se.copy_same"))
            return
        if not (self.chk_copy_mods.isChecked() or self.chk_copy_controls.isChecked()):
            QMessageBox.warning(self, _("se.error"), _("se.copy_nothing"))
            return
        ans = QMessageBox.question(
            self, _("se.confirm_title"),
            _("se.confirm_copy", src=src.company_name or src.display_name,
              dst=dst.company_name or dst.display_name),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        self._run_apply(
            _("se.status_copying"),
            self.svc.copy_profile_settings, src, dst,
            self.chk_copy_mods.isChecked(), self.chk_copy_controls.isChecked(),
            success_msg=_("se.ok_copy"),
            fail_msg=_("se.fail_copy"),
        )

    # ---------- 功能 6: 解锁 ----------

    def _on_unlock(self, kind: str):
        if not self._require_slot():
            return
        msg_map = {
            "dealers": (_("se.confirm_unlock_dealers"),
                        _("se.status_unlocking_dealers"),
                        _("se.ok_unlock_dealers"),
                        _("se.fail_unlock_dealers"),
                        self.svc.unlock_all_dealers),
            "garages": (_("se.confirm_unlock_garages"),
                        _("se.status_unlocking_garages"),
                        _("se.ok_unlock_garages"),
                        _("se.fail_unlock_garages"),
                        self.svc.unlock_all_garages),
        }
        confirm_msg, status_msg, ok_msg, fail_msg, fn = msg_map[kind]
        ans = QMessageBox.question(
            self, _("se.confirm_title"), confirm_msg,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        self._run_apply(
            status_msg, fn, self._current_slot,
            success_msg=ok_msg, fail_msg=fail_msg,
        )

    def _on_unlock_all(self):
        if not self._require_slot():
            return
        ans = QMessageBox.question(
            self, _("se.confirm_title"), _("se.confirm_unlock_all"),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        slot = self._current_slot
        # 顺序执行 dealers + garages
        def _do_all():
            try:
                r1 = self.svc.unlock_all_dealers(slot)
            except Exception:
                r1 = False
            try:
                r2 = self.svc.unlock_all_garages(slot)
            except Exception:
                r2 = False
            return (r1 or r2), ""
        self._run_apply(
            _("se.status_unlocking_all"), _do_all,
            success_msg=_("se.ok_unlock_all"),
            fail_msg=_("se.fail_unlock_all"),
        )

    # ---------- 功能 7: 维修 / 加油 ----------

    def _on_repair(self):
        if not self._require_slot():
            return
        ans = QMessageBox.question(
            self, _("se.confirm_title"), _("se.confirm_repair"),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        self._run_apply(
            _("se.status_repairing"), self.svc.repair_truck, self._current_slot,
            success_msg=_("se.ok_repair"),
            fail_msg=_("se.fail_repair"),
        )

    def _on_refuel(self):
        if not self._require_slot():
            return
        fuel = self.spn_fuel.value()
        ans = QMessageBox.question(
            self, _("se.confirm_title"),
            _("se.confirm_refuel", val=f"{fuel:.1f}"),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        self._run_apply(
            _("se.status_refueling"), self.svc.refuel_truck, self._current_slot, fuel,
            success_msg=_("se.ok_refuel", val=f"{fuel:.1f}"),
            fail_msg=_("se.fail_refuel"),
        )

    # =========================================================================
    #  通用：执行修改 + 进度条 + 结果提示
    # =========================================================================

    def _require_slot(self) -> bool:
        if self._current_slot is None:
            QMessageBox.warning(self, _("se.error"), _("se.no_slot"))
            return False
        return True

    def _run_apply(self, status_msg: str, fn, *args,
                   success_msg: str = "", fail_msg: str = "",
                   post_callback=None):
        """在后台执行一个修改操作，并在完成后显示结果。"""
        self._set_apply_buttons_enabled(False)
        self.progress.show()
        self.lbl_status.setText(status_msg)
        if self._apply_worker is not None and self._apply_worker.isRunning():
            return
        worker = _ApplyWorker(fn, *args, parent=self)
        self._live_workers.add(worker)
        # 用闭包保存 post_callback
        def _on_done(success, msg):
            if self._closing_for_workers:
                return
            self.progress.hide()
            self._set_apply_buttons_enabled(True)
            if success:
                self.lbl_status.setText(success_msg or _("se.ok"))
                if success_msg:
                    QMessageBox.information(self, _("se.success"), success_msg)
            else:
                self.lbl_status.setText(fail_msg or _("se.failed"))
                if fail_msg:
                    QMessageBox.warning(self, _("se.error"), f"{fail_msg}\n{msg}" if msg else fail_msg)
            if success and post_callback is not None:
                try:
                    post_callback()
                except Exception:
                    pass
            # 自动刷新统计
            if self._current_slot is not None:
                self._refresh_stats()
        worker.result_ready.connect(_on_done)
        worker.finished.connect(lambda w=worker: self._on_worker_finished(w))
        worker.start()
        self._apply_worker = worker

    # =========================================================================
    #  关闭时清理
    # =========================================================================

    def closeEvent(self, event):
        running = [w for w in self._live_workers if w.isRunning()]
        if running:
            event.ignore()
            self._closing_for_workers = True
            self.setEnabled(False)
            self.lbl_status.setText("正在安全结束后台任务…")
            from PySide6.QtCore import QTimer
            QTimer.singleShot(100, self._retry_close_after_workers)
            return
        super().closeEvent(event)

    def _retry_close_after_workers(self):
        if any(w.isRunning() for w in self._live_workers):
            from PySide6.QtCore import QTimer
            QTimer.singleShot(100, self._retry_close_after_workers)
            return
        self._closing_for_workers = False
        self.setEnabled(True)
        self.close()
