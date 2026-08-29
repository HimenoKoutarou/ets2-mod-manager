# -*- coding: utf-8 -*-
"""全局 QSS 主题系统 — 支持浅色 / 深色 / 跟随系统三种模式。

色板设计：
  深色 = Catppuccin Mocha（#1e1e2e 系）
  浅色 = GitHub Light（#ffffff 系）
  强调色统一蓝紫渐变

使用：
  from ui.theme import ThemeManager, THEME_DARK, THEME_LIGHT, THEME_AUTO
  ThemeManager.apply(app, THEME_AUTO)  # 跟随系统
  ThemeManager.apply(app, THEME_DARK)  # 强制深色
"""

import ctypes
import json
from pathlib import Path
from PySide6.QtCore import QObject, Signal

# ============================================================
# 主题模式常量
# ============================================================
THEME_DARK  = "dark"
THEME_LIGHT = "light"
THEME_AUTO  = "auto"

_ALL_MODES = [THEME_DARK, THEME_LIGHT, THEME_AUTO]

# ============================================================
# 深色色板 (Catppuccin Mocha)
# ============================================================
_D = {
    "bg":        "#151a1f",
    "surface":   "#202830",
    "inset":     "#11161b",
    "border":    "#34404b",
    "border_hi": "#52616e",
    "text":      "#e6edf3",
    "text_dim":  "#a7b4bf",
    "text_mute": "#71808d",
    "accent":    "#4fd1c5",
    "accent_hi": "#81e6d9",
    "green":     "#8bd5a5",
    "green_dk":  "#21897e",
    "green_bd":  "#1b6f67",
    "green_hv":  "#2aa198",
    "red":       "#f38b8b",
    "yellow":    "#f0c674",
    "orange":    "#e5a66b",
    "sel_bg":    "rgba(79, 209, 197, 0.14)",
    "sel_bg_hi": "rgba(79, 209, 197, 0.22)",
    "sel_bg_hv": "rgba(79, 209, 197, 0.08)",
}

# ============================================================
# 浅色色板 (GitHub Light)
# ============================================================
_L = {
    "bg":        "#f5f7fb",
    "surface":   "#ffffff",
    "inset":     "#eef2f7",
    "border":    "#e2e7ef",
    "border_hi": "#c6d0dc",
    "text":      "#1f2328",
    "text_dim":  "#57606a",
    "text_mute": "#8c959f",
    "accent":    "#2f80ed",
    "accent_hi": "#56a3ff",
    "green":     "#1f9d74",
    "green_dk":  "#2f80ed",
    "green_bd":  "#2468c7",
    "green_hv":  "#256dcc",
    "red":       "#cf222e",
    "yellow":    "#d4a72c",
    "orange":    "#bc4c00",
    "sel_bg":    "rgba(47, 128, 237, 0.08)",
    "sel_bg_hi": "rgba(47, 128, 237, 0.14)",
    "sel_bg_hv": "rgba(47, 128, 237, 0.05)",
}


