"""Auto-split MainWindow Mixin（方法级拆分；不持有独立状态，仅把 MainWindow 方法按功能归档）。

Mixin 类本身不做 __init__ / 不 super()，所有 self.xxx 属性都来自 MainWindow 实例自身（已在 MainWindow.__init__ 中初始化）。
唯一注意：closeEvent 位于 _SignalMixin 中，其末尾会直接调用 `QMainWindow.closeEvent(self, event)` 跳过 MRO。
"""
from __future__ import annotations
from core.scs_archive import ScsArchiveReader

from services.i18n_service import _, tr, I18nNotifier, set_language, current_language, available_languages, language_display_name
from .._mw_widgets import _LangSwitchDialog, SplashScreen, ModTable
from services.profile_service import _decode_text


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

from services.i18n_service import _, tr
from core.models import Mod, ModIcon


class _DialogMixin:
    @staticmethod
    def _detail_archive_fallback_allowed(mod: Optional[Mod]) -> bool:
        """Avoid synchronous extractor work while the user browses rows.

        Keyboard navigation must stay responsive. Encrypted HashFS/AEM and
        Workshop directories are handled by background workers; opening them
        from the detail pane would otherwise block the GUI for seconds.
        """
        if mod is None:
            return False
        if getattr(mod, "package_type", "") == "workshop":
            return False
        try:
            from services.session_service import load_mod_icon_probe
            if not mod.icon.is_available and load_mod_icon_probe(mod.mod_id, mod.last_modified) is False:
                return False
        except Exception:
            pass
        try:
            pp = Path(mod.package_path)
            if pp.is_file():
                with pp.open("rb") as f:
                    if f.read(4) in (b"SCS#", b"AEM!"):
                        return False
                # Large archives can still be expensive to inspect even when
                # they are not encrypted. Let the background parser populate
                # their icon instead of blocking every arrow-key selection.
                if not mod.icon.is_available and int(getattr(mod, "file_size", 0) or 0) > 64 * 1024 * 1024:
                    return False
        except OSError:
            pass
        return True

    def _show_folder_detail(self, folder: str) -> None:
        """Show a compact folder summary in the detail pane."""
        members = []
        for entry in getattr(self, "current_worklist", []) or []:
            pkg = str(entry.get("package_name") or "").strip()
            if not pkg:
                continue
            mod = self._lookup_mod(pkg) or entry.get("mod")
            try:
                cat = self._category_tag_for_entry({"package_name": pkg}, mod)
            except Exception:
                cat = ""
            if cat != folder:
                continue
            title = (mod.display_title if mod else "") or pkg
            state = "已启用" if entry.get("enabled") else "未启用"
            members.append(f"{state}  ·  {title}")
        self.lbl_title.setText(f"文件夹：{folder}")
        self.lbl_meta.setText(f"自定义文件夹  ·  {len(members)} 个 Mod")
        self.preview.clear()
        self.preview.setText("文件夹")
        self.preview.setStyleSheet("background:#151b2b; color:#7aa2f7; border:1px solid #3b4f7a; border-radius:8px;")
        self.txt_desc.setPlainText("\n".join(members) if members else "文件夹内暂无 Mod")

    def _show_mod_detail(self, pkg: str, mod: Optional[Mod], hint: Optional[dict] = None):
        hint = hint or {}
        # 标题
        display_title = (mod.display_title if mod else "") or hint.get("display") or pkg or _("detail.none")
        version = (mod.manifest.package_version if mod else "") or hint.get("version") or _("detail.ver_notag")
        self.lbl_title.setText(_("ui.title_with_version", title=display_title, version=version))
        # 标题颜色：缺失 mod 标红
        if mod is None:
            txt = self.lbl_title.text()
            marker = " [⚠️ 文件丢失]"
            if marker not in txt:
                self.lbl_title.setText(txt + marker)
            from PySide6.QtGui import QPalette
            pal = self.lbl_title.palette()
            pal.setColor(QPalette.WindowText, QColor("#ef4444"))
            self.lbl_title.setPalette(pal)
        else:
            from PySide6.QtGui import QPalette
            self.lbl_title.setPalette(self.style().standardPalette())
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
            # Workshop 页面预览图不一定存在于本地内容目录；优先读取已缓存的
            # Steam 预览图，避免等待后台标题/图片线程完成后详情仍显示空白。
            if not pix and mod.package_type == "workshop":
                try:
                    from services.steam_workshop_service import get_cached_preview_bytes
                    cached = get_cached_preview_bytes(str(mod.mod_id))
                    if cached:
                        img = QImage.fromData(QByteArray(cached))
                        if not img.isNull(): pix = QPixmap.fromImage(img)
                except Exception:
                    pass
            if not pix and self._detail_archive_fallback_allowed(mod):
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
                        icon_names = []
                        if mod.manifest.icon_filename:
                            icon_names.append(mod.manifest.icon_filename.replace("\\", "/"))
                        icon_names += ["mod_icon.jpg", "mod_icon.png", "icon.jpg", "icon.png", "preview.jpg"]
                        for ic_name in dict.fromkeys(icon_names):
                            icon_bytes = rdr.read_bytes(ic_name)
                            if icon_bytes and len(icon_bytes) > 100:
                                img = QImage.fromData(QByteArray(icon_bytes))
                                if not img.isNull():
                                    pix = QPixmap.fromImage(img)
                                    mod.icon = ModIcon(
                                        raw_bytes=icon_bytes,
                                        format=Path(ic_name).suffix.lstrip(".") or "jpg",
                                        source_path=ic_name,
                                    )
                                    try:
                                        from services.session_service import save_mod_icon_cache
                                        save_mod_icon_cache(mod.mod_id, mod.last_modified, icon_bytes, mod.icon.format)
                                    except Exception:
                                        pass
                                    break
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
                    icon_names = ["mod_icon.jpg", "mod_icon.png", "icon.jpg", "icon.png", "preview.jpg"]
                    if mod.manifest.icon_filename:
                        icon_names.insert(0, mod.manifest.icon_filename)
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
                                                mod.icon = ModIcon(
                                                    raw_bytes=data,
                                                    format=icp.suffix.lstrip(".") or "jpg",
                                                    source_path=str(icp),
                                                )
                                                try:
                                                    from services.session_service import save_mod_icon_cache
                                                    save_mod_icon_cache(mod.mod_id, mod.last_modified, data, mod.icon.format)
                                                except Exception:
                                                    pass
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
            elif desc and not mod.description:
                mod.description = desc
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
