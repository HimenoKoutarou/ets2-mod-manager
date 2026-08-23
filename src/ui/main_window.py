"""
ETS2 Mod Manager — 主窗口（PySide6）
布局：
  +-------------------+--------------------------------------------------+
  |  Profiles 侧栏    |  顶部工具栏（启用/禁用/↑↓/预设/保存/软链接迁移）  |
  |                   +--------------------------------------------------+
  |                   |  中央模组列表（表格支持多选、拖拽重排）            |
  |                   |  [x] 名称 | 来源 | 大小 | 适配版本 | 优先级      |
  |                   |  支持拖拽：InternalMove，上移/下移/置顶/置底      |
  |                   +--------------------------------------------------+
  |                   |  右侧详情面板：预览图 / 标题 / 作者 / 版本 / 描述 |
  +-------------------+--------------------------------------------------+
  |  底部状态栏：已启用 N/总数 M   加载顺序(上→下=先→后)  | SAVE PROFILE  |
  +----------------------------------------------------------------------+
"""
from __future__ import annotations

import io
import sys
import os
from pathlib import Path
from typing import List, Optional, Dict

from PySide6.QtCore import Qt, QSize, QMimeData, QByteArray, Signal, QTimer, QObject
from PySide6.QtGui import QAction, QIcon, QPixmap, QImage, QBrush, QColor, QFont, QDrag
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QFrame,
    QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem, QToolBar,
    QLabel, QPlainTextEdit, QPushButton, QStatusBar, QFileDialog, QMessageBox,
    QHeaderView, QAbstractItemView, QProgressBar, QCheckBox, QComboBox, QGroupBox,
    QSizePolicy, QTreeWidget, QTreeWidgetItem, QDialogButtonBox, QDialog, QTextBrowser, QTextEdit,
    QMenu, QScrollArea, QGridLayout, QLineEdit, QSpinBox, QTabWidget, QToolButton,
)

# --- 核心 / 服务层 import ---
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils.paths import detect_paths, ETS2Paths
from utils.symlink_manager import SymlinkManager, SymlinkResult
from core.mod_scanner import ModScanner
from core.models import Mod
from core.scs_archive import ScsArchiveReader
from core.sii_parser import parse_mods_info
from services.backup_service import BackupService
from services.profile_service import ProfileService, ProfileInfo
from services.priority_service import PriorityService
from services.i18n_service import _, tr, I18nNotifier, set_language, current_language, available_languages, language_display_name
from version import __version__
from services.update_service import UpdateService



