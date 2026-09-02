"""main_window 辅助 Worker 线程（R10：全部继承 QThread；不再用 threading.Thread 后台线程）。

标准做法：继承 QThread、重写 run()、通过 Signal 与 UI 线程通信；closeEvent 可 quit()/wait()/terminate() 兜底。
threading.Lock / RLock 保留在各 service 内部，它们不是后台线程载体。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, Signal, QThread

from services.i18n_service import _, tr
from core.models import Mod


class _QuickScanWorker(QThread):
    """QThread：快速扫描所有模组（skip_manifest_parse=True），按文件逐个回调进度。"""

    progress_filename = Signal(str)        # 当前扫描的文件名（用于主进度条不定态阶段文案）
    result_ready = Signal(list, list)      # (mods_list, new_ids)
    failed = Signal(str)

    def __init__(self, scanner, parent=None):
        super().__init__(parent)
        self._scanner = scanner
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            from pathlib import Path
            scanner = self._scanner
            mi_index = scanner.load_mods_info_index()
            self._mi_index = mi_index
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
                    # 本地已有同名 mod：合并 Workshop 路径到已有记录，不创建重复条目
                    existing = mods_index[mod.mod_id]
                    existing._workshop_path = mod.package_path  # type: ignore[attr-defined]
                    existing._has_workshop_dup = True  # type: ignore[attr-defined]

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


class _AsyncParseWorker(QThread):
    """QThread：逐个解析加密包的 manifest/icon/description"""

    progress = Signal(int, int, str)   # (current_idx, total, mod_id)
    one_parsed = Signal(str)          # (mod_id)

    def __init__(self, pending_mods: list, paths, parent=None, mi_index: dict = None, max_workers: int = 4, worker_count_getter=None):
        super().__init__(parent)
        self._pending = pending_mods
        self._paths = paths
        self._mi_index = mi_index or {}
        self._stop = False
        self._max_workers = max(1, int(max_workers or 1))
        self._worker_count_getter = worker_count_getter

    def stop(self):
        self._stop = True

    def run(self):
        from core.mod_scanner import _build_mod_from_package, _enrich_nested_fallback
        from core.sii_parser import parse_mods_info
        from pathlib import Path as _P
        from concurrent.futures import ThreadPoolExecutor, as_completed
        # 复用已加载的 mi_index（避免重复读 mods_info.sii）
        mi_index = self._mi_index
        if not mi_index:
            try:
                if self._paths.mods_info_path and self._paths.mods_info_path.exists():
                    mi_index = parse_mods_info(str(self._paths.mods_info_path))
            except Exception:
                pass
        total = len(self._pending)

        def _parse_one(m):
            try:
                pp = _P(m.package_path)
                if pp.is_dir():
                    # 更可靠：原 package_type 在 Mod 对象上已有
                    ptype = m.package_type or "directory"
                elif pp.suffix.lower() == ".zip":
                    ptype = "zip"
                else:
                    ptype = "scs"
                # 异步阶段做完整解析（skip_nested=False），包含嵌套兜底
                parsed = _build_mod_from_package(pp, ptype, mi_index)
                # 回填到原 Mod 对象（只填空字段，保留原 mod_id 等不变）
                if parsed.manifest.display_name and not m.manifest.display_name:
                    m.manifest.display_name = parsed.manifest.display_name
                # 如果解析出了有效的 package_name，且当前是兜底值，则覆盖
                _PKG_BL = {"", "manifest", "package_name", "mods_info", "nameless", "mod_package"}
                cur_pkg = (m.manifest.package_name or "").strip()
                new_pkg = (parsed.manifest.package_name or "").strip()
                is_cur_fb = (cur_pkg == m.mod_id or not cur_pkg or cur_pkg.startswith(".") or cur_pkg.lower() in _PKG_BL)
                is_new_ok = (new_pkg and not new_pkg.startswith(".") and new_pkg.lower() not in _PKG_BL)
                if is_new_ok and is_cur_fb:
                    m.manifest.package_name = new_pkg
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
            except Exception:
                return m.mod_id
            return m.mod_id

        # Mod 包之间互不依赖；用少量工作线程并发读取，避免 500+ 个包
        # 在单线程中串行打开。线程数受控，降低机械盘/杀毒软件争用。
        completed = 0
        # 分批建立线程池：用户在启动页切换性能档位后，下一批立即采用新线程数。
        pos = 0
        while pos < total and not self._stop:
            try:
                requested = int(self._worker_count_getter()) if self._worker_count_getter else self._max_workers
            except Exception:
                requested = self._max_workers
            workers = min(max(1, requested), total - pos)
            batch = self._pending[pos:pos + max(8, workers * 4)]
            pos += len(batch)
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mod-meta") as pool:
                futures = [pool.submit(_parse_one, m) for m in batch]
                for fut in as_completed(futures):
                    if self._stop:
                        for pending in futures: pending.cancel()
                        break
                    try: mod_id = fut.result()
                    except Exception: mod_id = ""
                    completed += 1
                    if mod_id: self.one_parsed.emit(mod_id)
                    self.progress.emit(completed, total, mod_id)


class _WorkshopFetchWorker(QThread):
    """QThread：后台批量查询 Steam Workshop 标题，完成后触发刷新。"""

    fetch_done = Signal()

    def __init__(self, all_mods: list, save_cache: bool = True, parent=None):
        super().__init__(parent)
        self._mods = list(all_mods)   # 快照：避免主线程列表变动导致迭代异常
        self._save_cache = save_cache
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            from services.steam_workshop_service import fetch_and_fill_mods, get_cached_preview_url, get_cached_preview_bytes, save_preview_bytes
            from core.models import ModIcon
            import urllib.request
            fetch_and_fill_mods(
                self._mods,
                save_cache=self._save_cache,
                should_stop=lambda: self._stop,
            )
            # Workshop 目录通常没有 Steam 页面预览图，补读 API 返回的 preview_url。
            for m in self._mods:
                if self._stop:
                    break
                if getattr(m, "package_type", "") != "workshop" or getattr(m, "icon", None) is None:
                    continue
                if m.icon.is_available:
                    continue
                cached_bytes = get_cached_preview_bytes(str(getattr(m, "mod_id", "")))
                if cached_bytes:
                    m.icon = ModIcon(raw_bytes=cached_bytes, format="jpg", source_path="workshop-cache")
                    continue
                url = get_cached_preview_url(str(getattr(m, "mod_id", "")))
                if not url:
                    continue
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "ETS2ModManager/1.0"})
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        data = resp.read()
                    if data:
                        content_type = resp.headers.get("Content-Type", "")
                        save_preview_bytes(str(getattr(m, "mod_id", "")), data, content_type)
                        ext = ".png" if "png" in content_type.lower() else ".jpg"
                        m.icon = ModIcon(raw_bytes=data, format=ext[1:], source_path=url)
                except Exception:
                    continue
        except Exception:
            pass
        self.fetch_done.emit()


class _EnrichProfilesWorker(QThread):
    """QThread：后台逐个 enrich profile（解密 / 读 SII 回填 display_name/company_name）。"""

    one_enriched = Signal(str, str, object)   # (pid, label, profile) — profile 引用不变，主线程直接读已更新字段

    def __init__(self, profile_svc, profiles: list, parent=None):
        super().__init__(parent)
        self._svc = profile_svc
        self._profiles = list(profiles)      # 快照
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        for p in self._profiles:
            if self._stop:
                break
            try:
                ok = self._svc.enrich_profile(p)
            except Exception:
                ok = False
            if ok:
                pid = str(getattr(p, "profile_sii", "") or getattr(p, "profile_id", str(id(p))))
                # Keep the profile tree label identical to the game's custom
                # profile name; storage location and mod count belong in status text.
                name = (getattr(p, "display_name", "") or
                        getattr(p, "save_name", "") or
                        getattr(p, "company_name", "") or
                        getattr(p, "profile_id", "正在读取存档名称…"))
                count = int(getattr(p, "mod_count", 0) or 0)
                label = f"{name}（本地，已启用 {count} 个 Mod）"
                self.one_enriched.emit(pid, label, p)


class _ProfileReadWorker(QThread):
    """Read one profile's active_mods without blocking the Qt GUI thread."""

    result_ready = Signal(object, list, str, int)  # (profile, active_mods, error, token)

    def __init__(self, profile_svc, profile, token: int = 0, parent=None):
        super().__init__(parent)
        self._svc = profile_svc
        self._profile = profile
        self._token = int(token or 0)

    def run(self):
        try:
            active = self._svc.get_active_mods(self._profile)
            self.result_ready.emit(self._profile, list(active or []), "", self._token)
        except Exception as exc:
            self.result_ready.emit(self._profile, [], f"{type(exc).__name__}: {exc}", self._token)


def _build_progress_detail_zh(stage: str, name: str) -> str:
    n = (name or "").strip()
    if len(n) > 48:
        n = n[:48] + "..."
    return f"{stage}: {n}" if n else stage


_DECODE_CACHE: dict = {}

def _decode_text(b: bytes) -> str:
    # 缓存：如果之前探测成功过某个编码，先试它
    if _DECODE_CACHE.get("ok_enc"):
        try:
            return b.decode(_DECODE_CACHE["ok_enc"])
        except UnicodeDecodeError:
            pass
    for enc in ("utf-8-sig", "utf-8", "cp1252", "gbk", "latin-1"):
        try:
            result = b.decode(enc)
            _DECODE_CACHE["ok_enc"] = enc  # 记住成功的编码
            return result
        except UnicodeDecodeError:
            pass
    return b.decode("utf-8", errors="replace")
