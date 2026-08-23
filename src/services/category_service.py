"""
模组分类 / 已知模组追踪服务。
- 持久化：
  - assets/cache/known_mods.json  = {mod_id: {"category": "用户文件夹名", ...}}
  - assets/cache/user_folders.json = ["文件夹1", "文件夹2", ...]
- 分类文件夹由用户自行创建/重命名/删除
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple


_LOCK = threading.Lock()
_CACHE: Optional[Dict[str, Dict]] = None
_DIRTY = False

_FOLDER_LOCK = threading.Lock()
_FOLDER_CACHE: Optional[List[str]] = None
_FOLDER_DIRTY = False

UNCATEGORIZED_KEY = ""


def _base_dir() -> Path:
    here = Path(__file__).resolve().parent.parent.parent
    return here / "assets" / "cache"


def _cache_path() -> Path:
    folder = _base_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return folder / "known_mods.json"


def _folders_path() -> Path:
    folder = _base_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return folder / "user_folders.json"


def _load() -> Dict[str, Dict]:
    global _CACHE
    with _LOCK:
        if _CACHE is not None:
            return _CACHE
    cp = _cache_path()
    d: Dict[str, Dict] = {}
    if cp.exists():
        try:
            with cp.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                d = raw
        except (OSError, json.JSONDecodeError):
            d = {}
    with _LOCK:
        _CACHE = d
    return d


def save(force: bool = False) -> None:
    global _DIRTY
    with _LOCK:
        if _CACHE is None or (not _DIRTY and not force):
            return
        cp = _cache_path()
        try:
            tmp = cp.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(_CACHE, f, ensure_ascii=False, indent=2)
            os.replace(tmp, cp)
            _DIRTY = False
        except OSError:
            pass


def _load_folders() -> List[str]:
    global _FOLDER_CACHE
    with _FOLDER_LOCK:
        if _FOLDER_CACHE is not None:
            return _FOLDER_CACHE
    fp = _folders_path()
    folders: List[str] = []
    if fp.exists():
        try:
            with fp.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, list):
                folders = [s for s in raw if isinstance(s, str) and s]
        except (OSError, json.JSONDecodeError):
            folders = []
    with _FOLDER_LOCK:
        _FOLDER_CACHE = folders
    if _FOLDER_DIRTY:
        _save_folders()
    return folders


def _save_folders() -> None:
    global _FOLDER_DIRTY
    with _FOLDER_LOCK:
        if _FOLDER_CACHE is None or not _FOLDER_DIRTY:
            return
        fp = _folders_path()
        try:
            tmp = fp.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(_FOLDER_CACHE, f, ensure_ascii=False, indent=2)
            os.replace(tmp, fp)
            _FOLDER_DIRTY = False
        except OSError:
            pass


def all_folders() -> List[str]:
    return list(_load_folders())


def create_folder(name: str) -> bool:
    name = (name or "").strip()
    if not name:
        return False
    folders = _load_folders()
    if name in folders:
        return False
    folders.append(name)
    global _FOLDER_CACHE, _FOLDER_DIRTY
    with _FOLDER_LOCK:
        _FOLDER_CACHE = list(folders)
        _FOLDER_DIRTY = True
    _save_folders()
    return True


def rename_folder(old: str, new: str) -> int:
    old = (old or "").strip()
    new = (new or "").strip()
    if not old or not new or old == new:
        return 0
    folders = _load_folders()
    if new in folders:
        return -1
    if old not in folders:
        return 0
    idx = folders.index(old)
    folders[idx] = new
    global _FOLDER_CACHE, _FOLDER_DIRTY
    with _FOLDER_LOCK:
        _FOLDER_CACHE = list(folders)
        _FOLDER_DIRTY = True
    _save_folders()
    c = _load()
    global _DIRTY
    n = 0
    for mid, rec in c.items():
        if (rec or {}).get("category", "") == old:
            rec["category"] = new
            _DIRTY = True
            n += 1
    save()
    return n


def delete_folder(name: str) -> int:
    name = (name or "").strip()
    if not name:
        return 0
    folders = _load_folders()
    if name not in folders:
        return 0
    folders.remove(name)
    global _FOLDER_CACHE, _FOLDER_DIRTY
    with _FOLDER_LOCK:
        _FOLDER_CACHE = list(folders)
        _FOLDER_DIRTY = True
    _save_folders()
    c = _load()
    global _DIRTY
    n = 0
    for mid, rec in c.items():
        if (rec or {}).get("category", "") == name:
            rec["category"] = ""
            _DIRTY = True
            n += 1
    save()
    return n


def all_categories() -> List[str]:
    return all_folders()


def label_of(key: str) -> str:
    if not key:
        try:
            from services.i18n_service import _ as _tr
            return _tr("cat.uncategorized")
        except Exception:
            return "Uncategorized"
    return key


def get_record(mod_id: str) -> Optional[Dict]:
    if not mod_id:
        return None
    c = _load()
    rec = c.get(mod_id)
    return dict(rec) if isinstance(rec, dict) else None


def get_category(mod_id: str) -> str:
    rec = get_record(mod_id)
    return (rec or {}).get("category", "") or ""


def set_category(mod_id: str, category: str) -> None:
    if not mod_id:
        return
    c = _load()
    global _DIRTY
    rec = c.get(mod_id) or {}
    if rec.get("category", "") != category:
        rec["category"] = category or ""
        _DIRTY = True
    rec["last_seen"] = int(time.time())
    rec.setdefault("first_seen", rec["last_seen"])
    c[mod_id] = rec


def set_categories_bulk(mapping: Dict[str, str]) -> None:
    if not mapping:
        return
    c = _load()
    global _DIRTY
    now = int(time.time())
    for mid, cat in mapping.items():
        if not mid:
            continue
        rec = c.get(mid) or {}
        if rec.get("category", "") != (cat or ""):
            rec["category"] = cat or ""
            _DIRTY = True
        rec["last_seen"] = now
        rec.setdefault("first_seen", now)
        c[mid] = rec


def touch_and_detect_new(
    scanned_ids: Iterable[str],
    name_hints: Optional[Dict[str, str]] = None,
) -> Tuple[List[str], List[str]]:
    c = _load()
    global _DIRTY
    now = int(time.time())
    name_hints = name_hints or {}
    new_ids: List[str] = []
    known_ids: List[str] = []
    for mid in scanned_ids:
        if not mid:
            continue
        existed = mid in c
        if existed:
            rec = c[mid]
            rec["last_seen"] = now
            hnt = name_hints.get(mid)
            if hnt and rec.get("name_hint") != hnt:
                rec["name_hint"] = hnt
                _DIRTY = True
            known_ids.append(mid)
        else:
            rec: Dict = {
                "category": "",
                "first_seen": now,
                "last_seen": now,
            }
            hnt = name_hints.get(mid)
            if hnt:
                rec["name_hint"] = hnt
            c[mid] = rec
            _DIRTY = True
            new_ids.append(mid)
    save()
    return new_ids, known_ids


def name_hint_of(mod_id: str) -> str:
    rec = get_record(mod_id)
    return (rec or {}).get("name_hint", "") or ""


def stats() -> Dict[str, int]:
    c = _load()
    out: Dict[str, int] = {UNCATEGORIZED_KEY: 0}
    for fname in _load_folders():
        out[fname] = 0
    for rec in c.values():
        cat = (rec or {}).get("category", "") or ""
        if cat in out:
            out[cat] += 1
        elif cat:
            out[cat] = out.get(cat, 0) + 1
        else:
            out[UNCATEGORIZED_KEY] += 1
    return out


def mods_in_category(category_key: str) -> Set[str]:
    c = _load()
    if category_key == UNCATEGORIZED_KEY:
        return {mid for mid, rec in c.items()
                if not (rec or {}).get("category", "")}
    return {mid for mid, rec in c.items()
            if (rec or {}).get("category", "") == category_key}