def _build_qss(c: dict) -> str:
    """从色板字典生成完整 QSS 字符串。"""
    return f"""
/* ===== 全局 ===== */
QWidget {{
    background-color: {c["bg"]};
    color: {c["text"]};
    font-family: "Microsoft YaHei", "Segoe UI", "SF Pro Display", sans-serif;
    font-size: 13px;
}}
QMainWindow {{ background-color: {c["bg"]}; }}
QFrame#sidebarPanel {{
    background-color: {c["surface"]};
    border: 1px solid {c["border"]};
    border-radius: 8px;
}}
QFrame#detailPanel {{
    background-color: {c["surface"]};
    border: 1px solid {c["border"]};
    border-radius: 8px;
}}
QFrame#scanProgressPanel {{
    background-color: {c["surface"]};
    border: 1px solid {c["border"]};
    border-left: 3px solid {c["accent"]};
    border-radius: 4px;
}}
QLabel#sectionLabel {{
    color: {c["accent"]};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0px;
}}

/* ===== 按钮 ===== */
QPushButton {{
    background-color: {c["surface"]};
    color: {c["text"]};
    border: 1px solid {c["border"]};
    border-radius: 9px;
    padding: 7px 14px;
    font-weight: 500;
}}
QPushButton:hover {{
    background-color: {c["inset"]};
    border-color: {c["border_hi"]};
}}
QPushButton:pressed {{
    background-color: {c["inset"]};
}}
QPushButton:disabled {{
    color: {c["text_mute"]};
    background-color: {c["surface"]};
    border-color: {c["border"]};
}}

/* ===== 主按钮 ===== */
QPushButton#primaryButton,
QToolButton#primaryButton {{
    background-color: {c["green_dk"]};
    color: #ffffff;
    border: 1px solid {c["green_bd"]};
    border-radius: 6px;
    font-weight: 700;
    padding: 6px 16px;
}}
QPushButton#primaryButton:hover,
QToolButton#primaryButton:hover {{
    background-color: {c["green_hv"]};
}}

/* ===== 危险按钮 ===== */
QPushButton#dangerButton {{
    background-color: transparent;
    color: {c["red"]};
    border: 1px solid {c["red"]};
    border-radius: 6px;
    font-weight: 600;
}}
QPushButton#dangerButton:hover {{
    background-color: {c["red"]};
    color: #fff;
}}

/* ===== 工具按钮 ===== */
QToolButton {{
    background-color: transparent;
    color: {c["text"]};
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 13px;
}}
QToolButton:hover {{
    background-color: {c["surface"]};
    border: 1px solid {c["border"]};
}}
QToolButton:pressed {{
    background-color: {c["inset"]};
}}
QToolButton::menu-indicator {{
    image: none;
    width: 0px;
}}

/* ===== 工具栏 ===== */
QToolBar {{
    background-color: {c["inset"]};
    border: none;
    border-bottom: 1px solid {c["border"]};
    spacing: 4px;
    padding: 6px 8px;
}}
QToolBar::separator {{
    background-color: {c["border"]};
    width: 1px;
    height: 20px;
    margin: 0 4px;
}}

/* ===== 输入框 ===== */
QLineEdit {{
    background-color: {c["inset"]};
    color: {c["text"]};
    border: 1px solid {c["border"]};
    border-radius: 10px;
    padding: 8px 12px;
    selection-background-color: {c["accent"]};
}}
QLineEdit:focus {{
    border-color: {c["accent"]};
}}
QLineEdit::placeholder {{
    color: {c["text_mute"]};
}}

/* ===== 下拉框 ===== */
QComboBox {{
    background-color: {c["inset"]};
    color: {c["text"]};
    border: 1px solid {c["border"]};
    border-radius: 9px;
    padding: 6px 10px;
    min-height: 20px;
}}
QComboBox:hover {{
    border-color: {c["border_hi"]};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background-color: {c["surface"]};
    color: {c["text"]};
    border: 1px solid {c["border"]};
    border-radius: 6px;
    selection-background-color: {c["accent"]};
    selection-color: {c["bg"]};
    outline: none;
    padding: 4px;
}}

/* ===== 表格 ===== */
QTableWidget {{
    background-color: {c["bg"]};
    color: {c["text"]};
    border: 1px solid {c["border"]};
    border-radius: 10px;
    gridline-color: {c["inset"]};
    selection-background-color: {c["sel_bg"]};
    selection-color: {c["text"]};
    alternate-background-color: {c["inset"]};
    outline: none;
    font-size: 13px;
    alternate-background-color: {c["surface"]};
}}
QTableWidget::item {{
    padding: 6px 8px;
    border: none;
}}
QTableWidget::item:selected {{
    background-color: {c["sel_bg_hi"]};
}}
QHeaderView::section {{
    background-color: {c["surface"]};
    color: {c["text_dim"]};
    border: none;
    border-right: 1px solid {c["border"]};
    border-bottom: 1px solid {c["border"]};
    padding: 6px 8px;
    font-weight: 600;
    font-size: 12px;
}}
QTableCornerButton::section {{
    background-color: {c["surface"]};
    border: none;
}}

/* ===== 树形控件 ===== */
QTreeWidget {{
    background-color: {c["bg"]};
    color: {c["text"]};
    border: 1px solid {c["border"]};
    border-radius: 10px;
    outline: none;
    padding: 6px;
}}
QTreeWidget::item {{
    padding: 5px 4px;
    border: none;
}}
QTreeWidget::item:selected {{
    background-color: {c["sel_bg_hi"]};
    color: {c["text"]};
}}
QTreeWidget::item:hover {{
    background-color: {c["sel_bg_hv"]};
}}

/* === GroupBox === */
QGroupBox {{
    background-color: transparent;
    border: none;
    margin-top: 4px;
    padding-top: 2px;
    font-weight: 600;
    color: {c["text_dim"]};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 0;
    padding: 0;
    background-color: transparent;
    color: {c["accent"]};
    font-size: 12px;
}}

/* ===== 标签 ===== */
QLabel {{
    background-color: transparent;
    color: {c["text"]};
}}
QLabel#dimLabel {{
    color: {c["text_dim"]};
}}
QLabel#mutedLabel {{
    color: {c["text_mute"]};
    font-size: 11px;
}}
QLabel#titleLabel {{
    font-size: 14px;
    font-weight: bold;
    color: {c["text"]};
}}

/* ===== 进度条 ===== */
QProgressBar {{
    background-color: {c["inset"]};
    border: 1px solid {c["border"]};
    border-radius: 4px;
    text-align: center;
    color: {c["text"]};
    height: 18px;
}}
QProgressBar::chunk {{
    background-color: {c["accent"]};
    border-radius: 2px;
}}

/* ===== 滚动条 ===== */
QScrollBar:vertical {{
    background: {c["inset"]};
    width: 10px;
    border: none;
    border-radius: 5px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {c["border"]};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {c["border_hi"]};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
}}
QScrollBar:horizontal {{
    background: {c["inset"]};
    height: 10px;
    border: none;
    border-radius: 5px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {c["border"]};
    border-radius: 4px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {c["border_hi"]};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* ===== 文本编辑区 ===== */
QPlainTextEdit, QTextEdit, QTextBrowser {{
    background-color: {c["inset"]};
    color: {c["text"]};
    border: 1px solid {c["border"]};
    border-radius: 6px;
    padding: 6px;
    selection-background-color: {c["accent"]};
    selection-color: {c["bg"]};
}}

/* ===== Tab 控件 ===== */
QTabWidget::pane {{
    border: 1px solid {c["border"]};
    border-radius: 4px;
    top: -1px;
    background-color: {c["bg"]};
}}
QTabBar::tab {{
    background-color: {c["inset"]};
    color: {c["text_dim"]};
    border: 1px solid {c["border"]};
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    padding: 7px 16px;
    margin-right: 2px;
    font-weight: 500;
}}
QTabBar::tab:selected {{
    background-color: {c["bg"]};
    color: {c["accent"]};
    border-color: {c["border"]};
    border-bottom: 1px solid {c["bg"]};
}}
QTabBar::tab:hover:!selected {{
    background-color: {c["surface"]};
    color: {c["text"]};
}}

/* ===== 菜单 ===== */
QMenu {{
    background-color: {c["surface"]};
    color: {c["text"]};
    border: 1px solid {c["border"]};
    border-radius: 4px;
    padding: 6px;
}}
QMenu::item {{
    padding: 6px 24px 6px 16px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background-color: {c["accent"]};
    color: {c["bg"]};
}}
QMenu::separator {{
    height: 1px;
    background: {c["border"]};
    margin: 4px 8px;
}}

/* ===== 对话框 ===== */
QDialog {{ background-color: {c["bg"]}; }}

/* ===== Frame ===== */
QFrame[frameShape="6"] {{
    background-color: {c["surface"]};
    border: 1px solid {c["border"]};
    border-radius: 12px;
}}

/* ===== 复选框 ===== */
QCheckBox {{
    spacing: 6px;
    background: transparent;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid {c["border_hi"]};
    background: {c["inset"]};
}}
QCheckBox::indicator:hover {{
    border-color: {c["accent"]};
}}
QCheckBox::indicator:checked {{
    background: {c["accent"]};
    border-color: {c["accent"]};
    image: none;
}}

/* ===== 状态栏 ===== */
QStatusBar {{
    background-color: {c["inset"]};
    color: {c["text_dim"]};
    border-top: 1px solid {c["border"]};
    font-size: 12px;
}}

/* ===== Splitter ===== */
QSplitter::handle {{
    background-color: {c["border"]};
}}
QSplitter::handle:horizontal {{ width: 2px; }}
QSplitter::handle:vertical {{ height: 2px; }}

/* ===== SpinBox ===== */
QSpinBox {{
    background-color: {c["inset"]};
    color: {c["text"]};
    border: 1px solid {c["border"]};
    border-radius: 6px;
    padding: 4px 6px;
}}
QSpinBox:focus {{ border-color: {c["accent"]}; }}

/* ===== ToolTip ===== */
QToolTip {{
    background-color: {c["surface"]};
    color: {c["text"]};
    border: 1px solid {c["border"]};
    border-radius: 3px;
    padding: 4px 8px;
}}
"""