# ---------------------------------------------------------------------------
#  启动加载屏（SplashScreen）——扫描期间显示，禁止主窗口交互
# ---------------------------------------------------------------------------
class SplashScreen(QWidget):
    """专业启动加载屏：显示详细扫描进度和当前任务。"""

    STEPS = ["init", "quick_scan", "parse"]
    STEP_LABELS = {
        "init": "初始化",
        "quick_scan": "快速扫描模组",
        "parse": "解析加密模组包",
    }

    def __init__(self, logo_path: str = "", parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFixedSize(520, 580)
        self._first_scan = False
        self._current_step = 0
        self._progress_percent = 0

        # --- 现代深色主题样式 ---
        self.setStyleSheet("""
            QWidget {
                background-color: #1e1e2e;
                color: #cdd6f4;
                font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
            }
            QLabel#titleLabel {
                font-size: 22px;
                font-weight: bold;
                color: #cdd6f4;
            }
            QLabel#subtitleLabel {
                font-size: 12px;
                color: #a6adc8;
            }
            QLabel#stepLabel {
                font-size: 14px;
                font-weight: 600;
                color: #89b4fa;
            }
            QLabel#percentLabel {
                font-size: 24px;
                font-weight: bold;
                color: #f38ba8;
            }
            QLabel#detailLabel {
                font-size: 11px;
                color: #a6adc8;
            }
            QLabel#warnLabel {
                background: #313244;
                border: 1px solid #f9e2af;
                border-radius: 8px;
                color: #f9e2af;
                font-size: 11px;
                padding: 10px 14px;
            }
            QProgressBar {
                border: none;
                border-radius: 6px;
                background-color: #313244;
                height: 8px;
                text-align: center;
            }
            QProgressBar::chunk {
                border-radius: 6px;
                background-color: linear-gradient(90deg, #89b4fa, #cba6f7);
            }
        """)

        # --- 主布局 ---
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 32, 36, 24)
        layout.setSpacing(16)

        # --- Logo + 标题区域 ---
        header_layout = QHBoxLayout()
        header_layout.setSpacing(16)

        logo_label = QLabel()
        logo_label.setFixedSize(80, 80)
        logo_label.setAlignment(Qt.AlignCenter)
        logo_pix = QPixmap(logo_path) if logo_path and Path(logo_path).exists() else QPixmap()
        if not logo_pix.isNull():
            scaled = logo_pix.scaled(72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            logo_label.setPixmap(scaled)
        else:
            logo_label.setText("🌸")
            logo_label.setStyleSheet("font-size:48px;")
        header_layout.addWidget(logo_label, 0, Qt.AlignVCenter)

        title_layout = QVBoxLayout()
        title_layout.setSpacing(2)
        title = QLabel("ETS2 Mod Manager")
        title.setObjectName("titleLabel")
        title_layout.addWidget(title)
        subtitle = QLabel("By Himeno Sena")
        subtitle.setObjectName("subtitleLabel")
        title_layout.addWidget(subtitle)
        header_layout.addLayout(title_layout, 1)

        layout.addLayout(header_layout)

        # --- 分隔线 ---
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background-color: #313244; max-height: 1px;")
        layout.addWidget(line)

        # --- 步骤信息 ---
        step_layout = QHBoxLayout()
        self._step_label = QLabel(self.STEP_LABELS["init"])
        self._step_label.setObjectName("stepLabel")
        step_layout.addWidget(self._step_label)
        step_layout.addStretch()
        self._step_counter = QLabel("步骤 1/3")
        self._step_counter.setStyleSheet("font-size: 12px; color: #6c7086;")
        step_layout.addWidget(self._step_counter)
        layout.addLayout(step_layout)

        # --- 进度显示 ---
        progress_layout = QVBoxLayout()
        progress_layout.setSpacing(8)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(8)
        progress_layout.addWidget(self._progress)

        self._percent_label = QLabel("0%")
        self._percent_label.setObjectName("percentLabel")
        self._percent_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        progress_layout.addWidget(self._percent_label)

        layout.addLayout(progress_layout)

        # --- 当前任务详情 ---
        detail_frame = QFrame()
        detail_frame.setStyleSheet("""
            QFrame {
                background-color: #313244;
                border-radius: 8px;
            }
        """)
        detail_layout = QVBoxLayout(detail_frame)
        detail_layout.setContentsMargins(12, 10, 12, 10)
        detail_layout.setSpacing(6)

        task_header = QLabel("当前任务")
        task_header.setStyleSheet("font-size: 10px; color: #6c7086; font-weight: bold;")
        detail_layout.addWidget(task_header)

        self._detail_label = QLabel("准备就绪...")
        self._detail_label.setObjectName("detailLabel")
        self._detail_label.setWordWrap(True)
        self._detail_label.setStyleSheet("font-size: 12px; color: #cdd6f4;")
        self._detail_label.setMinimumHeight(20)
        self._detail_label.setMaximumHeight(50)
        detail_layout.addWidget(self._detail_label)

        layout.addWidget(detail_frame)

        # --- 状态日志（可滚动）---
        self._log_area = QFrame()
        self._log_area.setStyleSheet("""
            QFrame {
                background-color: #181825;
                border-radius: 8px;
                padding: 8px;
            }
        """)
        log_layout = QVBoxLayout(self._log_area)
        log_layout.setContentsMargins(4, 4, 4, 4)
        log_header = QLabel("状态日志")
        log_header.setStyleSheet("font-size: 10px; color: #6c7086; font-weight: bold;")
        log_layout.addWidget(log_header)

        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setMaximumHeight(120)
        self._log_text.setStyleSheet("""
            QTextEdit {
                background: transparent;
                border: none;
                color: #a6adc8;
                font-family: "Consolas", "Microsoft YaHei", monospace;
                font-size: 11px;
                selection-background-color: #45475a;
            }
            QTextEdit QScrollBar:vertical {
                background: #181825;
                width: 8px;
                border: none;
            }
            QTextEdit QScrollBar::handle:vertical {
                background: #45475a;
                border-radius: 4px;
                min-height: 20px;
            }
            QTextEdit QScrollBar::handle:vertical:hover {
                background: #585b70;
            }
            QTextEdit QScrollBar::add-line:vertical,
            QTextEdit QScrollBar::sub-line:vertical {
                height: 0;
            }
            QTextEdit QScrollBar::add-page:vertical,
            QTextEdit QScrollBar::sub-page:vertical {
                background: none;
            }
        """)
        self._log_text.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self._log_text.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._log_text.document().setMaximumBlockCount(200)
        log_layout.addWidget(self._log_text)

        layout.addWidget(self._log_area)

        # --- 警告提示 ---
        self._warn_label = QLabel("")
        self._warn_label.setObjectName("warnLabel")
        self._warn_label.setWordWrap(True)
        self._warn_label.setVisible(False)
        layout.addWidget(self._warn_label)

        # --- 底部提示 ---
        footer = QLabel(_("splash.footer"))
        footer.setAlignment(Qt.AlignCenter)
        footer.setStyleSheet("font-size: 10px; color: #6c7086;")
        layout.addWidget(footer)

    def set_first_scan(self, is_first: bool):
        self._first_scan = is_first
        if is_first:
            self._warn_label.setText(chr(0x26a0) + "  " + _("splash.first_scan_warn"))
            self._warn_label.setVisible(True)
            self._log("首次扫描：解析加密包需要较长时间（10-30秒），请耐心等待...", "warn")
        else:
            self._warn_label.setVisible(False)

    def _log(self, message: str, level: str = "info"):
        color_map = {
            "info": "#a6adc8",
            "success": "#a6e3a1",
            "warn": "#f9e2af",
            "error": "#f38ba8",
        }
        color = color_map.get(level, "#a6adc8")
        html = f'<span style="color:{color};">{message}</span>'
        self._log_text.append(html)
        from PySide6.QtGui import QTextCursor
        cursor = self._log_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        self._log_text.setTextCursor(cursor)
        # 自动滚动到底部显示最新日志
        self._log_text.verticalScrollBar().setValue(
            self._log_text.verticalScrollBar().maximum()
        )

    def _step_index(self, phase: str) -> int:
        mapping = {
            "ui.sp_phase_quick": 1,
            "ui.sp_phase_parse": 2,
            "splash.phase_init": 0,
        }
        for key, idx in mapping.items():
            if key in phase:
                return idx
        return self._current_step

    def update_progress(self, phase: str, busy: bool = True, cur: int = 0, total: int = 0, detail: str = ""):
        # 更新步骤
        step_idx = self._step_index(phase)
        if step_idx != self._current_step:
            self._current_step = step_idx
            step_key = self.STEPS[min(step_idx, len(self.STEPS) - 1)]
            step_name = self.STEP_LABELS.get(step_key, phase)
            self._step_label.setText(step_name)
            self._step_counter.setText(f"步骤 {min(step_idx + 1, 3)}/3")
            self._log(f"[{step_name}] 开始执行...", "info")

        # 更新进度条
        if busy:
            self._progress.setRange(0, 0)
            self._percent_label.setText("处理中...")
            self._progress_percent = -1
        else:
            percent = int(cur / max(total, 1) * 100)
            self._progress.setRange(0, 100)
            self._progress.setValue(percent)
            self._percent_label.setText(f"{percent}% ({cur}/{total})")
            if percent != self._progress_percent and percent % 25 == 0:
                self._progress_percent = percent
                if percent == 100:
                    self._log(f"完成进度 {percent}%", "success")

        # 更新详情
        if detail:
            detail_short = detail[:60] + "..." if len(detail) > 60 else detail
            self._detail_label.setText(detail_short)
            if self._progress_percent == -1 or (not busy and cur == 1):
                self._log(f"处理中: {detail_short}", "info")

    def show_modal(self):
        self.show()
        self.raise_()
        # Don't call activateWindow - keeps focus on main window
        
    def show_normal(self):
        """显示Splash但不抢占焦点，允许用户继续使用其他应用。"""
        self.show()

    def close_splash(self):
        self._log("扫描完成！", "success")
        self.close()
        self.deleteLater()




# ---------------------------------------------------------------------------
#  模组表格：支持多选 + 拖拽重排（InternalMove），同时把 worklist dict 塞进每个 row item
# ---------------------------------------------------------------------------
COL_ENABLED = 0
COL_NAME = 1
COL_SOURCE = 2
COL_SIZE = 3
COL_VERSION = 4
COL_ORDER = 5
COL_PKG = 6  # 隐藏列：package_name


class ModTable(QTableWidget):
    order_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(0, 7, parent)
        self.setHorizontalHeaderLabels([_("tbl.col_check"), _("tbl.col_name"), _("tbl.col_source"), _("tbl.col_size"), _("tbl.col_version"), _("tbl.col_order"), "(pkg)"])
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDragDropOverwriteMode(False)
        self.setDefaultDropAction(Qt.MoveAction)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        self.horizontalHeader().setSectionResizeMode(COL_VERSION, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(COL_SOURCE, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(COL_SIZE, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(COL_ORDER, QHeaderView.ResizeToContents)
        self.setColumnHidden(COL_PKG, True)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setDefaultSectionSize(24)

    def dropEvent(self, event):
        super().dropEvent(event)
        self._renumber_order()
        self.order_changed.emit()

    def _renumber_order(self):
        # 拖拽后重新按启用顺序给出 order
        o = 0
        for r in range(self.rowCount()):
            en = self._row_enabled(r)
            if en:
                self.setItem(r, COL_ORDER, self._mk(str(o)))
                o += 1
            else:
                self.setItem(r, COL_ORDER, self._mk("—"))

    # 行 helper
    @staticmethod
    def _mk(text: str, bold=False, color=None, align=Qt.AlignLeft | Qt.AlignVCenter) -> QTableWidgetItem:
        it = QTableWidgetItem(text)
        it.setTextAlignment(align)
        if bold:
            f = it.font(); f.setBold(True); it.setFont(f)
        if color is not None:
            it.setForeground(QBrush(QColor(color)))
        return it

    def _row_enabled(self, row: int) -> bool:
        it = self.item(row, COL_ENABLED)
        return bool(it and it.checkState() == Qt.Checked)

    def set_row_enabled(self, row: int, enabled: bool):
        it = self.item(row, COL_ENABLED)
        if it:
            it.setCheckState(Qt.Checked if enabled else Qt.Unchecked)

    def add_mod_row(self, work_entry: dict, mod: Optional[Mod]):
        r = self.rowCount()
        self.insertRow(r)
        # checkbox
        en = work_entry.get("enabled", False)
        chk_item = QTableWidgetItem()
        chk_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled)
        chk_item.setCheckState(Qt.Checked if en else Qt.Unchecked)
        self.setItem(r, COL_ENABLED, chk_item)
        # name + 分类颜色
        name = (mod.display_title if mod else None) or work_entry["package_name"]
        name_item = self._mk(name, bold=en, color="#000000" if en else "#8a8a8a")
        self.setItem(r, COL_NAME, name_item)
        # source
        src_tip = ""
        if mod:
            src_tip = f"{mod.package_type} · {mod.package_path}"
        src_text = (mod.package_type if mod else "—") or "—"
        self.setItem(r, COL_SOURCE, self._mk(src_text))
        if src_tip:
            self.item(r, COL_SOURCE).setToolTip(src_tip)
        # size
        sz = mod.file_size if mod else 0
        size_txt = f"{sz/1024/1024:.1f} MB" if sz else "—"
        self.setItem(r, COL_SIZE, self._mk(size_txt, align=Qt.AlignRight | Qt.AlignVCenter))
        # version
        vtxt = (mod.display_version if mod else None) or "—"
        self.setItem(r, COL_VERSION, self._mk(vtxt))
        # order
        order = work_entry.get("order", -1)
        self.setItem(r, COL_ORDER, self._mk(str(order) if order >= 0 else "—", align=Qt.AlignCenter))
        # pkg (hidden)
        self.setItem(r, COL_PKG, self._mk(work_entry["package_name"]))

    def update_row_for_mod(self, row: int, mod: Mod) -> None:
        """更新指定行的 name/version/source 列（解析完成后刷新）"""
        en = self._row_enabled(row)
        # name
        name = mod.display_title or mod.mod_id
        old_name = self.item(row, COL_NAME)
        if old_name is not None:
            old_name.setText(name)
            f = old_name.font(); f.setBold(en); old_name.setFont(f)
            old_name.setForeground(QBrush(QColor("#000000" if en else "#8a8a8a")))
        # version
        vtxt = mod.display_version or "—"
        vitem = self.item(row, COL_VERSION)
        if vitem is not None:
            vitem.setText(vtxt)
        # source tooltip
        src_tip = f"{mod.package_type} · {mod.package_path}"
        sitem = self.item(row, COL_SOURCE)
        if sitem is not None:
            sitem.setText(mod.package_type or "—")
            sitem.setToolTip(src_tip)

    def find_row_by_pkg(self, package_name: str) -> Optional[int]:
        """按 package_name 查找行号（COL_PKG 隐藏列）"""
        for r in range(self.rowCount()):
            it = self.item(r, COL_PKG)
            if it and it.text() == package_name:
                return r
        return None

    def selected_rows(self) -> List[int]:
        rs = set(i.row() for i in self.selectedIndexes())
        return sorted(rs)

    def package_at(self, row: int) -> str:
        return self.item(row, COL_PKG).text()


# ---------------------------------------------------------------------------
#  主窗口
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{_('app.title')}  v{__version__}")
        self.resize(1280, 780)
        # 设置窗口图标
        icon_path = Path(__file__).resolve().parent.parent.parent / "assets" / "app_icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self._logo_path = str(Path(__file__).resolve().parent.parent.parent / "assets" / "logo.png")
        self._splash: Optional["SplashScreen"] = None

        # --- 初始化核心对象 ---
        self.paths: ETS2Paths = detect_paths()
        self.symlink = SymlinkManager(self.paths.mod_dir)
        self.scanner = ModScanner(self.paths.mod_dir, self.paths.workshop_content_dir, self.paths.mods_info_path)

        self.backup_svc = BackupService()
        self.profile_svc = ProfileService(self.paths, backup=self.backup_svc)
        self.all_mods: List[Mod] = []
        self.all_mods_by_pkg: Dict[str, Mod] = {}
        self.priority_svc = PriorityService([])
        self.current_worklist: List[dict] = []
        self.current_profile: Optional[ProfileInfo] = None
        self.profiles: List[ProfileInfo] = []
        self._current_filter_cat: str | None = None
        self._profile_fill_pending: bool = False
        self._all_mods_by_id: dict[str, object] = {}
        self._profile_tree_items: dict[str, QTreeWidgetItem] = {}
        self._cat_items: dict[str, QTreeWidgetItem] = {}
        # 异步扫描与加密包解析
        self._quick_scan_worker: Optional["_QuickScanWorker"] = None
        self._async_parse_worker: Optional["_AsyncParseWorker"] = None
        self._async_parse_progress: Optional[QProgressBar] = None
        # 顶部主进度条（显眼位置，扫描时显示，空闲隐藏）
        self._scan_progress_bar: Optional[QProgressBar] = None
        self._scan_progress_label: Optional[QLabel] = None
        self._scan_progress_frame: Optional[QFrame] = None
        # 搜索 + 过滤器
        self._search_keyword: str = ""
        self._current_mod_tab: str = "all"   # "all" | "active"

        # 自动更新服务
        self.update_svc = UpdateService(proxy_url="http://127.0.0.1:7897")
        self.update_svc.update_available.connect(self._on_update_available)
        self.update_svc.no_update_needed.connect(self._on_no_update_needed)
        self.update_svc.error_occurred.connect(self._on_update_error)
        self.update_svc.status_changed.connect(self._on_update_status_changed)
        self.update_svc.progress.connect(self._on_update_progress)
        self.update_svc.download_finished.connect(self._on_download_finished)
        self.update_svc.install_finished.connect(self._on_install_finished)

        self._ui_refresh_timer = QTimer(self)
        self._ui_refresh_timer.setInterval(80)
        self._ui_refresh_timer.timeout.connect(self._on_ui_refresh_timer)
        self._ui_refresh_timer_active = False

        self._build_ui()
        self._build_toolbar()
        self._build_menubar()
        self.setStatusBar(QStatusBar(self))
        self.statusBar().showMessage(_("ui.sb_ets2_doc_dir", dir=str(self.paths.documents_dir)))

        # 启动后扫描
        QTimer.singleShot(50, self._bootstrap)

        # 语言切换通知 → 刷新 UI
        try:
            I18nNotifier.instance().languageChanged.connect(self._on_language_changed)
        except Exception:
            pass

    # ---------- UI 构建 ----------
    def _build_ui(self):
        central = QWidget(); self.setCentralWidget(central)
        root = QVBoxLayout(central); root.setContentsMargins(4, 0, 4, 4)

        splitter = QSplitter(Qt.Horizontal)
        # --- 左：Profiles 树 + 分类文件夹树（资源管理器风格） + 软链接状态 ---
        left = QFrame(); left.setFrameShape(QFrame.StyledPanel)
        lv = QVBoxLayout(left); lv.setContentsMargins(6, 6, 6, 6); lv.setSpacing(6)

        # (1) 🎮 存档 Profiles 树
        lv.addWidget(QLabel(_("ui.lbl_profiles")))
        self.tree_profiles = QTreeWidget()
        self.tree_profiles.setHeaderHidden(True)
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
        gb_cat = QGroupBox(_("ui.gb_categories"))
        gb_cat.setObjectName("gb_categories")
        vb_cat = QVBoxLayout(gb_cat)
        self.tree_categories = QTreeWidget()
        self.tree_categories.setHeaderHidden(True)
        self.tree_categories.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree_categories.setMinimumHeight(220)
        self.tree_categories.itemClicked.connect(self._on_tree_category_clicked)
        self.tree_categories.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree_categories.customContextMenuRequested.connect(self._on_tree_category_menu)
        from services.category_service import all_folders
        self._cat_item_all = QTreeWidgetItem([_("ui.cat_all")])
        self._cat_item_all.setData(0, Qt.UserRole, ("__filter_all__", None))
        self.tree_categories.addTopLevelItem(self._cat_item_all)
        self._cat_item_uncategorized = QTreeWidgetItem([_("ui.cat_uncategorized")])
        self._cat_item_uncategorized.setData(0, Qt.UserRole, ("__filter_cat__", ""))
        self.tree_categories.addTopLevelItem(self._cat_item_uncategorized)
        self._cat_items: dict[str, QTreeWidgetItem] = {}
        for fname in all_folders():
            it = QTreeWidgetItem([_("ui.cat_prefix", label=fname)])
            it.setData(0, Qt.UserRole, ("__filter_cat__", fname))
            self._cat_items[fname] = it
            self.tree_categories.addTopLevelItem(it)
        # 默认选中"全部模组"
        self.tree_categories.setCurrentItem(self._cat_item_all)
        self._current_filter_cat: str | None = None
        vb_cat.addWidget(self.tree_categories)
        lv.addWidget(gb_cat, 3)

        # (3) 💾 软链接 GroupBox
        gb = QGroupBox(_("ui.gb_symlink"))
        gb.setObjectName("gb_symlink")
        vb = QVBoxLayout(gb)
        self.lbl_link_status = QLabel(_("ui.link_checking"))
        self.lbl_link_status.setWordWrap(True)
        vb.addWidget(self.lbl_link_status)
        self.btn_repair = QPushButton(_("ui.btn_repair"))
        self.btn_repair.clicked.connect(self._on_repair_broken_link)
        self.btn_repair.setVisible(False)
        self.btn_repair.setStyleSheet("color:#b71c1c; font-weight:600;")
        vb.addWidget(self.btn_repair)
        row = QHBoxLayout()
        self.btn_relocate = QPushButton(_("ui.btn_relocate"))
        self.btn_relocate.clicked.connect(self._on_relocate)
        self.btn_unlink = QPushButton(_("ui.btn_unlink"))
        self.btn_unlink.clicked.connect(self._on_unlink_restore)
        row.addWidget(self.btn_relocate); row.addWidget(self.btn_unlink)
        vb.addLayout(row)
        lv.addWidget(gb)
        splitter.addWidget(left)
        splitter.setStretchFactor(0, 1)

        # --- 中：模组表 Tab（全部/已启用） + 详情 垂直拆分 ---
        middle_splitter = QSplitter(Qt.Vertical)
        # 顶部"扫描进度条"容器（空闲隐藏，扫描/解析阶段 show）
        self._scan_progress_frame = QFrame()
        self._scan_progress_frame.setFrameShape(QFrame.StyledPanel)
        self._scan_progress_frame.setStyleSheet("QFrame{background:#f6f8fa;border:1px solid #d0d7de;border-radius:4px;}")
        spf_lay = QHBoxLayout(self._scan_progress_frame)
        spf_lay.setContentsMargins(8, 6, 8, 6); spf_lay.setSpacing(8)
        self._scan_progress_label = QLabel(_("ui.sp_idle"))
        self._scan_progress_label.setStyleSheet("color:#24292f;font-weight:500;")
        self._scan_progress_bar = QProgressBar()
        self._scan_progress_bar.setFixedHeight(18)
        self._scan_progress_bar.setTextVisible(True)
        self._scan_progress_bar.setFormat("")
        self._scan_progress_bar.setRange(0, 100)
        self._scan_progress_bar.setValue(0)
        self._scan_progress_bar.setStyleSheet(
            "QProgressBar{border:1px solid #d0d7de;border-radius:3px;text-align:center;background:#fff;}"
            "QProgressBar::chunk{background:linear-gradient(90deg,#2da44e,#1a7f37);border-radius:2px;}"
        )
        btn_cancel = QPushButton(_("ui.sp_cancel"))
        btn_cancel.setCursor(Qt.PointingHandCursor)
        btn_cancel.setStyleSheet("QPushButton{padding:2px 10px;border:1px solid #d0d7de;border-radius:3px;background:#fff;}"
                                 "QPushButton:hover{background:#f3f4f6;}")
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
        lay_all.addWidget(self.table_all)
        self.tab_mods.addTab(self._tab_page_all, _("ui.tab_all_mods"))
        self._tab_page_active = QWidget()
        lay_act = QVBoxLayout(self._tab_page_active); lay_act.setContentsMargins(0, 0, 0, 0)
        self.table_active = ModTable()
        self.table_active.order_changed.connect(self._on_table_order_changed)
        self.table_active.itemSelectionChanged.connect(lambda: self._on_selection_changed(self.table_active))
        self.table_active.itemChanged.connect(self._on_check_changed)
        lay_act.addWidget(self.table_active)
        self.tab_mods.addTab(self._tab_page_active, _("ui.tab_active_mods"))
        self.tab_mods.currentChanged.connect(self._on_mod_tab_changed)
        # 主 table 引用始终指向当前可见的 table（后续所有逻辑用 self.table）
        self.table = self.table_all
        self._mod_tables = {"all": self.table_all, "active": self.table_active}
        middle_splitter.addWidget(self.tab_mods)

        # --- 下：详情面板（横排，左=预览图，右=标题+作者+版本+描述） ---
        detail = QFrame(); detail.setFrameShape(QFrame.StyledPanel)
        det_h = QHBoxLayout(detail); det_h.setContentsMargins(6, 6, 6, 6)
        self.preview = QLabel(_("ui.preview_empty"))
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedSize(320, 188)   # 276x162 + padding
        self.preview.setStyleSheet("background:#1e1e1e; color:#aaa; border:1px solid #555; border-radius:6px;")
        det_h.addWidget(self.preview)
        info_box = QVBoxLayout()
        self.lbl_title = QLabel(_("ui.lbl_no_mod"))
        f = QFont(); f.setPointSize(12); f.setBold(True); self.lbl_title.setFont(f)
        self.lbl_meta = QLabel(_("ui.lbl_meta_dash"))
        self.lbl_meta.setStyleSheet("color:#555;")
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
        btn_mods.setToolButtonStyle(Qt.ToolButtonTextOnly); btn_mods.setStyleSheet("QToolButton{padding:4px 10px;border:1px solid #d0d7de;border-radius:4px;background:#fff;}QToolButton:hover{background:#f3f4f6;}QToolButton::menu-indicator{width:0px;}")
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
        btn_prio.setToolButtonStyle(Qt.ToolButtonTextOnly); btn_prio.setStyleSheet("QToolButton{padding:4px 10px;border:1px solid #d0d7de;border-radius:4px;background:#fff;}QToolButton:hover{background:#f3f4f6;}QToolButton::menu-indicator{width:0px;}")
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
        # ---- (4) 下拉按钮：保存 ▼（加粗，保留高优先级按钮样式）----
        btn_save = QToolButton(); btn_save.setText(_("ui.tb_drop_save")); btn_save.setProperty("i18n_key", "ui.tb_drop_save")
        btn_save.setPopupMode(QToolButton.MenuButtonPopup); btn_save.setCursor(Qt.PointingHandCursor)
        f = QFont(); f.setBold(True); btn_save.setFont(f)
        btn_save.setToolButtonStyle(Qt.ToolButtonTextOnly); btn_save.setStyleSheet("QToolButton{padding:4px 12px;border:1px solid #1a7f37;border-radius:4px;background:#2da44e;color:#fff;font-weight:700;}QToolButton:hover{background:#2c974b;}QToolButton::menu-indicator{image:none;width:4px;}")
        btn_save.clicked.connect(self._save_profile)
        m_save = QMenu(btn_save)
        a_save = m_save.addAction(_("ui.tb_save")); a_save.setProperty("i18n_key", "ui.tb_save"); a_save.triggered.connect(self._save_profile)
        a_backup = m_save.addAction(_("ui.tb_backup")); a_backup.setProperty("i18n_key", "ui.tb_backup"); a_backup.triggered.connect(self._do_backup_now)
        btn_save.setMenu(m_save); tb.addWidget(btn_save)
        # 保留工具栏动作引用供 retranslate 遍历
        self._tb_toolbuttons = [btn_mods, btn_prio, btn_save]
        # ---- 搜索框（右对齐）----
        tb.addSeparator()
        spacer = QWidget(); spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.search_input = QLineEdit()
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setPlaceholderText(_("ui.ph_search"))
        self.search_input.setFixedWidth(280)
        self.search_input.textChanged.connect(self._on_search_changed)
        self.search_input.returnPressed.connect(lambda: self._on_search_changed(self.search_input.text()))
        tb.addWidget(self.search_input)


    def _build_menubar(self):
        """顶部菜单栏：语言切换"""
        try:
            mb = self.menuBar()
            lang_menu = mb.addMenu(_("menu.lang"))
            from PySide6.QtGui import QActionGroup
            ag = QActionGroup(self)
            ag.setExclusive(True)
            self._lang_actions = {}
            for lang in available_languages():
                act = lang_menu.addAction(language_display_name(lang))
                act.setCheckable(True)
                if lang == current_language():
                    act.setChecked(True)
                act.triggered.connect(lambda _c=False, l=lang: self._do_switch_language(l))
                ag.addAction(act)
                self._lang_actions[lang] = act
        except Exception:
            pass

    def _do_switch_language(self, lang: str):
        if set_language(lang, emit=True):
            # _on_language_changed 由信号触发，会调用 _retranslate_all_ui
            # 这里只需更新勾选状态，不重复调用 retranslate
            for l, act in getattr(self, "_lang_actions", {}).items():
                act.setChecked(l == lang)

    def _on_language_changed(self, lang: str):
        for l, act in getattr(self, "_lang_actions", {}).items():
            act.setChecked(l == lang)
        self._retranslate_all_ui()

    def _retranslate_all_ui(self):
        """语言切换后刷新所有控件文本"""
        # 标题
        self.setWindowTitle(f"{_('app.title')}  v{__version__}")
        # 表格列头
        self.table.setHorizontalHeaderLabels([
            _("tbl.col_check"), _("tbl.col_name"), _("tbl.col_source"),
            _("tbl.col_size"), _("tbl.col_version"), _("tbl.col_order"), "(pkg)"
        ])
        # 按钮
        try:
            self.btn_load_order.setText(_("ui.btn_load_order"))
            self.btn_repair.setText(_("ui.btn_repair"))
            self.btn_relocate.setText(_("ui.btn_relocate"))
            self.btn_unlink.setText(_("ui.btn_unlink"))
        except Exception:
            pass
        # 左栏：QLabel / QGroupBox 标题（优先用 objectName，fallback 到文本匹配）
        try:
            lbl_profiles = self.findChild(QLabel, "lbl_profiles_title")
            if lbl_profiles:
                lbl_profiles.setText(_("ui.lbl_profiles"))
            else:
                for lbl in self.findChildren(QLabel):
                    t = lbl.text()
                    if "<b>" in t and ("Profiles" in t or "存档" in t or "📁" in t):
                        lbl.setText(_("ui.lbl_profiles")); break
            gb_cats = self.findChild(QGroupBox, "gb_categories")
            if gb_cats:
                gb_cats.setTitle(_("ui.gb_categories"))
            gb_sym = self.findChild(QGroupBox, "gb_symlink")
            if gb_sym:
                gb_sym.setTitle(_("ui.gb_symlink"))
            if not (gb_cats and gb_sym):
                for gb in self.findChildren(QGroupBox):
                    t = gb.title()
                    if not gb_cats and ("分类" in t or "Category" in t or "过滤" in t or "filter" in t or "点击" in t):
                        gb.setTitle(_("ui.gb_categories"))
                    elif not gb_sym and ("Mod 目录" in t or "Mod Dir" in t or "迁移" in t or "Migration" in t or "软链接" in t):
                        gb.setTitle(_("ui.gb_symlink"))
        except Exception:
            pass
        # 刷新详情面板默认文本 + 软链接状态
        try:
            self.preview.setText(_("ui.preview_empty"))
            self.lbl_title.setText(_("ui.lbl_no_mod"))
            self.lbl_meta.setText(_("ui.lbl_meta_dash"))
            self.txt_desc.setPlaceholderText(_("ui.ph_desc"))
            self._update_link_status()
        except Exception:
            pass
        # 刷新分类树（含计数）
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
        # 刷新工具栏动作文本（通过 i18n_key 属性查找，兼容所有语言）
        try:
            for tb in self.findChildren(QToolBar):
                for act in tb.actions():
                    key = act.property("i18n_key")
                    if key:
                        act.setText(_(key))
            # 菜单栏语言项标题
            for m in self.findChildren(QMenu):
                title = m.title()
                if ("语言" in title) or ("Language" in title) or ("Язык" in title) or ("🌐" in title):
                    m.setTitle(_("menu.lang"))
        except Exception:
            pass
        # 菜单栏中每个语言项的显示名
        try:
            for lang, act in getattr(self, "_lang_actions", {}).items():
                act.setText(language_display_name(lang))
        except Exception:
            pass
        # 状态栏
        try:
            self._refresh_status_after_change()
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

    def _on_quick_scan_result(self, mods_list: list, new_ids: list):
        """快速扫描完成：填 all_mods → 刷新表格 → 立即保存会话 → 新模组弹窗 → 启动异步解析 + Steam 查询"""
        self.all_mods = list(mods_list)
        self.all_mods_by_pkg = {m.mod_id: m for m in self.all_mods}
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
                    "icon_filename": getattr(m.manifest, "icon_filename", "") or "",
                    "description_filename": getattr(m.manifest, "description_filename", "") or "",
                    "icon_available": bool(getattr(m.icon, "is_available", False)),
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
            not m.manifest.display_name or not m.icon.is_available or not m.description
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
            from services.session_service import load_scan_snapshot
            from core.models import Mod
            from pathlib import Path as _P
        except Exception:
            return False
        snap = load_scan_snapshot(
            self.paths.mod_dir,
            self.paths.workshop_content_dir,
            self.paths.mods_info_path,
        )
        if not snap:
            return False
        mods: list = []
        for md in snap:
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
                if md.get("icon_filename"):
                    m.manifest.icon_filename = md["icon_filename"]
                if md.get("description_filename"):
                    m.manifest.description_filename = md["description_filename"]
                if md.get("icon_available"):
                    m.icon.is_available = True
                if md.get("description"):
                    m.description = md["description"]
                if md.get("category_tag"):
                    m.category_tag = md["category_tag"]
                mods.append(m)
            except Exception:
                continue
        if not mods:
            return False
        # 恢复显示
        self.all_mods = mods
        self.all_mods_by_pkg = {m.mod_id: m for m in mods}
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
            self._fill_table_for_profile(self.current_profile)
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
            from PySide6.QtCore import QTimer
            QTimer.singleShot(250, lambda ids=list(new_ids): self._show_new_mods_dialog(ids))
        self.statusBar().showMessage(
            _("ui.sb_from_cache", n=len(mods), size=f"{total_size:.1f}")
        )
        # 如果所有 mod 都已有 display_name，跳过异步解析（真正从缓存秒开）
        need_parse = any(
            not m.manifest.display_name or not m.icon.is_available or not m.description
            for m in mods
        )
        if need_parse:
            self._start_async_parse()
        try:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(500, self._fetch_workshop_titles_async)
        except Exception:
            pass
        return True



    def _start_async_parse(self) -> None:
        """启动后台异步解析：解析加密包 + 回填目录型/scs/zip 的 icon/description/display_name"""
        from services.external_extractor_service import supports_archive
        from pathlib import Path as _P
        pending = []
        for m in self.all_mods:
            # 三个关键字段都齐全才跳过；任一缺失都送入解析队列
            if m.manifest.display_name and m.icon.is_available and m.description:
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
        self._async_parse_progress.setFormat("解析加密包 %v/%m")
        # 顶部大进度条
        self._show_scan_progress(_("ui.sp_phase_parse"), busy=False, cur=0, total=len(pending), fmt="%v / %m", detail="")
        # 启动 QThread 工作线程
        worker = _AsyncParseWorker(pending, self.paths)
        self._async_parse_worker = worker
        worker.progress.connect(self._on_async_parse_progress)
        worker.one_parsed.connect(self._on_mod_parsed)
        worker.finished.connect(self._on_async_parse_finished)
        worker.start()

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
        # 刷新表格中对应的行（按 mod_id = COL_PKG 匹配）
        row = self.table.find_row_by_pkg(mod_id)
        if row is not None:
            self.table.update_row_for_mod(row, m)
            # 如果当前详情面板正在显示这个 mod，也刷新详情
            try:
                cur = self.table.currentRow()
                if cur == row:
                    self._show_mod_detail(mod_id, m)
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

    def _fetch_workshop_titles_async(self):
        """后台线程查询 Steam Workshop 标题，完成后刷新表格。"""
        from threading import Thread

        def _worker():
            try:
                from services.steam_workshop_service import fetch_and_fill_mods
                fetch_and_fill_mods(self.all_mods, save_cache=True)
            except Exception:
                pass
            # 在主线程刷新表格
            try:
                from PySide6.QtCore import QTimer
                QTimer.singleShot(0, self._refresh_table_after_fetch)
            except Exception:
                pass

        def _refresh_table_after_fetch():
            if self.current_profile:
                self._fill_table_for_profile(self.current_profile)
            self._refresh_category_counts()

        self._refresh_table_after_fetch = _refresh_table_after_fetch
        t = Thread(target=_worker, daemon=True)
        t.start()

    def _load_profiles(self):
        self.profiles = self.profile_svc.list_profiles()
        for i in range(self.tree_profiles.topLevelItemCount() - 1, -1, -1):
            self.tree_profiles.takeTopLevelItem(i)
        self._profile_tree_items: dict[str, QTreeWidgetItem] = {}
        first_with_mods: object | None = None
        first_any: object | None = None
        for p in self.profiles:
            label = str(p)
            it = QTreeWidgetItem([label])
            it.setData(0, Qt.UserRole, p)
            if getattr(p, "location", None) == "cloud":
                it.setForeground(0, QBrush(QColor("#1a6ab0")))
            self.tree_profiles.addTopLevelItem(it)
            self._profile_tree_items[getattr(p, "profile_id", str(id(p)))] = it
            if first_any is None: first_any = it
            # 取 active_mods 数量判断
            try:
                n_active = len(getattr(p, "active_mods", []) or [])
            except Exception:
                n_active = 0
            if first_with_mods is None and n_active > 0:
                first_with_mods = it
        if first_with_mods is not None:
            self.tree_profiles.setCurrentItem(first_with_mods)
            self._on_tree_profile_selected()
        elif first_any is not None:
            self.tree_profiles.setCurrentItem(first_any)
            self._on_tree_profile_selected()

    # ---------- profile 切换 / 表格填装 ----------
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
        super().closeEvent(event)

    def _on_tree_profile_selected(self):
        items = self.tree_profiles.selectedItems()
        if not items: return
        prof = items[0].data(0, Qt.UserRole)
        if prof is None: return
        self.current_profile = prof
        try:
            n_active = len(getattr(prof, "active_mods", []) or [])
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
        act = menu.exec(self.tree_profiles.mapToGlobal(pos))
        if act == a_lo:
            # 先把当前存档切到此 profile（以便加载顺序对话框使用）
            self.current_profile = prof
            self._show_load_order_dialog()
        elif act == a_bk:
            try:
                self.backup_svc.backup(getattr(prof, "profile_sii", None), tag="ui-menu-snapshot")
                QMessageBox.information(self, _("dlg.backup_ok_title"), _("dlg.backup_ok2", prof=str(prof)))
            except Exception as e:
                QMessageBox.warning(self, _("dlg.backup_fail_title"), str(e))

    def _on_profile_selected(self):
        # 兼容空壳（原先连接到 profiles_list 的信号不再触发）
        pass

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
                m = self.all_mods_by_pkg.get(entry["package_name"]) or (entry.get("mod") if isinstance(entry.get("mod"), Mod) else None)
                t.add_mod_row(entry, m)
            self._reorder_table_for(t)
            t.blockSignals(False)
        self._apply_filter_to_table()
        self._refresh_status_after_change()

    def _reorder_table_for(self, tbl):
        """对指定 tbl 按 current_worklist 重排序并 renumber"""
        tbl.setUpdatesEnabled(False)
        try:
            enabled_rows = []; disabled_rows = []
            for r in range(tbl.rowCount()):
                if tbl._row_enabled(r):
                    enabled_rows.append(self._take_row_from(tbl, r))
                else:
                    disabled_rows.append(self._take_row_from(tbl, r))
            tbl.setRowCount(0)
            ordered = [x for x in self.current_worklist if x.get("enabled")] +                       [x for x in self.current_worklist if not x.get("enabled")]
            pkg_order = [x["package_name"] for x in ordered]
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
        """把当前活动表格的勾选+顺序回写到 self.current_worklist，同时同步勾选状态到另一张表。"""
        # Step 0: 同步勾选状态（如果两张表都有对应 pkg 的行，两边勾选保持一致）
        try:
            src_tbl = self.table
            other_tbl = self.table_active if src_tbl is self.table_all else self.table_all
            sync_map: Dict[str, bool] = {}
            for r in range(src_tbl.rowCount()):
                pi = src_tbl.item(r, COL_PKG)
                if pi is None: continue
                sync_map[pi.text()] = src_tbl._row_enabled(r)
            for r in range(other_tbl.rowCount()):
                pi = other_tbl.item(r, COL_PKG)
                if pi is None: continue
                val = sync_map.get(pi.text())
                if val is None: continue
                if val != other_tbl._row_enabled(r):
                    other_tbl.set_row_enabled(r, val)
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

    def _on_table_order_changed(self):
        self._sync_worklist_from_table()
        self._refresh_status_after_change()

    def _on_check_changed(self, item: QTableWidgetItem):
        if item.column() != COL_ENABLED: return
        self._sync_worklist_from_table()
        # 重新构建行顺序：enabled 在前 disabled 在后（两张表）
        self._reorder_table_according_to_worklist()
        # active Tab 的过滤（勾选变化后，条目可能在 active 表出现/消失）
        try:
            self._apply_filter_to_table()
        except Exception:
            pass
        self._refresh_category_counts()
        self._refresh_status_after_change()
        # 同步详情面板标题加粗状态
        try:
            tbl = getattr(self, "table", None)
            if tbl is None: tbl = self.table_all
            pkg_item = tbl.item(item.row(), COL_PKG)
            if pkg_item:
                try:
                    self._show_mod_detail(pkg_item.text(), self.all_mods_by_pkg.get(pkg_item.text()), {})
                except Exception:
                    pass
        except Exception:
            pass

    def _batch(self, action: str):
        rows = self.table.selected_rows()
        if not rows:
            QMessageBox.information(self, _("dlg.hint_title"), _("dlg.hint_select_rows"))
            return
        self._sync_worklist_from_table()
        wl2 = PriorityService.batch_toggle(self.current_worklist, rows, action)
        self.current_worklist = wl2
        self._fill_table_for_profile(self.current_profile) if False else None
        # 直接重填
        if self.current_profile:
            self._fill_table_for_profile(self.current_profile)

    def _move(self, kind: str):
        rows = self.table.selected_rows()
        if not rows: return
        self._sync_worklist_from_table()
        if kind == "up": self.current_worklist = self.priority_svc.move_up(self.current_worklist, rows)
        elif kind == "down": self.current_worklist = self.priority_svc.move_down(self.current_worklist, rows)
        elif kind == "top": self.current_worklist = self.priority_svc.move_top(self.current_worklist, rows)
        elif kind == "bottom": self.current_worklist = self.priority_svc.move_bottom(self.current_worklist, rows)
        if self.current_profile: self._fill_table_for_profile(self.current_profile)

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
        self._fill_table_for_profile(self.current_profile)
        # 恢复选中（按 package_name 匹配）
        pkgs = []
        for r in rows:
            it = tbl.item(r, COL_PKG)
            if it: pkgs.append(it.text())
        for pkg in pkgs:
            rr = self.table.find_row_by_pkg(pkg)
            if rr is not None:
                self.table.selectRow(rr)
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
    def _on_mod_tab_changed(self, idx: int):
        self._current_mod_tab = "active" if idx == 1 else "all"
        self.table = self.table_active if idx == 1 else self.table_all
        # 切换 Tab 时重新构建对应当前 profile 的表格
        if self.current_profile:
            self._fill_table_for_profile(self.current_profile)

    # ---------- 搜索 ----------
    def _on_search_changed(self, text: str):
        self._search_keyword = text.strip()
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

    # ---------- 保存 profile ----------
    def _save_profile(self):
        if not self.current_profile:
            QMessageBox.warning(self, _("dlg.no_profile_title"), _("dlg.no_profile_save"))
            return
        self._sync_worklist_from_table()
        new_active = PriorityService.worklist_to_active(self.current_worklist)
        ret = QMessageBox.question(
            self, _("dlg.save_confirm_title"),
            _("dlg.save_confirm_msg", prof=self.current_profile.profile_id[:16], n=len(new_active)))
        if ret != QMessageBox.Yes: return
        try:
            wrote = self.profile_svc.set_active_mods(self.current_profile, new_active)
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
                mod = self.all_mods_by_pkg.get(pkg) if pkg else None
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
            mod = self.all_mods_by_pkg.get(pkg) if pkg else None
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
            mod = self.all_mods_by_pkg.get(pn)
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
        dlg = QDialog(self)
        dlg.setWindowTitle(_("dlg.nm_title", n=len(new_ids)))
        dlg.resize(660, 560)
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
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText(_("dlg.nm_ok"))
        bb.button(QDialogButtonBox.Cancel).setText(_("dlg.nm_cancel"))
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
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
        mod = self.all_mods_by_pkg.get(pkg)
        self._show_mod_detail(pkg, mod, hint)

    def _show_mod_detail(self, pkg: str, mod: Optional[Mod], hint: Optional[dict] = None):
        hint = hint or {}
        # 标题
        display_title = (mod.display_title if mod else "") or hint.get("display") or pkg or _("detail.none")
        version = (mod.manifest.package_version if mod else "") or hint.get("version") or _("detail.ver_notag")
        self.lbl_title.setText(_("ui.title_with_version", title=display_title, version=version))
        meta_parts = []
        if mod and mod.manifest.author:
            meta_parts.append(_("detail.author", v=mod.manifest.author))
        if mod:
            cats = mod.manifest.category_labels
            if cats: meta_parts.append(_("detail.category", v=" / ".join(cats)))
            if mod.manifest.compatible_versions:
                meta_parts.append(_("detail.compat", v=", ".join(mod.manifest.compatible_versions)))
        elif hint.get("version") and hint["version"] not in ("—", _("detail.ver_notag")):
            meta_parts.append(_("detail.compat", v=hint["version"]))
        src = (mod.package_type if mod else "") or hint.get("source") or ""
        if src: meta_parts.append(_("detail.source", v=src))
        if mod and mod.file_size:
            meta_parts.append(_("detail.size", v=f"{mod.file_size/1024/1024:.1f}"))
        meta_parts.append(_("detail.id", v=(mod.mod_id if mod else pkg)))
        if mod is None:
            meta_parts.append(_("detail.missing_file"))
        self.lbl_meta.setText("    ·    ".join(meta_parts))

        # 预览图
        pix = None
        desc = ""
        if mod is not None:
            if mod.icon.is_available and mod.icon.raw_bytes:
                img = QImage.fromData(mod.icon.raw_bytes)
                if not img.isNull():
                    pix = QPixmap.fromImage(img)
            if not pix:
                # 尝试 .scs / .zip / 目录内打开（含子 .scs 嵌套兜底）
                candidates_rdr: List = []
                pp = Path(mod.package_path)
                pp_exists = pp.exists()
                if pp_exists:
                    try:
                        candidates_rdr.append(ScsArchiveReader(pp))
                        if pp.is_dir():
                            # workshop 嵌套：目录下第一层 *.scs 都试
                            try:
                                for sp in sorted(pp.iterdir()):
                                    if sp.is_file() and sp.suffix.lower() in (".scs", ".zip"):
                                        try: candidates_rdr.append(ScsArchiveReader(sp))
                                        except Exception: pass
                                    if len(candidates_rdr) >= 5: break
                            except OSError: pass
                    except Exception:
                        pass
                for rdr in candidates_rdr:
                    try:
                        for ic_name in ("mod_icon.jpg", "mod_icon.png", "icon.jpg", "icon.png", "preview.jpg"):
                            icon_bytes = rdr.read_bytes(ic_name)
                            if icon_bytes and len(icon_bytes) > 100:
                                img = QImage.fromData(QByteArray(icon_bytes))
                                if not img.isNull(): pix = QPixmap.fromImage(img); break
                        if not desc:
                            for dn in ("mod_description.txt", "description.txt", "readme.txt"):
                                db = rdr.read_bytes(dn)
                                if db: desc = _decode_text(db); break
                        try: rdr.close()
                        except Exception: pass
                        if pix and desc: break
                    except Exception:
                        try: rdr.close()
                        except Exception: pass
                # 额外兜底：Workshop 目录型常常把 manifest/icon/description 直接放在
                # universal/、数字版本目录(如 150/)等第一层子目录里，此时 ScsArchiveReader
                # 遍历第一层文件不会跨进普通子目录，所以这里直接遍历子目录读磁盘文件。
                if (not pix or not desc) and pp_exists and pp.is_dir():
                    import re as _re
                    try:
                        sub_dirs = [sd for sd in pp.iterdir() if sd.is_dir()]
                    except OSError:
                        sub_dirs = []
                    def _sd_key(d: Path):
                        n = d.name
                        if n.lower() == "universal": return (0, 0, n.lower())
                        m_ = _re.match(r"(\d+)", n)
                        if m_: return (1, -int(m_.group(1)), n.lower())
                        return (2, 0, n.lower())
                    sub_dirs_sorted = sorted(sub_dirs, key=_sd_key)[:20]
                    icon_names = ("mod_icon.jpg", "mod_icon.png", "icon.jpg", "icon.png", "preview.jpg")
                    desc_names = ("mod_description.txt", "description.txt", "mod_info.txt", "mod.txt")
                    for sd in sub_dirs_sorted:
                        if not pix:
                            try:
                                for ic in icon_names:
                                    icp = sd / ic
                                    if icp.is_file():
                                        try:
                                            data = icp.read_bytes()
                                        except OSError:
                                            continue
                                        if data and len(data) > 100:
                                            img2 = QImage.fromData(QByteArray(data))
                                            if not img2.isNull():
                                                pix = QPixmap.fromImage(img2)
                                                break
                            except Exception:
                                pass
                        if not desc:
                            try:
                                for dn in desc_names:
                                    dnp = sd / dn
                                    if dnp.is_file():
                                        try:
                                            txt = dnp.read_text(encoding="utf-8", errors="replace")
                                        except OSError:
                                            continue
                                        if txt:
                                            desc = txt
                                            break
                            except Exception:
                                pass
                        if pix and desc:
                            break
            if not desc and mod.description:
                desc = mod.description
            if not desc and mod.manifest.package_version:
                desc = _("ui.version_prefix", v=mod.manifest.package_version)
        if pix is None:
            self.preview.setText(_("ui.preview_no_icon"))
            self.preview.setStyleSheet("background:#1e1e1e; color:#aaa; border:1px solid #555; border-radius:6px;")
        else:
            scaled = pix.scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.preview.setPixmap(scaled)
            self.preview.setText("")
            self.preview.setStyleSheet("background: #111; border:1px solid #555; border-radius:6px;")
        self.txt_desc.setPlainText(desc or _("ui.desc_empty"))


# ---------------------------------------------------------------------------
#  异步加密包解析工作线程
# ---------------------------------------------------------------------------
class _QuickScanWorker(QObject):
    """QThread 工作线程：快速扫描所有模组（skip_manifest_parse=True），按文件逐个回调进度（避免 UI 阻塞）。"""

    progress_filename = Signal(str)        # 当前扫描的文件名（用于主进度条不定态阶段文案）
    result_ready = Signal(list, list)      # (mods_list, new_ids)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, scanner):
        super().__init__()
        self._scanner = scanner
        self._stop = False
        self._thread = None

    def start(self):
        from threading import Thread
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True

    def _run(self):
        try:
            from pathlib import Path
            scanner = self._scanner
            mi_index = scanner.load_mods_info_index()
            mods_index: dict = {}

            # --- 1) 本地 mod 目录 ---
            local_items = []
            if scanner.local_mod_dir and scanner.local_mod_dir.exists():
                try:
                    local_items = list(scanner.local_mod_dir.iterdir())
                except OSError:
                    local_items = []
            for item in local_items:
                if self._stop:
                    break
                try:
                    self.progress_filename.emit(item.name)
                except Exception:
                    pass
                try:
                    mod = scanner._classify_and_build(item, mi_index, True)  # skip_parse=True
                except Exception:
                    continue
                if mod is not None:
                    mods_index[mod.mod_id] = mod

            # --- 2) Workshop 目录：每个子目录是一个订阅模组 ---
            ws_items = []
            if scanner.workshop_dir and scanner.workshop_dir.exists():
                try:
                    ws_items = [p for p in scanner.workshop_dir.iterdir() if p.is_dir()]
                except OSError:
                    ws_items = []
            for item in ws_items:
                if self._stop:
                    break
                try:
                    self.progress_filename.emit(str(item.name))
                except Exception:
                    pass
                try:
                    from core.mod_scanner import _build_mod_minimal
                    mod = _build_mod_minimal(item, "workshop", mi_index)
                except Exception:
                    continue
                if mod is None:
                    continue
                if mod.mod_id not in mods_index:
                    mods_index[mod.mod_id] = mod
                else:
                    new_id = mod.mod_id + "_workshop"
                    mod.mod_id = new_id
                    mods_index[new_id] = mod

            mods_list = list(mods_index.values())

            # --- 3) 分类标签回填 + 新模组检测（这步也逐包emit阶段名称） ---
            try:
                from services import category_service as _cs
                name_hints = {}
                for m in mods_list:
                    if self._stop:
                        break
                    try:
                        self.progress_filename.emit(_build_progress_detail_zh("分类", m.display_title))
                    except Exception:
                        pass
                    name_hints[m.mod_id] = m.display_title
                if not self._stop:
                    for m in mods_list:
                        cat = _cs.get_category(m.mod_id)
                        if cat:
                            m._category_tag = cat
                    _cs.touch_and_detect_new([m.mod_id for m in mods_list], name_hints=name_hints)
                    _cs.save()
            except Exception:
                pass
            try:
                from services.session_service import get_new_mod_ids_vs_last_session
                new_ids = get_new_mod_ids_vs_last_session([m.mod_id for m in mods_list])
            except Exception:
                new_ids = []

            if self._stop:
                # 取消即认为失败，不发送结果
                self.failed.emit(_("ui.sb_scan_cancelled"))
            else:
                self.result_ready.emit(mods_list, list(new_ids))
        except Exception as e:
            import traceback
            self.failed.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
        finally:
            try:
                self.finished.emit()
            except Exception:
                pass


def _build_progress_detail_zh(stage: str, name: str) -> str:
    n = (name or "").strip()
    if len(n) > 48:
        n = n[:48] + "..."
    return f"{stage}: {n}" if n else stage


class _AsyncParseWorker(QObject):
    """QThread 工作线程：逐个解析加密包的 manifest/icon/description"""

    progress = Signal(int, int, str)   # (current_idx, total, mod_id)
    one_parsed = Signal(str)          # (mod_id)
    finished = Signal()

    def __init__(self, pending_mods: list, paths):
        super().__init__()
        self._pending = pending_mods
        self._paths = paths
        self._stop = False
        self._thread: Optional[object] = None

    def start(self):
        from threading import Thread
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True

    def _run(self):
        from core.mod_scanner import _build_mod_from_package
        from core.sii_parser import parse_mods_info
        from pathlib import Path as _P
        # 加载 mods_info_index（解析时回填名字）
        mi_index = {}
        try:
            if self._paths.mods_info_path and self._paths.mods_info_path.exists():
                mi_index = parse_mods_info(str(self._paths.mods_info_path))
        except Exception:
            pass
        total = len(self._pending)
        for i, m in enumerate(self._pending):
            if self._stop:
                break
            # 逐个解析（从磁盘缓存走，避免重复解包）
            try:
                pp = _P(m.package_path)
                if pp.is_dir():
                    ptype = "workshop" if pp.parent.name == "227300" or (pp.parent and "workshop" in str(pp.parent.parent).lower()) else "directory"
                    # 更可靠：原 package_type 在 Mod 对象上已有
                    ptype = m.package_type or "directory"
                elif pp.suffix.lower() == ".zip":
                    ptype = "zip"
                else:
                    ptype = "scs"
                parsed = _build_mod_from_package(pp, ptype, mi_index)
                # 回填到原 Mod 对象（只填空字段，保留原 mod_id 等不变）
                if parsed.manifest.display_name and not m.manifest.display_name:
                    m.manifest.display_name = parsed.manifest.display_name
                if parsed.manifest.package_name and not m.manifest.package_name:
                    m.manifest.package_name = parsed.manifest.package_name
                if parsed.manifest.package_version and not m.manifest.package_version:
                    m.manifest.package_version = parsed.manifest.package_version
                if parsed.manifest.author and not m.manifest.author:
                    m.manifest.author = parsed.manifest.author
                if parsed.manifest.categories and not m.manifest.categories:
                    m.manifest.categories = list(parsed.manifest.categories)
                if parsed.manifest.icon_filename and not m.manifest.icon_filename:
                    m.manifest.icon_filename = parsed.manifest.icon_filename
                if parsed.manifest.description_filename and not m.manifest.description_filename:
                    m.manifest.description_filename = parsed.manifest.description_filename
                if parsed.manifest.compatible_versions and not m.manifest.compatible_versions:
                    m.manifest.compatible_versions = list(parsed.manifest.compatible_versions)
                if parsed.icon.is_available and not m.icon.is_available:
                    m.icon = parsed.icon
                if parsed.description and not m.description:
                    m.description = parsed.description
                self.one_parsed.emit(m.mod_id)
            except Exception:
                pass
            self.progress.emit(i + 1, total, m.mod_id)
        self.finished.emit()


def _decode_text(b: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "gbk", "latin-1"):
        try: return b.decode(enc)
        except UnicodeDecodeError: pass
    return b.decode("utf-8", errors="replace")


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    # App 级图标
    icon_path = Path(__file__).resolve().parent.parent / "assets" / "app_icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()