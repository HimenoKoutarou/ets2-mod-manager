# -*- coding: utf-8 -*-
"""
ui/crash_check_dialog.py — 崩溃排查对话框（Crashlog 解析 + 启动游戏监控）

公共组件：
    CrashCheckSignals(QObject) — 3 个 Signal：
        locate_mod_requested(str) / disable_mods_requested(list) / move_to_bottom_requested(str)
    _AnalyzeWorker(QThread)    — 调用 services.crash_service.analyze_crashlog
    _GameLaunchWorker(QThread) — 启动游戏 + watchdog 等退出 + 检测崩溃
    CrashCheckDialog(QDialog)   — 单页 UI：启动游戏 / 手动选 crashlog / 嫌疑表

不直接修改 profile；所有变更通过 Signal 委托给主窗口 _ToolbarMixin。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, List

from PySide6.QtCore import Qt, QObject, Signal, QThread, QTimer
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFileDialog, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QSplitter,
    QPlainTextEdit, QAbstractItemView, QMessageBox, QFrame,
    QTabWidget,
)
from PySide6.QtGui import QColor

from services.crash_service import (
    Severity, CrashSuspicion, CrashSuspectMod, CrashAnalyzeResult,
    analyze_crashlog, discover_latest_crash_pair,
)
from services.game_launcher_service import (
    find_game_exe, find_game_docs_dir, launch_and_watch, GameLaunchHandle,
)

# ============================================================
# 颜色常量
# ============================================================
_RED_HEX = "#D7263D"
_YELLOW_HEX = "#F46036"
_GREEN_HEX = "#1B998B"
_ORANGE_HEX = "#F46036"
_BRIGHT_YELLOW_HEX = "#FFD500"


# ============================================================
# Signal 定义
# ============================================================
class CrashCheckSignals(QObject):
    """CrashCheckDialog 向外暴露的 3 个动作 Signal。"""
    locate_mod_requested = Signal(str)
    disable_mods_requested = Signal(list)
    move_to_bottom_requested = Signal(str)


# ============================================================
# _AnalyzeWorker — Crashlog 解析后台线程
# ============================================================
class _AnalyzeWorker(QThread):
    finished = Signal()

    def __init__(self, crash_path, log_path=None, profile=None, all_mods=None, parent=None):
        super().__init__(parent)
        self.crash_path = crash_path
        self.log_path = log_path
        self.profile = profile
        self.all_mods = list(all_mods) if all_mods else []
        self.result: Optional[CrashAnalyzeResult] = None
        self.error: Optional[str] = None

    def run(self):
        try:
            self.result = analyze_crashlog(
                self.crash_path, self.log_path,
                profile=self.profile, all_mods=self.all_mods,
            )
        except Exception as e:
            self.error = str(e)
        self.finished.emit()


# ============================================================
# _GameLaunchWorker — 启动游戏 + 等退出 + 检测崩溃
# ============================================================
class _GameLaunchWorker(QThread):
    """启动游戏，等待退出，通过 Signal 通知结果。"""
    game_launched = Signal()        # 游戏已启动
    game_exited = Signal(bool)      # 游戏退出（crashed=True/False）
    launch_failed = Signal(str)     # 启动失败

    def __init__(self, exe_path: str, docs_dir: str, parent=None):
        super().__init__(parent)
        self.exe_path = exe_path
        self.docs_dir = docs_dir
        self._handle: Optional[GameLaunchHandle] = None

    def run(self):
        try:
            exe = Path(self.exe_path)
            docs = Path(self.docs_dir)

            # 记录 crash.txt mtime（启动前）
            crash_txt = docs / "game.crash.txt"
            mtime_before = crash_txt.stat().st_mtime if crash_txt.exists() else 0.0

            # 启动游戏
            import subprocess, time

            proc = subprocess.Popen(
                [str(exe)],
                cwd=str(exe.parent),
            )
            self.game_launched.emit()

            # 等待退出
            proc.wait()
            time.sleep(0.5)  # 等文件系统刷新

            # 判断是否崩溃
            crashed = False
            if crash_txt.exists():
                mtime_after = crash_txt.stat().st_mtime
                if mtime_after > mtime_before:
                    crashed = True
            rc = proc.returncode
            if rc is not None and rc != 0:
                crashed = True

            self.game_exited.emit(crashed)

        except FileNotFoundError:
            self.launch_failed.emit("找不到游戏 exe")
        except PermissionError:
            self.launch_failed.emit("没有权限启动游戏")
        except Exception as e:
            self.launch_failed.emit(str(e))


# ============================================================
# CrashCheckDialog 主体
# ============================================================
class CrashCheckDialog(QDialog):
    """崩溃排查对话框：Crashlog 解析 + 启动游戏监控。

    尺寸 1000x680；上方启动游戏 + 路径选择，下方嫌疑表 + 证据原文。"""

    signals = CrashCheckSignals()

    def __init__(self, profile=None, all_mods: Optional[List] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("崩溃排查：启动游戏监控 / Crashlog 解析")
        self.resize(1000, 680)
        self._profile = profile
        self._all_mods = list(all_mods) if all_mods else []
        self._analyze_worker: Optional[_AnalyzeWorker] = None
        self._launch_worker: Optional[_GameLaunchWorker] = None
        self._analyze_result: Optional[CrashAnalyzeResult] = None
        self._auto_log_path: Optional[str] = None
        self._game_exe: Optional[str] = None

        self._build_ui()
        QTimer.singleShot(150, self._run_auto_analyze)
        QTimer.singleShot(50, self._discover_exe)

    # ----------------------------------------------------------
    # UI 构建
    # ----------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # ---- 上部：启动游戏区 ----
        launch_box = QFrame()
        launch_box.setFrameShape(QFrame.Shape.StyledPanel)
        launch_layout = QHBoxLayout(launch_box)
        launch_layout.setContentsMargins(8, 6, 8, 6)

        self.btn_launch = QPushButton("🎮 启动游戏并监控")
        self.btn_launch.setStyleSheet(
            f"background:{_GREEN_HEX};color:white;padding:8px 20px;"
            f"font-weight:bold;border-radius:6px;font-size:14px;"
        )
        self.btn_launch.setCursor(Qt.PointingHandCursor)
        self.btn_launch.clicked.connect(self._on_launch_clicked)
        launch_layout.addWidget(self.btn_launch)

        self.launch_status_label = QLabel("正在查找游戏…")
        launch_layout.addWidget(self.launch_status_label, 1)

        root.addWidget(launch_box)

        # ---- 中部：crashlog 路径选择 ----
        path_box = QHBoxLayout()
        path_box.addWidget(QLabel("game.crash.txt:"))
        self.crash_path_edit = QLineEdit()
        self.crash_path_edit.setPlaceholderText("选择或自动扫描 game.crash.txt 路径")
        path_box.addWidget(self.crash_path_edit, 1)
        btn_browse = QPushButton("浏览...")
        btn_browse.clicked.connect(self._browse_crash)
        path_box.addWidget(btn_browse)
        self.btn_auto_scan = QPushButton("🔎 自动扫描")
        self.btn_auto_scan.clicked.connect(self._run_auto_analyze)
        path_box.addWidget(self.btn_auto_scan)
        self.btn_start_analyze = QPushButton("解析")
        self.btn_start_analyze.clicked.connect(self._start_analyze)
        path_box.addWidget(self.btn_start_analyze)
        root.addLayout(path_box)

        # 状态行
        self.crash_status_label = QLabel("等待解析")
        root.addWidget(self.crash_status_label)

        # ---- 下部：嫌疑表 + 证据面板 ----
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.suspect_table = QTableWidget(0, 5)
        self.suspect_table.setHorizontalHeaderLabels(
            ["#", "嫌疑度", "Mod 名称", "优先级 #", "动作"]
        )
        self.suspect_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.suspect_table.verticalHeader().setVisible(False)
        self.suspect_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.suspect_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.suspect_table.cellClicked.connect(self._on_suspect_clicked)
        splitter.addWidget(self.suspect_table)

        # 下：日志面板（带内部 Tab）
        log_box = QFrame()
        log_layout = QVBoxLayout(log_box)
        log_layout.setContentsMargins(0, 0, 0, 0)
        self.log_tabs = QTabWidget()
        self.evidence_view = QPlainTextEdit()
        self.evidence_view.setReadOnly(True)
        self.evidence_view.setPlaceholderText("点击上方嫌疑行查看证据原文")
        self.log_tabs.addTab(self.evidence_view, "嫌疑证据")
        self.tail_view = QPlainTextEdit()
        self.tail_view.setReadOnly(True)
        self.tail_view.setPlaceholderText("无崩溃尾部日志数据")
        self.log_tabs.addTab(self.tail_view, "崩溃尾部 30 行")
        log_layout.addWidget(self.log_tabs)
        splitter.addWidget(log_box)

        splitter.setStretchFactor(0, 55)
        splitter.setStretchFactor(1, 45)
        root.addWidget(splitter, 1)

        # 底部关闭
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(btn_close)
        root.addLayout(bottom)

    # ----------------------------------------------------------
    # 启动游戏
    # ----------------------------------------------------------
    def _discover_exe(self) -> None:
        """后台发现游戏 exe 路径。"""
        try:
            exe = find_game_exe()
            if exe:
                self._game_exe = str(exe)
                self.launch_status_label.setText(f"已找到: {exe.name}")
            else:
                self.launch_status_label.setText("未找到游戏，可手动选择")
        except Exception as e:
            self.launch_status_label.setText(f"查找失败: {e}")

    def _on_launch_clicked(self) -> None:
        """启动游戏按钮点击。"""
        exe_path = self._game_exe

        if not exe_path or not Path(exe_path).exists():
            path, _ = QFileDialog.getOpenFileName(
                self, "选择游戏 exe", "",
                "eurotrucks2.exe (eurotrucks2.exe);;amtrucks.exe (amtrucks.exe);;All (*.exe)",
            )
            if not path:
                return
            exe_path = path
            self._game_exe = path

        docs_dir = find_game_docs_dir()
        if not docs_dir:
            QMessageBox.warning(self, "无法启动", "找不到游戏文档目录")
            return

        self.btn_launch.setEnabled(False)
        self.launch_status_label.setText("游戏启动中…")

        self._launch_worker = _GameLaunchWorker(str(exe_path), str(docs_dir), self)
        self._launch_worker.game_launched.connect(self._on_game_launched)
        self._launch_worker.game_exited.connect(self._on_game_exited)
        self._launch_worker.launch_failed.connect(self._on_launch_failed)
        self._launch_worker.start()

    def _on_game_launched(self) -> None:
        self.launch_status_label.setText("🎮 游戏运行中… 关闭游戏后将自动检测崩溃")

    def _on_game_exited(self, crashed: bool) -> None:
        self.btn_launch.setEnabled(True)
        if crashed:
            self.launch_status_label.setText("⚠ 检测到崩溃！正在自动分析…")
            QTimer.singleShot(500, self._run_auto_analyze)
        else:
            self.launch_status_label.setText("✅ 游戏正常退出，未检测到崩溃")

    def _on_launch_failed(self, msg: str) -> None:
        self.btn_launch.setEnabled(True)
        self.launch_status_label.setText(f"启动失败: {msg}")
        QMessageBox.warning(self, "启动失败", msg)

    # ----------------------------------------------------------
    # Crashlog：浏览 / 自动扫描 / 解析
    # ----------------------------------------------------------
    def _browse_crash(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 game.crash.txt", "",
            "game.crash.txt (*.txt);;All (*.*)",
        )
        if path:
            self.crash_path_edit.setText(path)
            self._auto_log_path = None
            self._start_analyze()

    def _run_auto_analyze(self) -> None:
        """自动扫描默认 Documents 目录下最新的 (game.crash.txt, game.log.txt)。"""
        try:
            pair = discover_latest_crash_pair()
        except Exception:
            pair = None
        if not pair:
            return
        crash_p = pair.get("crash")
        log_p = pair.get("log")
        if crash_p is None:
            return
        self.crash_path_edit.setText(str(crash_p))
        self._auto_log_path = str(log_p) if log_p else None
        self._start_analyze()

    def _start_analyze(self) -> None:
        crash_path = self.crash_path_edit.text().strip()
        if not crash_path:
            QMessageBox.information(
                self, "缺少路径",
                "请先选择或自动扫描 game.crash.txt 路径。",
            )
            return
        if self._analyze_worker is not None and self._analyze_worker.isRunning():
            return
        log_path = self._auto_log_path
        self.btn_start_analyze.setEnabled(False)
        self.crash_status_label.setText("解析中...")
        self._analyze_worker = _AnalyzeWorker(
            crash_path=crash_path, log_path=log_path,
            profile=self._profile, all_mods=self._all_mods, parent=self,
        )
        self._analyze_worker.finished.connect(self._on_analyze_done)
        self._analyze_worker.start()

    def _on_analyze_done(self) -> None:
        w = self._analyze_worker
        self.btn_start_analyze.setEnabled(True)
        if w is None:
            return
        if w.error:
            self.crash_status_label.setText(f"解析失败: {w.error}")
            return
        self._analyze_result = w.result
        if self._analyze_result is None:
            self.crash_status_label.setText("解析失败：无返回结果")
            return
        r = self._analyze_result
        self.crash_status_label.setText(
            f"崩溃时间: {r.crash_time or '未知'} · Build: {r.build_version or '未知'} · "
            f"异常码: {r.exception_code or '未知'} · 模块: {r.fault_module_category}"
        )
        self._refresh_suspect_table()
        self.tail_view.setPlainText("\n".join(r.raw_tail_lines or []))

    # ----------------------------------------------------------
    # 嫌疑表 + 徽章 + 动作按钮
    # ----------------------------------------------------------
    def _refresh_suspect_table(self) -> None:
        self.suspect_table.setRowCount(0)
        if self._analyze_result is None:
            return
        for s in self._analyze_result.suspects:
            self._append_suspect_row(s)

    def _append_suspect_row(self, s: CrashSuspectMod) -> None:
        row = self.suspect_table.rowCount()
        self.suspect_table.insertRow(row)

        rank_item = QTableWidgetItem(str(s.rank))
        rank_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.suspect_table.setItem(row, 0, rank_item)

        susp_placeholder = QTableWidgetItem()
        susp_placeholder.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.suspect_table.setItem(row, 1, susp_placeholder)
        badge = QLabel(s.suspicion.value)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(self._suspicion_badge_css(s.suspicion))
        self.suspect_table.setCellWidget(row, 1, badge)

        name_item = QTableWidgetItem(s.mod_display_name or s.mod_id or "(未知 mod)")
        name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        name_item.setToolTip("\n".join(s.evidence_lines))
        self.suspect_table.setItem(row, 2, name_item)

        prio_text = "—" if s.priority_index is None else str(s.priority_index)
        prio_item = QTableWidgetItem(prio_text)
        prio_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.suspect_table.setItem(row, 3, prio_item)

        action_widget = QWidget()
        action_layout = QHBoxLayout(action_widget)
        action_layout.setContentsMargins(2, 2, 2, 2)
        btn_locate = QPushButton("跳到 mod")
        btn_locate.clicked.connect(
            lambda _checked=False, mid=s.mod_id: self._emit_locate(mid)
        )
        action_layout.addWidget(btn_locate)
        btn_disable = QPushButton("禁用")
        btn_disable.clicked.connect(
            lambda _checked=False, mid=s.mod_id: self._emit_disable_one(mid)
        )
        action_layout.addWidget(btn_disable)
        btn_move_bottom = QPushButton("下移最底")
        btn_move_bottom.clicked.connect(
            lambda _checked=False, mid=s.mod_id: self._emit_move_bottom(mid)
        )
        action_layout.addWidget(btn_move_bottom)
        self.suspect_table.setCellWidget(row, 4, action_widget)

    @staticmethod
    def _suspicion_badge_css(susp: CrashSuspicion) -> str:
        if susp == CrashSuspicion.S:
            bg, fg = _RED_HEX, "white"
        elif susp == CrashSuspicion.A:
            bg, fg = _ORANGE_HEX, "black"
        else:
            bg, fg = _BRIGHT_YELLOW_HEX, "black"
        return (f"background:{bg};color:{fg};padding:4px 8px;"
                f"border-radius:8px;font-weight:bold;")

    def _on_suspect_clicked(self, row: int, _col: int) -> None:
        if self._analyze_result is None:
            return
        suspects = self._analyze_result.suspects
        if row < 0 or row >= len(suspects):
            return
        s = suspects[row]
        self.evidence_view.setPlainText("\n".join(s.evidence_lines))
        self.log_tabs.setCurrentIndex(0)

    def _emit_locate(self, mod_id: str) -> None:
        if mod_id:
            self.signals.locate_mod_requested.emit(mod_id)

    def _emit_disable_one(self, mod_id: str) -> None:
        if not mod_id:
            return
        ret = QMessageBox.warning(
            self, "确认禁用",
            f"确认禁用 mod '{mod_id}' 并保存 profile？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret == QMessageBox.StandardButton.Yes:
            self.signals.disable_mods_requested.emit([mod_id])

    def _emit_move_bottom(self, mod_id: str) -> None:
        if mod_id:
            self.signals.move_to_bottom_requested.emit(mod_id)

    # ----------------------------------------------------------
    # 关闭时安全停止 Worker
    # ----------------------------------------------------------
    def closeEvent(self, event) -> None:
        try:
            if (self._analyze_worker is not None
                    and self._analyze_worker.isRunning()):
                self._analyze_worker.requestInterruption()
                self._analyze_worker.wait(1000)
                if self._analyze_worker.isRunning():
                    self._analyze_worker.terminate()
            if (self._launch_worker is not None
                    and self._launch_worker.isRunning()):
                self._launch_worker.requestInterruption()
                self._launch_worker.wait(1000)
                if self._launch_worker.isRunning():
                    self._launch_worker.terminate()
        except Exception as _e:
            import sys as _sys
            print(f"[crash_dialog] closeEvent error: {_e}", file=_sys.stderr)
        super().closeEvent(event)
