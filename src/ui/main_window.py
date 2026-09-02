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

from PySide6.QtCore import Qt, QSize, QMimeData, QByteArray, Signal, QTimer, QObject, QThread
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



def resolve_asset(rel: str) -> Path:
    r"""Unified assets/ resolver covering dev tree + PyInstaller onedir (_MEIPASS)
    + onefile.

    Dev  tree  : F:\\ETS2ModManager\\src\\ui\\main_window.py
                   parents[2] = F:\\ETS2ModManager  -> assets\\logo.png OK
    onedir col : ETS2ModManager.exe + assets/ NEXT TO EXE
                   sys.executable.parent + assets\\*.png OK
    onefile    : sys._MEIPASS/assets (PyInstaller exploded tree)
                   Path(_MEIPASS) + assets\\*.png OK
    Always returns absolute Path; caller must .exists() before opening.
    """
    import sys as _sys
    relp = Path(rel)
    # 1) onefile mode _MEIPASS
    meipass = getattr(_sys, "_MEIPASS", None)
    if meipass:
        c = Path(meipass) / relp
        if c.exists():
            return c.resolve()
    # 2) onedir / normal install: exe sibling
    frozen = getattr(_sys, "frozen", False)
    if frozen:
        c = Path(_sys.executable).resolve().parent / relp
        if c.exists():
            return c.resolve()
    # 3) dev tree: src/ui/x.py -> PROJECT_ROOT (parents[2])
    try:
        project_root = Path(__file__).resolve().parents[2]
    except Exception:
        project_root = Path.cwd()
    return (project_root / relp).resolve()


def _write_startup_error(message: str) -> None:
    """Persist bootstrap failures so windowed builds never fail silently."""
    import traceback as _traceback
    candidates = []
    try:
        base = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
        candidates.append(base / "logs" / "startup.log")
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(Path(local_app_data) / "ETS2ModManager" / "startup.log")
        candidates.append(Path(os.environ.get("TEMP", ".")) / "ETS2ModManager-startup.log")
    except Exception:
        return
    for path in candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(message.rstrip() + "\n")
            return
        except OSError:
            continue


# ---------------------------------------------------------------------------
#  启动加载屏（SplashScreen）——扫描期间显示，禁止主窗口交互
# 拆分出来的辅助 Widget / Worker（单文件 3785 行 → ~3060 行，降低 IDE 诊断压力）
from ._mw_widgets import _LangSwitchDialog, SplashScreen, ModTable
from ._mw_workers import _QuickScanWorker, _AsyncParseWorker, _WorkshopFetchWorker, _EnrichProfilesWorker, _ProfileReadWorker
from ._mw_mixins import _SignalMixin, _TableDataMixin, _ToolbarMixin, _DialogMixin
from .theme import ThemeManager, THEME_DARK, THEME_LIGHT, THEME_AUTO, QTB_DEFAULT, QTB_PRIMARY

# 全局深色主题从 theme.py 加载，旧的 _QTB_STYLE 常量已迁移