# 预生成两套 QSS
DARK_THEME  = _build_qss(_D)
LIGHT_THEME = _build_qss(_L)

# 兼容旧引用
QTB_DEFAULT = ""
QTB_PRIMARY = ""


# ============================================================
# 系统暗色检测
# ============================================================
def is_system_dark() -> bool:
    """检测 Windows 系统是否处于暗色模式。

    读注册表 HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\
    Themes\\Personalize\\AppsUseLightTheme
    0 = 暗色, 1 = 浅色
    """
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return value == 0
    except Exception:
        return False  # 默认浅色


def get_effective_theme(mode: str) -> str:
    """把 auto 转成实际的 dark/light。"""
    if mode == THEME_AUTO:
        return THEME_DARK if is_system_dark() else THEME_LIGHT
    return mode


def get_theme_qss(mode: str) -> str:
    """根据模式返回对应 QSS 字符串。"""
    effective = get_effective_theme(mode)
    if effective == THEME_DARK:
        return DARK_THEME
    return LIGHT_THEME


# ============================================================
# 主题管理器 — 持久化 + 信号通知
# ============================================================
_CONFIG_PATH = Path("config") / "theme.json"


class ThemeManager(QObject):
    """主题管理器：持久化用户选择 + apply 到 QApplication + 信号通知。"""

    themeChanged = Signal(str)  # 参数 = 实际生效的 dark/light

    _instance = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode = THEME_AUTO
        self._load()

    @classmethod
    def instance(cls) -> "ThemeManager":
        if cls._instance is None:
            cls._instance = ThemeManager()
        return cls._instance

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def effective_theme(self) -> str:
        return get_effective_theme(self._mode)

    def apply(self, app, mode: str = None):
        """应用主题到 QApplication。mode=None 时用当前保存的 mode。

        Bug B 修复：setStyleSheet 前强制给 app 一个合法 pointSize 的默认字体。
        Windows 上部分配置（含高 DPI、字体缩放）下 QApplication.font().pointSize() == -1
        （走 pixelSize 模式），后续对 QTableWidgetItem 调用 setFont() 会触发
        QFont::setPointSize(-1) 警告；在极快连续重排（如 missing mod toggle 的
        递归场景）期间会导致界面死锁。"""
        if mode is not None:
            self._mode = mode
            self._save()
        qss = get_theme_qss(self._mode)
        if app is not None:
            try:
                f = app.font()
                ps = f.pointSize()
                px = f.pixelSize()
                if ps <= 0:
                    # pointSize<=0 表示当前用 pixelSize 驱动；如果 pixelSize 也没给，就兜底
                    if px > 0:
                        est = max(9, round(px * 3.0 / 4))
                        f.setPointSize(est)
                    else:
                        f.setPointSize(10)
                # 最终 clamp，保证没有任何路径能带着 <=0 的 pointSize 赋值给 app
                if f.pointSize() <= 0:
                    f.setPointSize(10)
                app.setFont(f)
            except Exception:
                pass
            app.setStyleSheet(qss)
        else:
            app.setStyleSheet(qss)
        self.themeChanged.emit(self.effective_theme)

    def _load(self):
        try:
            if _CONFIG_PATH.exists():
                data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
                m = data.get("mode", THEME_AUTO)
                if m in _ALL_MODES:
                    self._mode = m
        except Exception:
            pass

    def _save(self):
        try:
            _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            _CONFIG_PATH.write_text(
                json.dumps({"mode": self._mode}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass
