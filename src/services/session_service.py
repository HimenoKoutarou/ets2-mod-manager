"""
会话状态服务：保存/加载上次会话的模组状态。
- 退出时保存：全部 mod_id 列表 + 每个存档的 active_mods 列表
- 下次打开：对比检测新增模组
- 切换存档：对比该存档的 active_mods 变化
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def _dir_signature(path: Optional[Path]) -> dict:
    """目录快速签名：文件总数+总大小+最大mtime，用于判断目录是否变化"""
    sig: dict = {"path": str(path) if path else "", "exists": False}
    if path is None or not path.exists():
        return sig
    try:
        if path.is_file():
            st = path.stat()
            return {
                "path": str(path),
                "exists": True,
                "is_file": True,
                "count": 1,
                "total_size": st.st_size,
                "mtime": st.st_mtime,
            }
        count = 0
        total = 0
        mtime_max = 0.0
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    count += 1
                    st = p.stat()
                    total += st.st_size
                    if st.st_mtime > mtime_max:
                        mtime_max = st.st_mtime
                    # 只统计第一层 .scs/.zip/子目录数量即可？不，rglob 全量更准
            except OSError:
                continue
        sig.update({
            "exists": True,
            "is_file": False,
            "count": count,
            "total_size": total,
            "mtime_max": mtime_max,
        })
    except OSError:
        pass
    return sig


def _sig_equal(a: dict, b: dict) -> bool:
    if bool(a.get("exists")) != bool(b.get("exists")):
        return False
    if not a.get("exists"):
        return True
    if a.get("path") != b.get("path"):
        return False
    if a.get("is_file") != b.get("is_file"):
        return False
    if a.get("is_file"):
        return (a.get("count") == b.get("count")
                and a.get("total_size") == b.get("total_size")
                and a.get("mtime") == b.get("mtime"))
    return (a.get("count") == b.get("count")
            and a.get("total_size") == b.get("total_size")
            and a.get("mtime_max") == b.get("mtime_max"))


_LOCK = threading.Lock()
_CACHE: Optional[dict] = None


def _base_dir() -> Path:
    here = Path(__file__).resolve().parent.parent.parent
    return here / "assets" / "cache"


def _session_path() -> Path:
    folder = _base_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return folder / "last_session.json"


def save_session_state(
    all_mod_ids: List[str],
    profiles_state: Dict[str, Dict],
    mods_snapshot: Optional[List[dict]] = None,
    dir_signatures: Optional[Dict[str, dict]] = None,
) -> None:
    """退出时调用：保存当前会话状态 + 可选快速扫描快照（避免启动重扫）。"""
    data = {
        "scan_time": datetime.now().isoformat(),
        "all_mod_ids": list(all_mod_ids),
        "profiles": profiles_state,
    }
    if mods_snapshot is not None:
        data["mods_snapshot"] = mods_snapshot
    if dir_signatures is not None:
        data["dir_signatures"] = dir_signatures
    sp = _session_path()
    try:
        tmp = sp.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, sp)
        global _CACHE
        with _LOCK:
            _CACHE = data
    except OSError:
        pass


def load_last_session() -> Optional[dict]:
    """加载上次会话状态。返回 dict 或 None。"""
    global _CACHE
    with _LOCK:
        if _CACHE is not None:
            return _CACHE
    sp = _session_path()
    if not sp.exists():
        return None
    try:
        with sp.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            with _LOCK:
                _CACHE = data
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return None


def load_scan_snapshot(
    mod_dir: Optional[Path],
    workshop_dir: Optional[Path],
    mods_info_path: Optional[Path],
) -> Optional[List[dict]]:
    """
    启动时尝试恢复上次快速扫描结果：
    - 校验 mod_dir / workshop_dir / mods_info_path 当前签名是否与保存时一致
    - 一致则返回 mods_snapshot（list of dict，每个 dict 可直接构造 Mod 对象）
    - 不一致或不存在返回 None，走真实扫描
    """
    data = load_last_session()
    if not data:
        return None
    saved_sigs = data.get("dir_signatures") or {}
    if not saved_sigs:
        return None
    # 实际计算当前签名
    cur = {
        "mod_dir": _dir_signature(mod_dir),
        "workshop_dir": _dir_signature(workshop_dir),
        "mods_info_path": _dir_signature(mods_info_path),
    }
    for k in cur.keys():
        if not _sig_equal(cur[k], saved_sigs.get(k) or {}):
            return None
    snap = data.get("mods_snapshot")
    if not isinstance(snap, list) or len(snap) == 0:
        return None
    # 校验字段存在性（防御性）
    req_keys = {"mod_id", "package_path", "package_type", "file_size", "last_modified"}
    for m in snap:
        if not isinstance(m, dict) or not req_keys.issubset(m.keys()):
            return None
    return snap


def get_new_mod_ids_vs_last_session(current_all_ids: List[str]) -> List[str]:
    """对比全部 mod_id：返回当前有但上次会话没有的 mod_id 列表。"""
    last = load_last_session()
    if not last:
        return list(current_all_ids)
    last_ids = set(last.get("all_mod_ids", []))
    return [mid for mid in current_all_ids if mid not in last_ids]


def get_new_active_in_profile(
    profile_id: str,
    current_active_ids: List[str],
) -> List[str]:
    """对比存档的 active_mods：返回当前有但上次会话该存档没有的 mod_id 列表。"""
    last = load_last_session()
    if not last:
        return list(current_active_ids)
    profiles = last.get("profiles", {})
    prof_state = profiles.get(profile_id, {})
    last_active = set(prof_state.get("active_mods", []))
    return [mid for mid in current_active_ids if mid not in last_active]