class MainWindow(QMainWindow, _SignalMixin, _TableDataMixin, _ToolbarMixin, _DialogMixin):
    def eventFilter(self, watched, event):
        # QMainWindow 位于 MRO 前方，显式转发给信号混入类的分类拖放处理。
        return _SignalMixin.eventFilter(self, watched, event)

    def __init__(self):
        super().__init__()
        self._base_window_title = f"{_('app.title')}  v{__version__}"
        self.setWindowTitle(self._base_window_title)
        self.resize(1280, 780)
        # 设置窗口图标 + 启动 splash 备用 logo 路径（统一走 resolve_asset 兼容 dev/frozen/onefile）
        icon_path = resolve_asset("assets/app_icon.png")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        logo_path = resolve_asset("assets/logo.png")
        if not logo_path.exists():
            logo_path = resolve_asset("assets/app_icon.png")
        self._logo_path = str(logo_path)
        self._splash: Optional["SplashScreen"] = None
        self._bootstrap_started = False

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
        # Startup/shutdown uses QWidget.isEnabled(); profile editability is a
        # separate state so Steam/Cloud profiles can still be browsed.
        self._profile_editable = False
        self._worklist_profile_key = None
        self.profiles: List[ProfileInfo] = []
        self._current_filter_cat: str | None = None
        self._profile_fill_pending: bool = False
        # P2 async priority dirty state (memory-only changes until Save)
        self._dirty_priority: bool = False
        self._baseline_worklist_hash: int = 0
        self._all_mods_by_id: dict[str, object] = {}
        self._profile_tree_items: dict[str, QTreeWidgetItem] = {}
        self._cat_items: dict[str, QTreeWidgetItem] = {}
        # 异步扫描与加密包解析
        self._quick_scan_worker: Optional["_QuickScanWorker"] = None
        self._async_parse_worker: Optional["_AsyncParseWorker"] = None
        self._ws_fetch_worker: Optional["_WorkshopFetchWorker"] = None
        self._enrich_profiles_worker: Optional["_EnrichProfilesWorker"] = None
        self._profile_read_worker: Optional["_ProfileReadWorker"] = None
        self._profile_read_workers: list = []
        self._profile_switch_token = 0
        self._profile_worklist_cache: dict = {}
        self._deferred_close_requested = False
        self._async_parse_progress: Optional[QProgressBar] = None
        # 顶部主进度条（显眼位置，扫描时显示，空闲隐藏）
        self._scan_progress_bar: Optional[QProgressBar] = None
        self._scan_progress_label: Optional[QLabel] = None
        self._scan_progress_frame: Optional[QFrame] = None
        # 搜索 + 过滤器
        self._search_keyword: str = ""
        self._current_mod_tab: str = "all"   # "all" | "active"

        # 自动更新服务
        self._update_install_thread: Optional[QThread] = None
        self.update_svc = UpdateService()  # 代理自动从环境变量读取
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

        # 启动后扫描：默认仍用 single-shot 50ms（直接 run 源码、不经过 installer main() 时）。
        # 打包模式下 main() 已经显示 installer splash 并在 60ms 后手动调 _bootstrap：
        # _bootstrap 自己会检查 self._bootstrap_after_installer_splash 决定最终 show 时机。
        if not getattr(self, "_bootstrap_after_installer_splash", False):
            # Give the installer entry point time to attach its splash and set
            # bootstrap mode before this fallback timer can fire.
            QTimer.singleShot(150, self._bootstrap)

        # 语言切换通知 → 刷新 UI（已在 _do_switch_language 中处理，保留用于外部触发）
        try:
            I18nNotifier.instance().languageChanged.connect(self._on_language_changed)
        except Exception:
            pass
        self._lang_switching = False  # 防止重入标志






    # ---------- async-priority dirty state (P2) ----------
    def _mark_priority_dirty(self, status_msg: str | None = None) -> None:
        """Call after ANY memory-only priority/enable change that hasn't been persisted yet."""
        self._dirty_priority = True
        base = getattr(self, "_base_window_title", None)
        if base is None:
            base = self.windowTitle()[1:] if self.windowTitle().startswith("*") else self.windowTitle()
            self._base_window_title = base
        if not self.windowTitle().startswith("*"):
            self.setWindowTitle(f"*{base}")
        btn = getattr(self, "btn_save", None)
        if btn is not None:
            try:
                current = btn.styleSheet() or ""
                if "font-weight" not in current:
                    btn.setStyleSheet(current + "QToolButton,QPushButton{font-weight:700;}")
            except Exception:
                pass
        if status_msg:
            try: self.statusBar().showMessage(status_msg, 5000)
            except Exception: pass

    def _clear_priority_dirty(self) -> None:
        """Call after successful set_active_mods write."""
        self._dirty_priority = False
        base = getattr(self, "_base_window_title", None)
        if base is None:
            base = self.windowTitle()[1:] if self.windowTitle().startswith("*") else self.windowTitle()
            self._base_window_title = base
        self.setWindowTitle(base)
        btn = getattr(self, "btn_save", None)
        if btn is not None:
            try: btn.setStyleSheet("")
            except Exception: pass
        try:
            h = hash(tuple(
                (str(e.get("package_name") or ""), int(e.get("priority_index") or 0), bool(e.get("enabled")))
                for e in self.current_worklist
            ))
            self._baseline_worklist_hash = h
        except Exception:
            pass

    def _refresh_dirty_from_worklist(self) -> None:
        """Recompute dirty flag from worklist vs baseline hash."""
        try:
            h = hash(tuple(
                (str(e.get("package_name") or ""), int(e.get("priority_index") or 0), bool(e.get("enabled")))
                for e in self.current_worklist
            ))
        except Exception:
            h = -1
        baseline = getattr(self, "_baseline_worklist_hash", 0) or 0
        self._dirty_priority = (baseline != 0 and h != baseline)
        base = getattr(self, "_base_window_title", None) or (
            self.windowTitle()[1:] if self.windowTitle().startswith("*") else self.windowTitle()
        )
        self.setWindowTitle(f"*{base}" if self._dirty_priority else base)
        btn = getattr(self, "btn_save", None)
        if btn is not None:
            try:
                btn.setStyleSheet("" if not self._dirty_priority else "QToolButton,QPushButton{font-weight:700;}")
            except Exception:
                pass
    def closeEvent(self, event):
        # _SignalMixin owns the complete shutdown path (session snapshot,
        # signal disconnection and worker stop flags).  Calling it explicitly
        # is necessary because QMainWindow appears first in this class's MRO.
        return _SignalMixin.closeEvent(self, event)


