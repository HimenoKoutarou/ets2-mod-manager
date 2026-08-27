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
import json
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
    QInputDialog,
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
from ui.l10n_dialog import L10nDialog
from services.l10n_service import L10nService
from ui.save_editor_dialog import SaveEditorDialog



# ---------------------------------------------------------------------------
#  启动加载屏（SplashScreen）——扫描期间显示，禁止主窗口交互
# 拆分出来的辅助 Widget / Worker（单文件 3785 行 → ~3060 行，降低 IDE 诊断压力）
from ._mw_widgets import _LangSwitchDialog, SplashScreen, ModTable
from ._mw_workers import _QuickScanWorker, _AsyncParseWorker, _WorkshopFetchWorker, _EnrichProfilesWorker
from ._mw_mixins import _SignalMixin, _TableDataMixin, _ToolbarMixin, _DialogMixin

# ============================================================
# Toolbar 样式常量（避免 4 份重复 CSS 字符串；改主题时只需改这里）
# ============================================================
_QTB_STYLE_DEFAULT = "QToolButton{padding:4px 10px;border:1px solid #d0d7de;border-radius:4px;background:#fff;}QToolButton:hover{background:#f3f4f6;}QToolButton::menu-indicator{width:0px;}"
_QTB_STYLE_PRIMARY = "QToolButton{padding:4px 12px;border:1px solid #1a7f37;border-radius:4px;background:#2da44e;color:#fff;font-weight:700;}QToolButton:hover{background:#2c974b;}QToolButton::menu-indicator{image:none;width:4px;}"

class MainWindow(QMainWindow, _SignalMixin, _TableDataMixin, _ToolbarMixin, _DialogMixin):
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

        self._refresh_debounce_timer = QTimer(self)
        self._refresh_debounce_timer.setSingleShot(True)
        self._refresh_debounce_timer.setInterval(30)
        self._refresh_debounce_timer.timeout.connect(self._do_deferred_refresh)
        self._need_refresh_order = False
        self._need_refresh_filter = False
        self._need_refresh_counts = False
        self._need_refresh_status = False

        self._build_ui()
        self._build_toolbar()
        # 汉化服务
        self._l10n_service = L10nService(Path("config"))
        ufl_path = Path("assets/bin/himeno_sena.ufl.scs")
        if ufl_path.exists():
            self._l10n_service.set_ufl_mod(ufl_path)
        self._build_menubar()
        self.setStatusBar(QStatusBar(self))
        self.statusBar().showMessage(_("ui.sb_ets2_doc_dir", dir=str(self.paths.documents_dir)))

        # 启动后扫描
        QTimer.singleShot(50, self._bootstrap)

        # 语言切换通知 → 刷新 UI（已在 _do_switch_language 中处理，保留用于外部触发）
        try:
            I18nNotifier.instance().languageChanged.connect(self._on_language_changed)
        except Exception:
            pass
        self._lang_switching = False  # 防止重入标志




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
