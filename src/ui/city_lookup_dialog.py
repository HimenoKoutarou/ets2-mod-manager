"""城市反查 mod 对话框

输入城市名 → 模糊匹配索引 → 表格显示
  - 列：城市 / 国家 / 生效 mod / 优先级位 / 全部来源数
  - 行排序：按 priority_index 升序（生效 mod 在前）
  - 双击行：定位主窗口表格中对应的 mod

UI 结构：
  +---------------------------------------------------------+
  | 搜索框：[输入城市名...] [重建索引]                         |
  +---------------------------------------------------------+
  | 状态行：找到 N 个城市，索引已就绪 / 索引构建中...           |
  +---------------------------------------------------------+
  | 表格：| 城市 | 国家 | 生效 mod | 优先级 | 来源数 |         |
  +---------------------------------------------------------+
"""
from __future__ import annotations

from typing import List, Optional, Callable

from PySide6.QtCore import Qt, QTimer, QThread, Signal
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QLabel, QHeaderView,
    QMessageBox, QProgressBar, QAbstractItemView,
)

from services.i18n_service import _
from services.city_lookup_service import CityLookupService, CityIndex, CityHit
from core.models import Mod


# =========================================================================
# 后台线程：扫描已启用 mod 建立索引
# =========================================================================
class _RebuildWorker(QThread):
    """后台扫描 mod 建立城市索引，避免 UI 卡顿。"""
    progress = Signal(int, int, str)   # current, total, mod_title
    finished_ok = Signal(object)        # CityIndex

    def __init__(self, svc: CityLookupService, enabled_mods: List[Mod]):
        super().__init__()
        self._svc = svc
        self._mods = enabled_mods
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    # ---- R12 opt: worker 端自定义取消异常（BaseException 子类，不被常规 except 吞）----
    class _WorkerCancelled(BaseException):
        pass

    def run(self):
        def cb(cur, total, title):
            if self._cancelled:
                # 抛 BaseException 子类，rebuild 循环中的 except Exception 不会吞，能立刻结束 worker
                raise _RebuildWorker._WorkerCancelled()
            self.progress.emit(cur, total, title)
        try:
            idx = self._svc.rebuild(self._mods, progress_cb=cb)
            self.finished_ok.emit(idx)
        except _RebuildWorker._WorkerCancelled:
            self.finished_ok.emit(None)
        except Exception as e:
            self.finished_ok.emit(None)