def _relaunch_without_console():
    """Dev 模式下如果有控制台窗口，用 pythonw.exe 重启自身彻底消除终端。

    用户用 `py src/__main__.py` 启动时，PowerShell 窗口是 Python 父终端，
    代码内部无法隐藏。此函数检测到控制台后用 pythonw.exe 重启：pythonw 无
    控制台窗口，当前进程退出后 PowerShell 窗口自动关闭。打包后 (frozen)
    跳过——PyInstaller console=False 已经无控制台。
    """
    import os, sys, shutil, subprocess, ctypes
    if sys.platform != "win32":
        return
    if getattr(sys, "frozen", False):
        return
    # 检测当前进程是否拥有控制台窗口
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if not hwnd:
            return
    except Exception:
        return
    # 找 pythonw.exe
    pythonw = shutil.which("pythonw")
    if not pythonw:
        exe_dir = os.path.dirname(sys.executable)
        candidate = os.path.join(exe_dir, "pythonw.exe")
        if os.path.exists(candidate):
            pythonw = candidate
    if not pythonw:
        # 找不到 pythonw → fallback: FreeConsole 分离（窗口不一定消失但至少 stdout 不再串到终端）
        try:
            ctypes.windll.kernel32.FreeConsole()
        except Exception:
            pass
        return
    # 重启：pythonw + 原始 argv
    argv = [pythonw] + list(sys.argv)
    try:
        ctypes.windll.kernel32.FreeConsole()
        subprocess.Popen(argv, creationflags=0x08000000, cwd=os.getcwd())
        sys.exit(0)
    except SystemExit:
        raise
    except Exception:
        pass


def main():
    _relaunch_without_console()
    app = QApplication.instance() or QApplication(sys.argv)
    # The installer splash used to be the only visible top-level widget.
    # On some Windows/Qt combinations closing it made QApplication leave the
    # event loop before the deferred MainWindow.show() callback could run.
    # Keep quit-on-last-window-closed disabled only during this hand-off; it is
    # restored by _on_main_window_shown once the real window is on screen.
    app.setQuitOnLastWindowClosed(False)
    ThemeManager.instance().apply(app)
    # App 级图标 + splash logo （统一 resolve_asset：dev tree / onedir / onefile 全匹配）
    icon_path = resolve_asset("assets/app_icon.png")
    logo_path = resolve_asset("assets/logo.png")
    if not logo_path.exists():
        logo_path = resolve_asset("assets/app_icon.png")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # -----------------------------------------------------------------------
    # 安装式启动：先展示 Splash（居中 + 有焦点 + 像安装进度条那样真的会动），
    # 等 bootstrap 把所有阶段跑完再 MainWindow.show()。用户不会先看到一个灰禁用
    # 空壳主窗口 + 一张被动小浮层；而是直接面对 "正在初始化 / 正在扫描 / 已完成"
    # 的连续进度条。MainWindow 在后台建 UI 但保持 setVisible(False)。
    # -----------------------------------------------------------------------
    splash = SplashScreen(str(logo_path) if logo_path.exists() else "")
    splash.show_installer_splash(center_on_primary=True)

    w = MainWindow()
    # 将 splash 句柄交给 MainWindow._bootstrap / finalize 使用；
    # 如果 MainWindow 已经有 self._splash（_show_splash 会复用），那么先塞进去
    # 可以让 _show_splash 不重复 new 一份，而是复用我们 installer splash。
    w._splash = splash
    # 注意：此处 NOT w.show()。必须等 _finalize_bootstrap 调 splash done 再 show。
    w._bootstrap_after_installer_splash = True
    # Keep a real top-level window alive throughout startup.  It stays hidden
    # behind the splash and disabled until bootstrap completes, so this does
    # not expose a half-initialized UI to the user.
    w.setEnabled(False)
    w.show()
    # Reassert the splash after showing the disabled owner window.  Qt may
    # otherwise activate the main window briefly during construction.
    splash.raise_()
    splash.activateWindow()

    def _bootstrap_and_show():
        try:
            w._bootstrap()
        except Exception as exc:
            _write_startup_error(
                "[bootstrap failed]\n" + __import__("traceback").format_exc()
            )
            # Keep the application alive on startup failures so the user can
            # inspect the window/status bar instead of the process vanishing.
            try:
                splash.close_splash_early("启动过程出现未处理异常")
            except Exception:
                pass
            try:
                w.show()
                w.raise_()
                w.activateWindow()
                w.setEnabled(True)
                app.setQuitOnLastWindowClosed(True)
                w.statusBar().showMessage(f"初始化失败：{type(exc).__name__}: {exc}", 15000)
            except Exception:
                _write_startup_error(
                    "[bootstrap recovery failed]\n" + __import__("traceback").format_exc()
                )

    QTimer.singleShot(60, _bootstrap_and_show)
    return app.exec()


if __name__ == "__main__":
    main()
