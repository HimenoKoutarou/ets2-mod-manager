"""汉化对话框 UI"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QTabWidget, QTableWidget, QTableWidgetItem, QDialog, QVBoxLayout,
    QHBoxLayout, QPushButton, QLabel, QProgressBar, QFileDialog,
    QHeaderView, QComboBox, QMessageBox
)

from core.game_data import GameDataResult
from services.l10n_service import L10nService, L10nResult, TranslationEntry


class _ExtractThread(QThread):
    progress = Signal(int, int, str)
    result_ready = Signal(object)
    canceled = Signal()

    def __init__(self, active_mods: list, mod_dir: str, target_locale: str = "zh_cn", parent=None):
        super().__init__(parent)
        self._active_mods = active_mods
        self._mod_dir = mod_dir
        self._target_locale = target_locale
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True
        self.requestInterruption()

    def should_stop(self) -> bool:
        return self._stop_requested or self.isInterruptionRequested()

    def run(self):
        from core.game_data import extract_game_data_for_active_mods
        total = len(self._active_mods)
        self.progress.emit(0, total, "扫描中...")
        error_text = ""
        try:
            def progress_cb(cur, count, name):
                self.progress.emit(cur + 1, count, name)
            game_data = extract_game_data_for_active_mods(
                self._active_mods,
                self._target_locale,
                should_stop=self.should_stop,
                progress=progress_cb,
            )
        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"
            game_data = GameDataResult()
        if self.should_stop():
            self.canceled.emit()
            return
        if error_text:
            game_data._extract_error = error_text
        self.progress.emit(total, total, "")
        self.result_ready.emit(game_data)


class _TranslateThread(QThread):
    progress = Signal(int, int, str)
    result_ready = Signal(bool, str)

    def __init__(self, l10n_service: L10nService, entries: List[TranslationEntry], parent=None):
        super().__init__(parent)
        self._service = l10n_service
        self._entries = entries

    def run(self):
        def cb(cur, total, name):
            self.progress.emit(cur, total, name)
        try:
            self._service.batch_translate(self._entries, cb)
            self.result_ready.emit(True, "")
        except Exception as exc:
            self.result_ready.emit(False, f"{type(exc).__name__}: {exc}")


class L10nDialog(QDialog):
    STATUS_COLORS = {
        "native":  QColor("#16a34a"),
        "local":   QColor("#22c55e"),
        "ufl":     QColor("#3b82f6"),
        "api":     QColor("#a855f7"),
        "pending": QColor("#f59e0b"),
        "failed":  QColor("#ef4444"),
    }
    STATUS_LABELS = {
        "native":  "原生",
        "local":   "已确认",
        "ufl":     "UFL",
        "api":     "AI翻译",
        "pending": "待翻译",
        "failed":  "未翻译",
    }

    def __init__(self, l10n_service: L10nService, parent=None):
        super().__init__(parent)
        self.l10n = l10n_service
        self.result: Optional[L10nResult] = None
        self._entries: List[TranslationEntry] = []
        self._extract_thread: Optional[_ExtractThread] = None
        self._translate_thread: Optional[_TranslateThread] = None
        self._closing_for_workers = False
        self.current_locale: str = l10n_service.get_target_locale()

        self.setWindowTitle("汉化管理")
        self.resize(900, 600)

        layout = QVBoxLayout(self)

        top_layout = QHBoxLayout()
        self.locale_label = QLabel("目标语言:")
        top_layout.addWidget(self.locale_label)

        self.locale_combo = QComboBox()
        for loc in L10nService.SUPPORTED_LOCALES:
            display = L10nService.LOCALE_DISPLAY_NAMES.get(loc, loc)
            self.locale_combo.addItem(f"{display} ({loc})", loc)
        current = self.l10n.get_target_locale()
        idx = L10nService.SUPPORTED_LOCALES.index(current) if current in L10nService.SUPPORTED_LOCALES else 0
        self.locale_combo.setCurrentIndex(idx)
        self.locale_combo.currentIndexChanged.connect(self._on_locale_changed)
        top_layout.addWidget(self.locale_combo, 1)
        layout.addLayout(top_layout)

        self.status_label = QLabel("准备提取已启用mod的数据...")
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self.tab_cities = self._create_tab("城市")
        self.tab_countries = self._create_tab("国家")
        self.tab_ferries = self._create_tab("港口")
        self.tab_hints = self._create_tab("提示文本")

        btn_layout = QHBoxLayout()
        self.btn_translate = QPushButton("翻译未翻译项")
        self.btn_translate.clicked.connect(self._do_translate)
        btn_layout.addWidget(self.btn_translate)

        self.btn_import_dict = QPushButton("导入词典")
        self.btn_import_dict.clicked.connect(self._do_import_dict)
        btn_layout.addWidget(self.btn_import_dict)

        self.btn_export = QPushButton("导出汉化mod")
        self.btn_export.clicked.connect(self._do_export)
        btn_layout.addWidget(self.btn_export)

        btn_layout.addStretch()
        self.btn_close = QPushButton("关闭")
        self.btn_close.clicked.connect(self.close)
        btn_layout.addWidget(self.btn_close)
        layout.addLayout(btn_layout)

    def _create_tab(self, name: str) -> QTableWidget:
        table = QTableWidget(0, 5)
        table.setHorizontalHeaderLabels(["英文名", "中文名", "来源mod", "状态", ""])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        table.setAlternatingRowColors(True)
        table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.tabs.addTab(table, name)
        return table

    def start_extract(self, active_mods: list, mod_dir: str):
        self.current_locale = self.l10n.get_target_locale()
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, max(1, len(active_mods)))
        self.status_label.setText(f"正在提取已启用mod的数据 (0/{len(active_mods)})...")
        self.locale_combo.setEnabled(False)
        self.btn_close.setEnabled(True)
        self._extract_thread = _ExtractThread(
            active_mods, mod_dir, self.current_locale, self
        )
        self._extract_thread.progress.connect(self._on_extract_progress)
        self._extract_thread.result_ready.connect(self._on_extract_done)
        self._extract_thread.canceled.connect(self._on_extract_canceled)
        self._extract_thread.finished.connect(self._on_extract_thread_finished)
        self._extract_thread.start()

    def _on_extract_thread_finished(self):
        worker = self._extract_thread
        self._extract_thread = None
        self.locale_combo.setEnabled(True)
        if worker is not None:
            worker.deleteLater()

    def _on_locale_changed(self, idx: int):
        new_locale = self.locale_combo.itemData(idx)
        if not new_locale:
            return
        ok = self.l10n.set_target_locale(new_locale)
        if not ok:
            self.status_label.setText(f"切换语言失败: 不支持 {new_locale}")
            return
        self.current_locale = new_locale
        self.result = None
        self._entries = []
        self.tab_cities.setRowCount(0)
        self.tab_countries.setRowCount(0)
        self.tab_ferries.setRowCount(0)
        self.tab_hints.setRowCount(0)
        self.tabs.setTabText(0, "城市")
        self.tabs.setTabText(1, "国家")
        self.tabs.setTabText(2, "港口")
        self.tabs.setTabText(3, "提示文本")
        self.btn_translate.setText("翻译未翻译项")
        self.btn_translate.setEnabled(False)
        display = L10nService.LOCALE_DISPLAY_NAMES.get(new_locale, new_locale)
        self.status_label.setText(f"目标语言已切换为 {display}，请重新提取 mod 数据以获取翻译结果")

    def _on_extract_progress(self, current, total, name):
        self.progress_bar.setValue(current)
        self.status_label.setText(f"正在提取 ({current}/{total}): {name}")

    def _on_extract_done(self, game_data: GameDataResult):
        self.progress_bar.setVisible(False)
        n_c = len(game_data.cities)
        n_co = len(game_data.countries)
        n_f = len(game_data.ferries)
        n_h = len(game_data.hints)
        n_loc = len(game_data.native_locale_dict)
        extract_error = getattr(game_data, "_extract_error", "")
        if extract_error:
            self.status_label.setText(f"提取失败：{extract_error}")
            QMessageBox.warning(self, "汉化数据提取失败", extract_error)
            return
        self.status_label.setText(
            f"提取完成: {n_c}个城市, {n_co}个国家, {n_f}个港口, {n_h}条提示文本"
            + (f", {n_loc}条原生翻译" if n_loc else "")
        )

        self.l10n.set_native_locale(game_data.native_locale_dict)

        self.result = L10nResult()
        for c in game_data.cities:
            e = self.l10n.translate(c.city_name, "city", c.source_mod)
            self.result.cities.append(e)
            self._entries.append(e)
        for c in game_data.countries:
            e = self.l10n.translate(c.name, "country", c.source_mod)
            self.result.countries.append(e)
            self._entries.append(e)
        for f in game_data.ferries:
            e = self.l10n.translate(f.ferry_name, "ferry", f.source_mod)
            self.result.ferries.append(e)
            self._entries.append(e)
        for h in game_data.hints:
            e = self.l10n.translate(h.text, "hint", h.source_mod)
            self.result.hints.append(e)
            self._entries.append(e)

        self._fill_table(self.tab_cities, self.result.cities)
        self._fill_table(self.tab_countries, self.result.countries)
        self._fill_table(self.tab_ferries, self.result.ferries)
        self._fill_table(self.tab_hints, self.result.hints)

        self.tabs.setTabText(0, f"城市 ({n_c})")
        self.tabs.setTabText(1, f"国家 ({n_co})")
        self.tabs.setTabText(2, f"港口 ({n_f})")
        self.tabs.setTabText(3, f"提示文本 ({len(game_data.hints)})")

        pending = self.result.pending_count
        self.btn_translate.setText(f"翻译未翻译项 ({pending})")
        self.btn_translate.setEnabled(pending > 0)

    def _on_extract_canceled(self):
        self.progress_bar.setVisible(False)
        self.status_label.setText("扫描已取消")

    def _fill_table(self, table: QTableWidget, entries: List[TranslationEntry]):
        table.setRowCount(len(entries))
        for i, e in enumerate(entries):
            item0 = QTableWidgetItem(e.source)
            item0.setFlags(item0.flags() & ~Qt.ItemIsEditable)
            table.setItem(i, 0, item0)

            item1 = QTableWidgetItem(e.translated)
            if e.status == "native":
                item1.setForeground(self.STATUS_COLORS["native"])
            elif e.status in ("local", "ufl"):
                item1.setForeground(self.STATUS_COLORS["local"])
            elif e.status == "api":
                item1.setForeground(self.STATUS_COLORS["api"])
            elif e.status in ("pending", "failed"):
                item1.setForeground(self.STATUS_COLORS["failed"])
            table.setItem(i, 1, item1)

            item2 = QTableWidgetItem(e.source_mod)
            item2.setFlags(item2.flags() & ~Qt.ItemIsEditable)
            table.setItem(i, 2, item2)

            item3 = QTableWidgetItem(self.STATUS_LABELS.get(e.status, e.status))
            item3.setForeground(self.STATUS_COLORS.get(e.status, QColor("#999")))
            item3.setFlags(item3.flags() & ~Qt.ItemIsEditable)
            table.setItem(i, 3, item3)

    def _on_cell_double_clicked(self, row, col):
        if col != 1:
            return
        table = self.tabs.currentWidget()
        item = table.item(row, 1)
        if not item:
            return
        new_text = item.text().strip()
        source_item = table.item(row, 0)
        if not source_item:
            return
        source = source_item.text()
        if new_text:
            self.l10n.update_translation(source, new_text)
            status_item = table.item(row, 3)
            if status_item:
                status_item.setText(self.STATUS_LABELS["local"])
                status_item.setForeground(self.STATUS_COLORS["local"])
            item.setForeground(self.STATUS_COLORS["local"])
            for e in self._entries:
                if e.source == source:
                    e.translated = new_text
                    e.status = "local"
                    break

    def _do_translate(self):
        pending = [e for e in self._entries if e.status in ("pending", "failed")]
        if not pending:
            return
        self.btn_translate.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, len(pending))
        self.status_label.setText(f"正在翻译 ({len(pending)}项)...")
        self._translate_thread = _TranslateThread(self.l10n, pending, self)
        self._translate_thread.progress.connect(self._on_translate_progress)
        self._translate_thread.result_ready.connect(self._on_translate_done)
        self._translate_thread.finished.connect(self._on_translate_thread_finished)
        self._translate_thread.start()

    def _on_translate_thread_finished(self):
        worker = self._translate_thread
        self._translate_thread = None
        if worker is not None:
            worker.deleteLater()

    def _on_translate_progress(self, current, total, name):
        self.progress_bar.setValue(current)
        if name:
            self.status_label.setText(f"翻译中 ({current}/{total}): {name}")

    def _on_translate_done(self, success=True, error_text=""):
        self.progress_bar.setVisible(False)
        if not success:
            self.status_label.setText(f"翻译失败: {error_text}")
            self.btn_translate.setEnabled(True)
            return
        translated = sum(1 for e in self._entries if e.status == "api")
        still_failed = sum(1 for e in self._entries if e.status == "failed")
        self.status_label.setText(
            f"翻译完成: API翻译{translated}项"
            + (f", 仍有{still_failed}项需手动补全" if still_failed else "")
        )
        self._fill_table(self.tab_cities, self.result.cities)
        self._fill_table(self.tab_countries, self.result.countries)
        self._fill_table(self.tab_ferries, self.result.ferries)
        self._fill_table(self.tab_hints, self.result.hints)
        pending = self.result.pending_count
        self.btn_translate.setText(f"翻译未翻译项 ({pending})")
        self.btn_translate.setEnabled(pending > 0)

    def _do_import_dict(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择词典文件", "",
            "词典文件 (*.json *.csv *.txt);;JSON (*.json);;CSV (*.csv);;TXT (*.txt);;所有文件 (*.*)"
        )
        if not file_path:
            return
        try:
            success, skipped, messages = self.l10n.import_custom_dict(Path(file_path), merge=True)
        except Exception as e:
            QMessageBox.critical(self, "导入失败", f"导入过程发生异常:\n{e}")
            return

        msg_lines = []
        msg_lines.append(f"成功导入: {success} 条")
        msg_lines.append(f"跳过: {skipped} 条")
        if messages:
            preview = messages[:50]
            msg_lines.append("")
            msg_lines.append("详细信息:")
            msg_lines.extend(preview)
            if len(messages) > 50:
                msg_lines.append(f"... 另有 {len(messages) - 50} 条信息未显示")

        full_msg = "\n".join(msg_lines)
        if any(m.startswith("错误:") for m in messages):
            QMessageBox.warning(self, "词典导入完成(含错误)", full_msg)
        else:
            QMessageBox.information(self, "词典导入完成", full_msg)

        if self.result:
            self._refill_with_new_dict()

    def _refill_with_new_dict(self):
        """在保持 source_mod/category 的前提下，根据新字典重新翻译并刷新表格"""
        def refresh_list(lst):
            for e in lst:
                if e.status in ("native",):
                    continue
                new_entry = self.l10n.translate(e.source, e.category, e.source_mod)
                e.translated = new_entry.translated
                e.status = new_entry.status

        refresh_list(self.result.cities)
        refresh_list(self.result.countries)
        refresh_list(self.result.ferries)
        refresh_list(self.result.hints)

        self._fill_table(self.tab_cities, self.result.cities)
        self._fill_table(self.tab_countries, self.result.countries)
        self._fill_table(self.tab_ferries, self.result.ferries)
        self._fill_table(self.tab_hints, self.result.hints)

        pending = self.result.pending_count
        self.btn_translate.setText(f"翻译未翻译项 ({pending})")
        self.btn_translate.setEnabled(pending > 0)

    def _do_export(self):
        if not self.result or self.result.translated_count == 0:
            self.status_label.setText("没有可导出的翻译内容")
            return
        default_name = "himeno_sena.generated.scs"
        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出汉化mod", default_name, "SCS Mod (*.scs);;ZIP (*.zip)"
        )
        if not file_path:
            return
        try:
            display_name_suffix = L10nService.LOCALE_DISPLAY_NAMES.get(
                self.l10n.get_target_locale(), self.l10n.get_target_locale()
            )
            self.l10n.generate_l10n_mod(
                self.result, Path(file_path), f"Generated L10n by ETS2ModManager"
            )
            self.status_label.setText(f"已导出 ({display_name_suffix}): {file_path}")
        except Exception as e:
            self.status_label.setText(f"导出失败: {e}")

    def reject(self):
        # Esc and the window close button must use the same safe shutdown path.
        self.close()

    def closeEvent(self, event):
        workers = [self._extract_thread, self._translate_thread]
        running = [w for w in workers if w is not None and w.isRunning()]
        if running:
            event.ignore()
            if not self._closing_for_workers:
                self._closing_for_workers = True
                self.setEnabled(False)
                self.status_label.setText("正在取消扫描，请稍候…")
                for worker in running:
                    stop = getattr(worker, "stop", None)
                    if stop:
                        stop()
            QTimer.singleShot(100, self._retry_close_after_workers)
            return
        super().closeEvent(event)

    def _retry_close_after_workers(self):
        workers = [self._extract_thread, self._translate_thread]
        if any(w is not None and w.isRunning() for w in workers):
            QTimer.singleShot(100, self._retry_close_after_workers)
            return
        self._closing_for_workers = False
        self.setEnabled(True)
        super().accept()