# =========================================================================
# 主对话框
# =========================================================================
class CityLookupDialog(QDialog):
    """城市反查 mod 对话框。"""

    def __init__(self, parent=None,
                 enabled_mods: Optional[List[Mod]] = None,
                 project_root=None,
                 on_locate_mod: Optional[Callable[[str], None]] = None):
        super().__init__(parent)
        self.setWindowTitle(_("city_lookup.title"))
        self.setModal(True)
        self.resize(780, 520)

        self._enabled_mods = enabled_mods or []
        self._on_locate = on_locate_mod
        self._svc = CityLookupService(project_root=project_root)
        self._worker: Optional[_RebuildWorker] = None
        self._index: Optional[CityIndex] = None
        self._results: List[tuple] = []  # [(city_name, hits), ...]

        self._build_ui()
        self._apply_styles()

        # 启动后异步加载/重建索引
        QTimer.singleShot(50, self._ensure_index_async)

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # 顶部搜索 + 重建按钮
        top = QHBoxLayout()
        top.setSpacing(8)
        self.input = QLineEdit()
        self.input.setPlaceholderText(_("city_lookup.ph_search"))
        self.input.setClearButtonEnabled(True)
        self.input.textChanged.connect(self._on_text_changed)
        top.addWidget(self.input, 1)

        self.btn_rebuild = QPushButton(_("city_lookup.rebuild"))
        self.btn_rebuild.clicked.connect(self._force_rebuild)
        top.addWidget(self.btn_rebuild)
        root.addLayout(top)

        # 进度条
        self.progress = QProgressBar()
        self.progress.setFixedHeight(6)
        self.progress.setRange(0, 100)
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        # 状态行
        self.lbl_status = QLabel(_("city_lookup.status_loading"))
        self.lbl_status.setStyleSheet("color:#a6adc8;font-size:12px;")
        root.addWidget(self.lbl_status)

        # 结果表格
        self.tbl = QTableWidget(0, 5)
        self.tbl.setHorizontalHeaderLabels([
            _("city_lookup.col_city"),
            _("city_lookup.col_country"),
            _("city_lookup.col_effective_mod"),
            _("city_lookup.col_priority"),
            _("city_lookup.col_sources"),
        ])
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl.setAlternatingRowColors(True)
        self.tbl.doubleClicked.connect(self._on_row_double_clicked)
        hdr = self.tbl.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)         # 城市
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents) # 国家
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)         # 生效 mod
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents) # 优先级
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents) # 来源数
        self.tbl.verticalHeader().setVisible(False)
        root.addWidget(self.tbl, 1)

        # 底部说明
        self.lbl_hint = QLabel(_("city_lookup.hint"))
        self.lbl_hint.setStyleSheet("color:#6c7086;font-size:11px;")
        self.lbl_hint.setWordWrap(True)
        root.addWidget(self.lbl_hint)

    def _apply_styles(self):
        self.setStyleSheet("""
            QDialog { background: #fafbfc; }
            QLineEdit {
                padding: 6px 10px;
                border: 1px solid #d0d7de;
                border-radius: 4px;
                background: #fff;
                font-size: 13px;
            }
            QLineEdit:focus { border-color: #0969da; }
            QPushButton {
                padding: 6px 14px;
                border: 1px solid #d0d7de;
                border-radius: 4px;
                background: #fff;
                font-size: 13px;
            }
            QPushButton:hover { background: #f3f4f6; }
            QPushButton:disabled { color: #8c959f; }
            QTableWidget {
                border: 1px solid #d0d7de;
                border-radius: 4px;
                background: #fff;
                gridline-color: #eaeef2;
                font-size: 13px;
            }
            QHeaderView::section {
                background: #f6f8fa;
                padding: 6px 8px;
                border: none;
                border-right: 1px solid #eaeef2;
                border-bottom: 1px solid #d0d7de;
                font-weight: 600;
            }
            QProgressBar {
                border: none;
                background: #eaeef2;
                border-radius: 3px;
            }
            QProgressBar::chunk { background: #0969da; border-radius: 3px; }
        """)

    # ------------------------------------------------------------------
    # 索引加载
    # ------------------------------------------------------------------
    def _ensure_index_async(self):
        """异步加载缓存或重建索引。"""
        try:
            sig = self._svc._signature(self._enabled_mods)
            cached = self._svc.get_index()
            if cached.profile_signature == sig and cached.cities:
                # 缓存命中
                self._index = cached
                self._on_index_ready()
                return
        except Exception:
            pass
        # 需要重建
        self._start_rebuild()

    def _force_rebuild(self):
        """手动触发重建。"""
        if self._worker and self._worker.isRunning():
            return
        self._start_rebuild()

    def _start_rebuild(self):
        if not self._enabled_mods:
            self.lbl_status.setText(_("city_lookup.status_no_mods"))
            return
        # 清空旧结果
        self.tbl.setRowCount(0)
        self.input.clear()
        self.progress.setVisible(True)
        self.progress.setRange(0, len(self._enabled_mods))
        self.progress.setValue(0)
        self.lbl_status.setText(_("city_lookup.status_building"))
        self.btn_rebuild.setEnabled(False)

        self._worker = _RebuildWorker(self._svc, self._enabled_mods)
        self._worker.progress.connect(self._on_rebuild_progress)
        self._worker.finished_ok.connect(self._on_rebuild_finished)
        self._worker.start()

    def _on_rebuild_progress(self, cur, total, title):
        self.progress.setMaximum(total)
        self.progress.setValue(cur)
        self.lbl_status.setText(
            _("city_lookup.status_progress", cur=cur, total=total, name=title)
        )

    def _on_rebuild_finished(self, idx):
        self.progress.setVisible(False)
        self.btn_rebuild.setEnabled(True)
        if idx is None:
            self.lbl_status.setText(_("city_lookup.status_failed"))
            QMessageBox.warning(self, _("city_lookup.title"), _("city_lookup.msg_failed"))
            return
        self._index = idx
        self._on_index_ready()

    def _on_index_ready(self):
        if self._index is None:
            return
        n = len(self._index.cities)
        self.lbl_status.setText(_("city_lookup.status_ready", count=n))
        # 触发一次搜索（空关键字=显示全部）
        self._apply_search("")

    # ------------------------------------------------------------------
    # 搜索（debounce 300ms）
    # ------------------------------------------------------------------
    def _on_text_changed(self, text: str):
        # debounce 300ms
        if not hasattr(self, "_debounce"):
            self._debounce = QTimer(self)
            self._debounce.setSingleShot(True)
            self._debounce.setInterval(300)
            self._debounce.timeout.connect(lambda: self._apply_search(self.input.text().strip()))
        self._debounce.start()

    def _apply_search(self, keyword: str):
        if self._index is None:
            return
        self._results = self._index.search(keyword, limit=500)
        self.tbl.setRowCount(len(self._results))
        # ---- R7 opt: 批量填充期间禁用重绘，500 行场景减少布局抖动 ----
        self.tbl.setUpdatesEnabled(False)
        # 循环内重复构造的字体/颜色提到外部
        from PySide6.QtGui import QFont as _QFont_R7, QColor as _QColor_R7
        _bold_font = _QFont_R7(); _bold_font.setBold(True)
        _green = _QColor_R7("#1a7f37")
        _amber = _QColor_R7("#9a6700")
        try:

            for row, (city_name, hits) in enumerate(self._results):
                eff = hits[0] if hits else None
                # 城市
                ci = QTableWidgetItem(city_name)
                ci.setData(Qt.UserRole, city_name)
                self.tbl.setItem(row, 0, ci)
                # 国家
                self.tbl.setItem(row, 1, QTableWidgetItem(eff.country if eff else ""))
                # 生效 mod
                mod_text = (eff.mod_title if eff else "") or ""
                mi = QTableWidgetItem(mod_text)
                mi.setData(Qt.UserRole + 1, eff.mod_id if eff else "")
                # 标记优先级最高的一行加粗显示生效 mod
                if eff and len(hits) > 1:
                    # 有多个来源：生效 mod 加粗
                    mi.setFont(_bold_font)
                    mi.setForeground(_green)
                self.tbl.setItem(row, 2, mi)
                # 优先级
                p_text = f"#{eff.priority_index}" if eff and eff.priority_index >= 0 else "—"
                self.tbl.setItem(row, 3, QTableWidgetItem(p_text))
                # 来源数
                src_text = str(len(hits)) if hits else "0"
                it_src = QTableWidgetItem(src_text)
                if len(hits) > 1:
                    it_src.setForeground(_amber)
                self.tbl.setItem(row, 4, it_src)

        finally:
            self.tbl.setUpdatesEnabled(True)

        # 状态更新
        found = len(self._results)
        if keyword:
            self.lbl_status.setText(
                _("city_lookup.status_search", kw=keyword, count=found)
            )
        else:
            self.lbl_status.setText(
                _("city_lookup.status_ready", count=found)
            )

    # ------------------------------------------------------------------
    # 双击行：定位到 mod
    # ------------------------------------------------------------------
    def _on_row_double_clicked(self, index):
        if not self._on_locate:
            return
        row = index.row()
        if row < 0 or row >= len(self._results):
            return
        city_name, hits = self._results[row]
        if not hits:
            return
        # 默认定位生效 mod（第一个）；如果想要其他来源可后续扩展
        eff = hits[0]
        if eff.mod_id:
            self._on_locate(eff.mod_id)

    # ------------------------------------------------------------------
    # 关闭清理
    # ------------------------------------------------------------------
    def closeEvent(self, e):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            # 优雅等待 2s，若 worker 仍在扫描大 mod（cancel 检查点还没到）则强制终止
            if not self._worker.wait(2000):
                import sys as _sys_r12
                print("[city_lookup] worker 未及时响应 cancel，执行 terminate 兜底", file=_sys_r12.stderr)
                self._worker.terminate()
                self._worker.wait(1000)
        super().closeEvent(e)
