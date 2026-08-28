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
        # Fast first-level scan. os.scandir avoids repeated Path conversions;
        # cap entries so a damaged/network-mounted directory cannot block the
        # startup splash indefinitely.
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    if count >= 10000:
                        break
                    try:
                        st = entry.stat(follow_symlinks=False)
                        count += 1
                        if entry.is_file(follow_symlinks=False):
                            total += st.st_size
                        if st.st_mtime > mtime_max:
                            mtime_max = st.st_mtime
                    except OSError:
                        continue
        except OSError:
            # Treat an unreadable directory as present but empty; the real scan
            # will report any accessible mods instead of freezing startup.
            pass
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
FAST_RESTORE_WINDOW_SECONDS = 2 * 60 * 60


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
    metadata_ready: Optional[bool] = None,
) -> None:
    """退出时调用：保存当前会话状态 + 可选快速扫描快照（避免启动重扫）。"""
    global _CACHE
    # 关闭窗口时通常只更新 profile 状态；必须保留此前完整扫描得到的
    # 模组快照，否则下一次启动会无端退化成完整初始化。
    previous = load_last_session() or {}
    data = {
        "scan_time": datetime.now().isoformat() if mods_snapshot is not None else previous.get("scan_time", datetime.now().isoformat()),
        "all_mod_ids": list(all_mod_ids),
        "profiles": profiles_state,
    }
    if mods_snapshot is not None or previous.get("mods_snapshot"):
        data["mods_snapshot"] = mods_snapshot if mods_snapshot is not None else previous.get("mods_snapshot")
    if dir_signatures is not None or previous.get("dir_signatures"):
        data["dir_signatures"] = dir_signatures if dir_signatures is not None else previous.get("dir_signatures")
    if metadata_ready is not None or "metadata_ready" in previous:
        data["metadata_ready"] = bool(metadata_ready) if metadata_ready is not None else bool(previous.get("metadata_ready"))
    sp = _session_path()
    try:
        tmp = sp.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, sp)
        with _LOCK:
            _CACHE = data
    except (OSError, TypeError, ValueError, UnicodeEncodeError) as _e1:
        # 除磁盘/权限问题外，还要防 mods_snapshot 含 bytes/Path/datetime 等不可序列化字段
        # 降级策略：先丢大对象 mods_snapshot，再丢 dir_signatures，尽最大可能保留会话
        import sys as _sys
        print(f"[session] 首次保存失败: {type(_e1).__name__}: {_e1}；尝试降级写入", file=_sys.stderr)
        _fallbacks = [
            ("mods_snapshot", "丢弃扫描快照（可重扫恢复）"),
            ("dir_signatures", "丢弃签名校验（可重扫恢复）"),
        ]
        _data2 = dict(data)
        for _key, _reason in _fallbacks:
            if _key in _data2:
                del _data2[_key]
                print(f"[session]   - {_reason}: {_key}", file=_sys.stderr)
                try:
                    tmp = sp.with_suffix(".tmp")
                    with tmp.open("w", encoding="utf-8") as f:
                        json.dump(_data2, f, ensure_ascii=False, indent=2)
                    os.replace(tmp, sp)
                    with _LOCK:
                        _CACHE = _data2
                    return   # 降级成功
                except (OSError, TypeError, ValueError, UnicodeEncodeError) as _e2:
                    print(f"[session]   降级仍失败: {type(_e2).__name__}: {_e2}；继续下一级", file=_sys.stderr)
        # 所有降级都失败，打印错误，不抛（不影响关窗主流程）
        print(f"[session] 所有降级策略均失败，放弃本次会话保存", file=_sys.stderr)


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
    snap = data.get("mods_snapshot")
    if not isinstance(snap, list) or len(snap) == 0:
        return None
    # 短时间内重复启动时直接复用刚刚完成的快照，避免再次遍历数百个
    # Workshop 目录和计算目录签名。路径仍需一致，超过窗口则走严格校验。
    if is_recent_scan_snapshot(data):
        saved_sigs = data.get("dir_signatures") or {}
        expected = {
            "mod_dir": str(mod_dir) if mod_dir else "",
            "workshop_dir": str(workshop_dir) if workshop_dir else "",
            "mods_info_path": str(mods_info_path) if mods_info_path else "",
        }
        if all(str((saved_sigs.get(k) or {}).get("path", "")) == v for k, v in expected.items()) and _snapshot_entries_unchanged(snap):
            return snap
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
    # 校验字段存在性（防御性）
    req_keys = {"mod_id", "package_path", "package_type", "file_size", "last_modified"}
    for m in snap:
        if not isinstance(m, dict) or not req_keys.issubset(m.keys()):
            return None
    return snap


def _snapshot_entries_unchanged(snap: List[dict]) -> bool:
    """快速逐条 stat 校验：不读压缩包内容，只确认已缓存的包没有被替换。"""
    for item in snap:
        try:
            path = Path(str(item.get("package_path") or ""))
            st = path.stat()
            old_mtime = float(item.get("last_modified") or 0.0)
            if abs(st.st_mtime - old_mtime) > 0.001:
                return False
            if path.is_file() and int(st.st_size) != int(item.get("file_size") or 0):
                return False
        except OSError:
            return False
    return True


def is_recent_scan_snapshot(data: Optional[dict] = None) -> bool:
    """判断扫描快照是否在短时快速恢复窗口内。"""
    if data is None:
        data = load_last_session()
    if not isinstance(data, dict) or not data.get("mods_snapshot"):
        return False
    try:
        ts = datetime.fromisoformat(str(data.get("scan_time", ""))).timestamp()
        return 0 <= (datetime.now().timestamp() - ts) <= FAST_RESTORE_WINDOW_SECONDS
    except (TypeError, ValueError, OverflowError):
        return False


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
