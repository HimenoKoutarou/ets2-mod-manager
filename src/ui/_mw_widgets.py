"""main_window 辅助小部件：_LangSwitchDialog / SplashScreen / ModTable（自动从 main_window.py 抽出，行数降 ~560）。"""
from __future__ import annotations
from pathlib import Path
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QFrame, QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem, QToolBar, QLabel, QPlainTextEdit, QPushButton, QStatusBar, QFileDialog, QMessageBox, QHeaderView, QAbstractItemView, QProgressBar, QCheckBox, QComboBox, QGroupBox, QSizePolicy, QTreeWidget, QTreeWidgetItem, QDialogButtonBox, QDialog, QTextBrowser, QTextEdit, QMenu, QScrollArea, QGridLayout, QLineEdit, QSpinBox, QTabWidget, QToolButton, QInputDialog

import sys
from typing import List, Optional

from PySide6.QtCore import Qt, QSize, QMimeData, QByteArray, Signal, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPixmap, QDrag
from PySide6.QtWidgets import (
    QDialog, QComboBox, QPushButton, QVBoxLayout, QHBoxLayout, QLabel,
    QWidget, QProgressBar, QFrame,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView,
)

from services.i18n_service import _, tr
from core.models import Mod

class _LangSwitchDialog(QDialog):
    """语言切换期间的模态遮罩弹窗，阻止用户操作直到刷新完成。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setModal(True)
        self.setFixedSize(420, 220)
        outer = QVBoxLayout(self); outer.setContentsMargins(0,0,0,0)
        card = QFrame(); card.setObjectName("langSwitchCard")
        card.setStyleSheet("QFrame#langSwitchCard{background:#ffffff; border:1px solid #d0d7de; border-radius:12px;}")
        cv = QVBoxLayout(card); cv.setContentsMargins(28,24,28,24); cv.setSpacing(14)
        t = QLabel("🌐 " + _("ui.lang_switch_title"))
        f = QFont(); f.setPointSize(14); f.setBold(True); t.setFont(f)
        cv.addWidget(t)
        m = QLabel(_("ui.lang_switch_msg")); m.setWordWrap(True); m.setStyleSheet("color:#444")
        cv.addWidget(m)
        bar = QProgressBar(); bar.setRange(0,0); bar.setTextVisible(False); bar.setFixedHeight(6)
        bar.setStyleSheet("QProgressBar{background:#eef2f7;border:none;border-radius:3px;}QProgressBar::chunk{background:#2da44e;border-radius:3px;}")
        cv.addWidget(bar)
        tip = QLabel(_("ui.lang_switch_tip")); tip.setStyleSheet("color:#666; font-size:12px;")
        cv.addWidget(tip)
        outer.addWidget(card)
    def close_it(self):
        try:
            self.accept()
        except Exception:
            pass


# ---------------------------------------------------------------------------

class SplashScreen(QWidget):
    """专业启动加载屏：显示详细扫描进度和当前任务。"""

    STEPS = ["init", "paths", "quick_scan", "parse", "enrich", "done"]
    STEP_LABELS = {
        "init":     "初始化引擎",
        "paths":    "检测文档与配置",
        "quick_scan": "快速扫描模组",
        "parse":    "解析加密模组包",
        "enrich":   "填充存档信息",
        "done":     "准备进入主界面",
    }
    # 每个阶段的进度权重（合计 100）——像安装程序那样阶段推进有意义，不一直 busy.
    PHASE_WEIGHTS = {
        "init":       8,
        "paths":      7,
        "quick_scan": 35,
        "parse":      38,
        "enrich":     10,
        "done":       2,
    }

    def __init__(self, logo_path: str = "", parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFixedSize(560, 620)   # installer splash 稍微大一圈，容纳阶段提示更宽松
        self._first_scan = False
        self._current_step = 0
        self._progress_percent = 0
        self._cumulative_pct = 0            # installer 累计权重
        self._phases_started: "set[str]" = set()
        self._installer_mode = False         # True → 关 splash 前 MainWindow 仍隐藏
        # 读取性能档位：按逻辑线程数给出保守默认值，可在初始化期间切换。
        import os, json
        self._perf_levels = {"low": 1, "medium": max(1, min(4, (os.cpu_count() or 4) // 2)), "high": max(1, min(8, os.cpu_count() or 4))}
        self._perf_mode = "medium"
        try:
            cfg = Path("config/performance.json")
            if cfg.exists():
                val = json.loads(cfg.read_text(encoding="utf-8")).get("mode", "medium")
                if val in self._perf_levels: self._perf_mode = val
        except Exception: pass

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
        # Try logo_path -> fallback to assets/app_icon.png -> fallback emoji.
        logo_pix = QPixmap()
        candidate_paths = []
        if logo_path:
            candidate_paths.append(logo_path)
        try:
            # Resolve app_icon sibling via same resolver used by main_window
            import sys as _sys
            meipass = getattr(_sys, "_MEIPASS", None)
            if meipass:
                candidate_paths.append(str(Path(meipass) / "assets" / "app_icon.png"))
            if getattr(_sys, "frozen", False):
                candidate_paths.append(str(Path(_sys.executable).resolve().parent / "assets" / "app_icon.png"))
            candidate_paths.append(str(Path(__file__).resolve().parents[2] / "assets" / "app_icon.png"))
        except Exception:
            pass
        for p in candidate_paths:
            try:
                if p and Path(p).exists():
                    trial = QPixmap(p)
                    if not trial.isNull():
                        logo_pix = trial
                        break
            except Exception:
                continue
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

        perf_layout = QHBoxLayout()
        perf_layout.addWidget(QLabel("读取性能"))
        self._perf_combo = QComboBox()
        for key, label in (("low", "低性能"), ("medium", "中性能"), ("high", "高性能")):
            self._perf_combo.addItem(f"{label}（{self._perf_levels[key]}线程）", key)
        self._perf_combo.setCurrentIndex(("low", "medium", "high").index(self._perf_mode))
        self._perf_combo.currentIndexChanged.connect(self._on_perf_changed)
        perf_layout.addWidget(self._perf_combo, 1)
        layout.addLayout(perf_layout)

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
        self._log_text.setMinimumHeight(150)
        self._log_text.setMaximumHeight(180)
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

    def _on_perf_changed(self, index: int):
        self._perf_mode = self._perf_combo.itemData(index)
        try:
            cfg = Path("config/performance.json"); cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text(__import__("json").dumps({"mode": self._perf_mode}, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception: pass

    def worker_count(self) -> int:
        return int(self._perf_levels.get(self._perf_mode, self._perf_levels["medium"]))
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
        sb = self._log_text.verticalScrollBar()
        follow_tail = sb.value() >= sb.maximum() - 2
        self._log_text.append(html)
        from PySide6.QtGui import QTextCursor
        cursor = self._log_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        self._log_text.setTextCursor(cursor)
        # 自动滚动到底部显示最新日志
        if follow_tail:
            sb.setValue(sb.maximum())

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

    def show_installer_splash(self, center_on_primary: bool = True):
        """安装式启动：有焦点 + 在其他应用之上但仅 app-local（不跨进程 always-on-top
        那样去挡用户浏览器）。Splash 自己居中到主屏幕。

        设计选择：不使用 WindowStaysOnTopHint（跨进程置顶，会挡用户在启动时打开的
        资源管理器 / 浏览器）。改为：先 show → raise_ → activateWindow 一次。因为
        此时 MainWindow 还没 show，Splash 就是用户唯一点到的窗口，体验像安装程序。
        """
        self._installer_mode = True
        screen = QApplication.primaryScreen()
        if screen and center_on_primary:
            geo = screen.availableGeometry()
            x = geo.center().x() - self.width() // 2
            y = geo.center().y() - self.height() // 2
            self.move(x, y)
        # show → activate （首次启动有明确焦点，安装进度那种感觉）
        self.show()
        self.raise_()
        self.activateWindow()
        QApplication.processEvents()
        # Initial: mark init phase
        self.mark_phase_start("init", "读取语言/主题缓存…")

    def mark_phase_start(self, phase_key: str, first_detail: str = ""):
        """进入新阶段：把上一阶段剩余权重一次性填满；新阶段从 0 开始 busy/detailed。"""
        if phase_key in self._phases_started:
            return
        prev_steps = [s for s in self.STEPS if s not in self._phases_started and s != phase_key
                      and self.STEPS.index(s) < self.STEPS.index(phase_key)]
        for s in prev_steps:
            if s not in self._phases_started:
                self._cumulative_pct += int(self.PHASE_WEIGHTS.get(s, 0))
                self._phases_started.add(s)
        self._phases_started.add(phase_key)
        phase_label = self.STEP_LABELS.get(phase_key, phase_key)
        self._current_step = self.STEPS.index(phase_key) if phase_key in self.STEPS else 0
        self._step_label.setText(phase_label)
        self._step_counter.setText(f"阶段 {min(self._current_step + 1, len(self.STEPS))}/{len(self.STEPS)}")
        self._log(f"[{phase_label}] 开始…", "info")
        self._progress.setRange(0, 100)
        self._progress.setValue(min(self._cumulative_pct, 100))
        self._percent_label.setText(f"{min(self._cumulative_pct, 100)}%")
        if first_detail:
            self._detail_label.setText(first_detail[:60] + ("…" if len(first_detail) > 60 else ""))
        QApplication.processEvents()

    def mark_phase_complete(self, phase_key: str):
        """阶段完成：把 PHASE_WEIGHTS 全部累加进去（busy 阶段用这个比较稳）。"""
        if phase_key in self._phases_started:
            return
        self._phases_started.add(phase_key)
        weight = int(self.PHASE_WEIGHTS.get(phase_key, 0))
        self._cumulative_pct = min(self._cumulative_pct + weight, 100)
        self._progress.setRange(0, 100)
        self._progress.setValue(self._cumulative_pct)
        self._percent_label.setText(f"{self._cumulative_pct}%")
        name = self.STEP_LABELS.get(phase_key, phase_key)
        self._log(f"[{name}] 完成", "success")

    def set_phase_progress_ratio(self, phase_key: str, cur: int, total: int):
        """可量化阶段（parse/enrich）：按 cur/total 分数推进 phase 内部分量。"""
        if phase_key not in self._phases_started:
            self.mark_phase_start(phase_key)
        weight = int(self.PHASE_WEIGHTS.get(phase_key, 0))
        phase_fraction = cur / max(total, 1)
        # 算出在 phase_key 之前已经累计的权重总和（不含自己）
        prior_total = 0
        for s in self.STEPS:
            if s == phase_key: break
            prior_total += int(self.PHASE_WEIGHTS.get(s, 0))
        target = min(prior_total + int(weight * phase_fraction), 100)
        if target > self._cumulative_pct:
            self._cumulative_pct = target
            self._progress.setRange(0, 100)
            self._progress.setValue(target)
            self._percent_label.setText(f"{target}%")

    def close_installer_splash(self, then_show_main=None):
        """100% → log → 小延迟让用户看到完成 → close → （可选）show MainWindow。"""
        try:
            for s in self.STEPS:
                self.mark_phase_complete(s)
            self._progress.setRange(0, 100)
            self._progress.setValue(100)
            self._percent_label.setText("100%")
            self._log("初始化完成，进入主界面…", "success")
            self._detail_label.setText("初始化完成 · 正在进入主界面")
        except Exception:
            pass
        QApplication.processEvents()
        def _finish():
            try:
                if then_show_main is not None:
                    try:
                        then_show_main.show()
                        then_show_main.raise_()
                        then_show_main.activateWindow()
                        QApplication.processEvents()
                    except Exception: pass
                self.close()
                self.deleteLater()
                # 通知 MainWindow: 主窗口已经真正 show + activate 了，
                # 可以开始排延后的更新日志/新模组弹窗
                cb = getattr(then_show_main, "_on_main_window_shown", None) if then_show_main else None
                if cb:
                    try: cb()
                    except Exception: pass
            except Exception:
                pass
        QTimer.singleShot(350, _finish)

    def close_splash_early(self, reason: str = ""):
        if reason:
            self._log(reason, "error")
        try:
            self._detail_label.setText(reason or "已中止")
        except Exception: pass
        QApplication.processEvents()
        QTimer.singleShot(1200, lambda: (self.close(), self.deleteLater() if False else None))

    
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
        # 启用区与禁用区是两个独立排序空间，禁止跨区拖动，
        # 否则 Qt 先移动视觉行、随后同步逻辑又会把它弹回原区。
        selected = self.selectedRows()
        if selected:
            selected_state = {self._row_enabled(i.row()) for i in selected}
            target_row = self.indexAt(event.position().toPoint()).row()
            if target_row < 0:
                target_row = self.rowCount() - 1
            if len(selected_state) > 1 or (0 <= target_row < self.rowCount() and self._row_enabled(target_row) not in selected_state):
                event.ignore()
                return
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
        # 表格统一使用正常字重，不再加粗（bold 参数保留只为兼容旧调用）
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
        name_item = self._mk(name, color="#000000" if en else "#8a8a8a")
        self.setItem(r, COL_NAME, name_item)
        # source（友好中文标签）
        src_map = {
            "scs": "本地SCS",
            "zip": "本地ZIP",
            "directory": "本地目录",
            "workshop": "创意工坊",
        }
        src_tip = ""
        if mod:
            src_tip = f"{mod.package_type} · {mod.package_path}"
        if mod:
            raw_type = mod.package_type or ""
            src_text = src_map.get(raw_type, raw_type or "—")
        else:
            src_text = "—"
        self.setItem(r, COL_SOURCE, self._mk(src_text))
        if src_tip:
            self.item(r, COL_SOURCE).setToolTip(src_tip)
        # size（sz==0 显示 0 MB，不要 —）
        sz = mod.file_size if mod else 0
        if sz >= 1024:
            size_txt = f"{sz/1024/1024:.1f} MB"
        else:
            size_txt = "0 MB" if sz == 0 else "< 0.1 MB"
        self.setItem(r, COL_SIZE, self._mk(size_txt, align=Qt.AlignRight | Qt.AlignVCenter))
        # version（适配版本，不再是package_version）
        vtxt = mod.display_compatible_version if mod else "—"
        self.setItem(r, COL_VERSION, self._mk(vtxt))
        # order
        order = work_entry.get("order", -1)
        self.setItem(r, COL_ORDER, self._mk(str(order) if order >= 0 else "—", align=Qt.AlignCenter))
        # pkg (hidden)
        self.setItem(r, COL_PKG, self._mk(work_entry["package_name"]))
        # 整行允许拖动；此前只有复选框设置了 ItemIsDragEnabled，
        # 从名称/来源列开始拖动时 Qt 可能不会发出稳定的 dropEvent。
        for c in range(self.columnCount()):
            cell = self.item(r, c)
            if cell is not None:
                cell.setFlags(cell.flags() | Qt.ItemIsDragEnabled)
        # name column 缺失 mod 标红
        if work_entry.get("_missing_mod"):
            name_item = self.item(r, COL_NAME)
            if name_item is not None:
                name_item.setForeground(QBrush(QColor("#ef4444")))
                tip = name_item.toolTip() or ""
                pkg = work_entry.get("package_name", "")
                extra = f"⚠️ Mod 文件缺失：{pkg}（存档里已启用但本地找不到该 mod 包）"
                if extra not in tip:
                    name_item.setToolTip(("" if not tip else tip + "\n") + extra)

    def update_row_for_mod(self, row: int, mod: Mod) -> None:
        """更新指定行的 name/version/source 列（解析完成后刷新）"""
        en = self._row_enabled(row)
        # name
        name = mod.display_title or mod.mod_id
        old_name = self.item(row, COL_NAME)
        if old_name is not None:
            old_name.setText(name)
            # 不再加粗，仅用颜色区分启用/未启用
            old_name.setForeground(QBrush(QColor("#000000" if en else "#8a8a8a")))
        # source
        src_map = {
            "scs": "本地SCS", "zip": "本地ZIP",
            "directory": "本地目录", "workshop": "创意工坊",
        }
        src_tip = f"{mod.package_type} · {mod.package_path}"
        sitem = self.item(row, COL_SOURCE)
        if sitem is not None:
            sitem.setText(src_map.get(mod.package_type or "", mod.package_type or "—"))
            sitem.setToolTip(src_tip)
        # version
        vitem = self.item(row, COL_VERSION)
        if vitem is not None:
            vitem.setText(mod.display_compatible_version or "—")

    def find_row_by_pkg(self, package_name: str) -> Optional[int]:
        """按 package_name 查找行号（COL_PKG 隐藏列）—— 支持 workshop ID 剥后缀智能匹配 + | 左段"""
        if not package_name:
            return None
        import re as _re_fr
        stripped = _re_fr.sub(r"_(workshop|copy\d*|local)$", "", package_name)
        left = package_name.split("|", 1)[0].strip()
        for r in range(self.rowCount()):
            it = self.item(r, COL_PKG)
            if it and it.text() == package_name:
                return r
        if stripped != package_name:
            for r in range(self.rowCount()):
                it = self.item(r, COL_PKG)
                if it and it.text() == stripped:
                    return r
        if left and left != package_name and left != stripped:
            for r in range(self.rowCount()):
                it = self.item(r, COL_PKG)
                if it and it.text() == left:
                    return r
            s_left = _re_fr.sub(r"_(workshop|copy\d*|local)$", "", left)
            if s_left != left:
                for r in range(self.rowCount()):
                    it = self.item(r, COL_PKG)
                    if it and it.text() == s_left:
                        return r
        for r in range(self.rowCount()):
            it = self.item(r, COL_PKG)
            if not it:
                continue
            col = it.text()
            col_left = col.split("|", 1)[0].strip()
            col_stripped = _re_fr.sub(r"_(workshop|copy\d*|local)$", "", col)
            if (col == stripped) or (col_stripped == package_name) or (col_stripped == stripped):
                return r
            # 左段匹配：mod_id（短）匹配 COL_PKG 中 "短|xxx" 这种
            if left and (col_left == package_name or col_left == stripped or col_left == left or
                         (_re_fr.sub(r"_(workshop|copy\d*|local)$", "", col_left) == stripped)):
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
# ============================================================
# Toolbar 样式常量（避免 4 份重复 CSS 字符串；改主题时只需改这里）
# ============================================================
_QTB_STYLE_DEFAULT = "QToolButton{padding:4px 10px;border:1px solid #d0d7de;border-radius:4px;background:#fff;}QToolButton:hover{background:#f3f4f6;}QToolButton::menu-indicator{width:0px;}"
_QTB_STYLE_PRIMARY = "QToolButton{padding:4px 12px;border:1px solid #1a7f37;border-radius:4px;background:#2da44e;color:#fff;font-weight:700;}QToolButton:hover{background:#2c974b;}QToolButton::menu-indicator{image:none;width:4px;}"


