"""
外部解包工具服务

使用以下社区开源工具解密 ETS2 加密模组包：
  - Extractor (extractor.exe) — sk-zk
    https://github.com/sk-zk/Extractor
    (打包分发于 CracksVault Ultimate SCS Unpacker Kit v1.1.2)
  - SXC Extractor (sxc64.exe) — madman271
    https://forum.scssoft.com/viewtopic.php?t=276948

用 extractor.exe / sxc64.exe 解 SCS# (HashFS) / AEM! / 加密 ZIP 格式的 mod 包，
提取 manifest.sii（及按需提取 icon/description）。

工具分工（与 DROP_SCS_HERE.bat 的 magic 分流一致）：
  SCS# (53435323) -> extractor.exe --deep --partial=/manifest.sii
  AEM! (41454D21) -> sxc64.exe -f manifest.sii
  PK   (504B0304) -> 若加密则 sxc64.exe，否则由 ScsArchiveReader 的 zipfile 处理

性能策略（单文件提取，避免全量解包大包耗时）：
  - extract_manifest_text: 只提取 manifest.sii，磁盘缓存 30 天 TTL
  - extract_file_bytes: 只提取目标文件，进程内缓存（同进程内不重复提取同文件）

Credit: 感谢 CracksVault 和 Truck Tools/sxc 项目的作者，
以及 Euro Truck Simulator 2 社区所有逆向工程贡献者。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess

# ---------------- subprocess: never allocate console window (GUI build) ---------------
import sys as _sys
if _sys.platform == "win32":
    _CREATE_NO_WINDOW = 0x08000000
    _sp_si = subprocess.STARTUPINFO()
    _sp_si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _SP_HIDE = _CREATE_NO_WINDOW
    _SP_STARTUPINFO = _sp_si
else:
    _SP_HIDE = 0
    _SP_STARTUPINFO = None
# -----------------------------------------------------------------------------------
import threading
import tempfile
import time
from pathlib import Path
from typing import Optional

MAGIC_SCS = b"SCS#"
MAGIC_AEM = b"AEM!"

_TOOLS_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "tools"
_EXTRACTOR = _TOOLS_DIR / "extractor.exe"
_SXC = _TOOLS_DIR / "sxc64.exe"
_CACHE_PATH = _TOOLS_DIR.parent / "cache" / "manifest_cache.json"

_lock = threading.Lock()
_cache: Optional[dict] = None
_TTL_SECONDS = 30 * 86400
_TIMEOUT_SECONDS = 15

# 进程内缓存：单文件字节缓存，避免同进程重复提取同一文件
# key = (cache_key(path), inner_name)，value = bytes or None（None 表示确认不存在）
_file_bytes_cache: dict = {}
_file_bytes_lock = threading.Lock()

# 磁盘文件缓存目录：extracted/{cache_key}/{filename_hash} -> 实际文件内容
_DISK_CACHE_DIR = _TOOLS_DIR.parent / "cache" / "extracted"
_disk_cache_lock = threading.Lock()


def _disk_cache_path(cache_key: str, inner_name: str) -> Path:
    """返回磁盘缓存文件路径。"""
    import hashlib
    safe_name = hashlib.md5(inner_name.encode("utf-8")).hexdigest()[:16]
    return _DISK_CACHE_DIR / cache_key / safe_name


def _disk_cache_meta(cache_key: str) -> Path:
    """返回磁盘缓存元数据文件路径（记录提取时间戳）。"""
    return _DISK_CACHE_DIR / cache_key / "_meta.json"


def _disk_cache_get(cache_key: str, inner_name: str) -> Optional[bytes]:
    """从磁盘缓存读取单个文件字节，TTL 未过期则返回 bytes，否则 None。"""
    fp = _disk_cache_path(cache_key, inner_name)
    mp = _disk_cache_meta(cache_key)
    if not fp.exists():
        return None
    try:
        if mp.exists():
            import json
            meta = json.loads(mp.read_text(encoding="utf-8"))
            if time.time() - meta.get("ts", 0) >= _TTL_SECONDS:
                return None  # 过期
    except Exception:
        pass
    try:
        return fp.read_bytes()
    except OSError:
        return None


def _disk_cache_put(cache_key: str, inner_name: str, data: bytes) -> None:
    """写入磁盘缓存（仅非空数据）。"""
    if not data:
        return
    fp = _disk_cache_path(cache_key, inner_name)
    mp = _disk_cache_meta(cache_key)
    with _disk_cache_lock:
        try:
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_bytes(data)
            # 更新元数据时间戳
            import json
            meta = {"ts": time.time()}
            mp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


def _detect_magic(path: Path) -> str:
    try:
        with open(path, "rb") as f:
            head = f.read(4)
    except OSError:
        return "unknown"
    if head == MAGIC_SCS:
        return "scs_hashfs"
    if head == MAGIC_AEM:
        return "aem"
    if head[:2] == b"PK":
        return "zip"
    return "unknown"


def _is_zip_encrypted(path: Path) -> bool:
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            return any(info.flag_bits & 0x1 for info in z.infolist())
    except Exception:
        return True


def _cache_key(path: Path) -> str:
    try:
        st = path.stat()
        raw = f"{path}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        raw = str(path)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _load_cache() -> dict:
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        if _CACHE_PATH.exists():
            try:
                _cache = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
                if not isinstance(_cache, dict):
                    _cache = {}
            except Exception:
                _cache = {}
        else:
            _cache = {}
        return _cache


def _save_cache() -> None:
    with _lock:
        if _cache is None:
            return
        try:
            _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = _CACHE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(_cache, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, _CACHE_PATH)
        except OSError:
            pass



def _sp_run(cmd_args, /, *, capture_output=True, timeout=None, input=None, env=None, cwd=None,
            shell=False, stdout=None, stderr=None):
    """Hardened subprocess runner: on Windows always suppresses console window creation.

    Rationale: PyInstaller console=False + subprocess.run([...console-mode binary...])
    without CREATE_NO_WINDOW briefly allocates a per-child console -> black cmd flash.
    Even if a future contributor adds new extractor calls and forgets per-call flags,
    routing via this helper guarantees popups remain eliminated. Matches the flags used
    in commit c29f330 at all 6 manual call sites.
    """
    kwargs: dict = {}
    if _SP_HIDE:
        kwargs["creationflags"] = _SP_HIDE
    if _SP_STARTUPINFO is not None:
        kwargs["startupinfo"] = _SP_STARTUPINFO
    if capture_output is not None:
        kwargs["capture_output"] = capture_output
    if timeout is not None:
        kwargs["timeout"] = timeout
    if input is not None:
        kwargs["input"] = input
    if env is not None:
        kwargs["env"] = env
    if cwd is not None:
        kwargs["cwd"] = cwd
    if shell:
        kwargs["shell"] = True
    if stdout is not None:
        kwargs["stdout"] = stdout
    if stderr is not None:
        kwargs["stderr"] = stderr
    return subprocess.run(list(cmd_args), **kwargs)


def _run_extractor(scs_path: Path, dest: Path, partial: str = "/manifest.sii") -> bool:
    """用 extractor.exe 提取 SCS# 包内指定路径的文件。partial 用 / 开头的绝对路径。"""
    if not _EXTRACTOR.exists():
        return False
    try:
        result = subprocess.run(
            [str(_EXTRACTOR), str(scs_path), "--deep", f"--partial={partial}",
             "-d", str(dest), "-s"],
            capture_output=True, timeout=_TIMEOUT_SECONDS,
            creationflags=_SP_HIDE, startupinfo=_SP_STARTUPINFO,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _run_sxc(archive_path: Path, dest: Path, filename: str = "manifest.sii") -> bool:
    """用 sxc64.exe 提取 AEM!/加密ZIP 包内指定文件名的文件。"""
    if not _SXC.exists():
        return False
    try:
        result = subprocess.run(
            [str(_SXC), str(archive_path), "-o", str(dest), "-f", filename, "-q"],
            capture_output=True, timeout=_TIMEOUT_SECONDS,
            creationflags=_SP_HIDE, startupinfo=_SP_STARTUPINFO,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _extract_single_file(path: Path, inner_name: str, dest: Path) -> bool:
    """按 magic 选工具提取单个文件到 dest 目录。"""
    magic = _detect_magic(path)
    partial = inner_name if inner_name.startswith("/") else "/" + inner_name
    if magic == "scs_hashfs":
        return _run_extractor(path, dest, partial=partial)
    if magic == "aem":
        return _run_sxc(path, dest, filename=Path(inner_name).name)
    if magic == "zip":
        # 加密 ZIP 或 zipfile 打不开的 ZIP，交给 sxc
        return _run_sxc(path, dest, filename=Path(inner_name).name)
    return False


def extract_manifest_text(archive_path) -> Optional[str]:
    """提取 manifest.sii 文本，带磁盘缓存（30 天 TTL）。返回文本或 None。"""
    path = Path(archive_path)
    if not path.is_file():
        return None
    key = _cache_key(path)
    cache = _load_cache()
    entry = cache.get(key)
    if isinstance(entry, dict):
        if time.time() - entry.get("ts", 0) < _TTL_SECONDS:
            # 空结果也返回（避免超时包每次重新提取）
            return entry.get("manifest_text") or None

    # P1 修复：用 mkdtemp 替代固定目录，避免并发竞争
    tmp = Path(tempfile.mkdtemp(prefix="ets2mm_mf_"))

    text: Optional[str] = None
    extract_ok = _extract_single_file(path, "manifest.sii", tmp)
    if extract_ok:
        for p in tmp.rglob("manifest.sii"):
            try:
                text = p.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                text = None
            break

    magic = _detect_magic(path)
    # R11: extractor failed = do NOT cache (avoid 30-day negative cache on temp failure)
    if extract_ok:
        cache[key] = {"manifest_text": text or "", "ts": time.time(), "magic": magic}
        _save_cache()
    shutil.rmtree(tmp, ignore_errors=True)
    return text


def extract_file_bytes(archive_path, inner_name: str) -> Optional[bytes]:
    """提取单个文件字节（icon/description）。进程内缓存 + 磁盘文件缓存（30天 TTL）。"""
    path = Path(archive_path)
    if not path.is_file():
        return None
    key = _cache_key(path)
    cache_key = (key, inner_name)

    # 1) 进程内缓存
    with _file_bytes_lock:
        if cache_key in _file_bytes_cache:
            return _file_bytes_cache[cache_key]

    # 2) 磁盘文件缓存
    disk_data = _disk_cache_get(key, inner_name)
    if disk_data is not None:
        with _file_bytes_lock:
            _file_bytes_cache[cache_key] = disk_data
        return disk_data

    # P1 修复：用 mkdtemp 替代固定目录，避免并发竞争
    tmp = Path(tempfile.mkdtemp(prefix="ets2mm_f_"))

    data: Optional[bytes] = None
    if _extract_single_file(path, inner_name, tmp):
        target = Path(inner_name).name
        for p in tmp.rglob(target):
            try:
                b = p.read_bytes()
                if b:
                    data = b
                    break
            except OSError:
                continue

    shutil.rmtree(tmp, ignore_errors=True)

    # 4) 写入磁盘 + 进程内缓存
    if data:
        _disk_cache_put(key, inner_name, data)
    with _file_bytes_lock:
        _file_bytes_cache[cache_key] = data
    return data


def extract_files_batch(archive_path, inner_names) -> dict:
    """批量提取多个文件，返回 {inner_name: bytes or None}。
    SCS# 用 --partial 多路径一次提取（耗时与单文件相同）；
    AEM!/ZIP 逐个提取（有进程内缓存，不重复）。
    磁盘缓存空结果标记，避免超时包重复提取。
    """
    path = Path(archive_path)
    if not path.is_file():
        return {}
    # 去重 + 保留顺序
    seen = set()
    names = []
    for n in inner_names:
        if n and n not in seen:
            seen.add(n)
            names.append(n)
    if not names:
        return {}

    key = _cache_key(path)
    # Do not short-circuit on a previous empty batch. Candidate paths may have
    # expanded (for example encrypted Workshop mods with nested icons).
    cache = _load_cache()
    entry = cache.get(key)
    # A previous batch_empty marker is informational only; retry extraction.

    # 磁盘文件缓存预检查：如果所有候选都能从磁盘缓存命中，直接返回
    disk_hit: dict = {}
    all_hit = True
    for n in names:
        d = _disk_cache_get(key, n)
        if d is not None:
            disk_hit[n] = d
            # 同步到进程内缓存
            ck = (key, n)
            with _file_bytes_lock:
                if ck not in _file_bytes_cache:
                    _file_bytes_cache[ck] = d
        else:
            all_hit = False
            break
    if all_hit and disk_hit:
        return disk_hit

    magic = _detect_magic(path)
    result: dict = {}

    if magic == "scs_hashfs" and _EXTRACTOR.exists():
        # SCS#: 用 --partial 多路径一次提取所有候选
        key = _cache_key(path)
        partials = []
        for n in names:
            p = n if n.startswith("/") else "/" + n
            partials.append(p)
        multi = ",".join(partials)

        # R11.1: mkdtemp replaces fixed dir (race condition fix)
        tmp = Path(tempfile.mkdtemp(prefix="ets2mm_b_"))

        ok = False
        try:
            proc = subprocess.run(
                [str(_EXTRACTOR), str(path), "--deep", f"--partial={multi}",
                 "-d", str(tmp), "-s"],
                capture_output=True, timeout=_TIMEOUT_SECONDS,
                creationflags=_SP_HIDE, startupinfo=_SP_STARTUPINFO,
            )
            # R11.1: check returncode
            ok = proc.returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            ok = False

        if ok:
            for n in names:
                target = Path(n).name
                data = None
                for p in tmp.rglob(target):
                    try:
                        b = p.read_bytes()
                        if b:
                            data = b
                            break
                    except OSError:
                        continue
                result[n] = data
                # 写入进程内缓存 + 磁盘缓存
                ck = (key, n)
                with _file_bytes_lock:
                    if ck not in _file_bytes_cache:
                        _file_bytes_cache[ck] = data
                if data:
                    _disk_cache_put(key, n, data)
        shutil.rmtree(tmp, ignore_errors=True)
        return result

    # AEM!/ZIP: 逐个提取（有进程内缓存）
    for n in names:
        result[n] = extract_file_bytes(path, n)
    return result


def supports_archive(archive_path) -> bool:
    """该文件是否可由本服务处理（SCS# / AEM! / 加密 ZIP / zipfile 打不开的 ZIP）。"""
    path = Path(archive_path)
    if not path.is_file():
        return False
    magic = _detect_magic(path)
    if magic in ("scs_hashfs", "aem"):
        return True
    if magic == "zip":
        # 加密 ZIP 或 zipfile 打不开的 ZIP（ModGuard/header 损坏），都交给 sxc
        try:
            import zipfile as _zf
            with _zf.ZipFile(path):
                pass
            return _is_zip_encrypted(path)
        except Exception:
            return True
    return False
