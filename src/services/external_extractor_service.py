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
_ENTRY_CACHE_PATH = _TOOLS_DIR.parent / "cache" / "external_entries_cache.json"

_lock = threading.Lock()
_cache: Optional[dict] = None
_TTL_SECONDS = 30 * 86400
_TIMEOUT_SECONDS = 15
_ENTRY_CACHE_TTL_SECONDS = 90 * 86400

# 进程内缓存：单文件字节缓存，避免同进程重复提取同一文件
# key = (cache_key(path), inner_name)，value = bytes or None（None 表示确认不存在）
_file_bytes_cache: dict = {}
_file_bytes_lock = threading.Lock()
_first_image_cache: dict = {}

# 磁盘文件缓存目录：extracted/{cache_key}/{filename_hash} -> 实际文件内容
_DISK_CACHE_DIR = _TOOLS_DIR.parent / "cache" / "extracted"
_L10N_TREE_CACHE_DIR = _TOOLS_DIR.parent / "cache" / "l10n_tree"
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


def _load_entry_cache() -> dict:
    try:
        if _ENTRY_CACHE_PATH.exists():
            value = json.loads(_ENTRY_CACHE_PATH.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
    except Exception:
        pass
    return {}


def _save_entry_cache(cache: dict) -> None:
    try:
        _ENTRY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _ENTRY_CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _ENTRY_CACHE_PATH)
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


def _run_extractor(scs_path: Path, dest: Path, partial: str = "/manifest.sii", should_stop=None,
                   timeout_seconds: float | None = None) -> bool:
    """用 extractor.exe 提取 SCS# 包内指定路径的文件。partial 用 / 开头的绝对路径。"""
    if not _EXTRACTOR.exists():
        return False
    try:
        proc = subprocess.Popen(
            [str(_EXTRACTOR), str(scs_path), "--deep", f"--partial={partial}",
             "-d", str(dest), "-s"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=_SP_HIDE, startupinfo=_SP_STARTUPINFO,
        )
        deadline = time.monotonic() + (timeout_seconds or _TIMEOUT_SECONDS)
        while proc.poll() is None:
            if should_stop and should_stop():
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return False
            if time.monotonic() >= deadline:
                proc.kill()
                return False
            time.sleep(0.05)
        return proc.returncode == 0
    except OSError:
        return False


def _run_sxc(archive_path: Path, dest: Path, filename: str = "manifest.sii", should_stop=None,
             timeout_seconds: float | None = None) -> bool:
    """用 sxc64.exe 提取 AEM!/加密ZIP 包内指定文件名的文件。"""
    if not _SXC.exists():
        return False
    try:
        # SXC treats -f as a path pattern. Passing only Path.name fails for
        # nested files such as /def/city/rlp_germany/bacharach.sii.
        filename = str(filename or "manifest.sii").replace("\\", "/")
        if not filename.startswith("/"):
            filename = "/" + filename
        proc = subprocess.Popen(
            [str(_SXC), str(archive_path), "-o", str(dest), "-f", filename, "-q"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=_SP_HIDE, startupinfo=_SP_STARTUPINFO,
        )
        deadline = time.monotonic() + (timeout_seconds or _TIMEOUT_SECONDS)
        while proc.poll() is None:
            if should_stop and should_stop():
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return False
            if time.monotonic() >= deadline:
                proc.kill()
                return False
            time.sleep(0.05)
        return proc.returncode == 0
    except OSError:
        return False


def _extract_single_file(path: Path, inner_name: str, dest: Path, should_stop=None) -> bool:
    """按 magic 选工具提取单个文件到 dest 目录。"""
    magic = _detect_magic(path)
    partial = inner_name if inner_name.startswith("/") else "/" + inner_name
    if magic == "scs_hashfs":
        return _run_extractor(path, dest, partial=partial, should_stop=should_stop)
    if magic == "aem":
        return _run_sxc(path, dest, filename=inner_name, should_stop=should_stop)
    if magic == "zip":
        # 加密 ZIP 或 zipfile 打不开的 ZIP，交给 sxc
        return _run_sxc(path, dest, filename=inner_name, should_stop=should_stop)
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


def extract_file_bytes(archive_path, inner_name: str, should_stop=None) -> Optional[bytes]:
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
    if should_stop and should_stop():
        shutil.rmtree(tmp, ignore_errors=True)
        return None
    if _extract_single_file(path, inner_name, tmp, should_stop=should_stop):
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


def extract_first_image_bytes(archive_path) -> Optional[tuple[bytes, str]]:
    """Extract the first image from an encrypted archive when manifest.sii
    does not declare an icon filename. This covers legacy packages whose
    preview uses an arbitrary name (for example ``PR_models.jpg``)."""
    path = Path(archive_path)
    if not path.is_file():
        return None
    cache_key = _cache_key(path)
    with _file_bytes_lock:
        if cache_key in _first_image_cache:
            return _first_image_cache[cache_key]
    tmp = Path(tempfile.mkdtemp(prefix="ets2mm_img_"))
    try:
        magic = _detect_magic(path)
        tool = _EXTRACTOR if magic == "scs_hashfs" else _SXC
        if not tool.exists():
            return None
        if magic == "scs_hashfs":
            # Encrypted HashFS packages frequently use an arbitrary preview
            # filename (for example /volgamap.jpg). Find such names only in
            # this background fallback; normal manifest candidates are tried
            # by read_icon before reaching here.
            import re as _re
            try:
                listed = subprocess.run(
                    [str(tool), str(path), "--deep", "--list"],
                    capture_output=True, text=True,
                    timeout=max(_TIMEOUT_SECONDS * 4, 45),
                    creationflags=_SP_HIDE, startupinfo=_SP_STARTUPINFO,
                )
            except (subprocess.TimeoutExpired, OSError):
                listed = None
            names = []
            if listed is not None:
                for match in _re.finditer(
                    r"(?im)^\s*(/[^\r\n]*\.(?:jpg|jpeg|png|webp|bmp|gif))\s*$",
                    listed.stdout or "",
                ):
                    name = match.group(1).strip().replace("\\", "/")
                    if name not in names:
                        names.append(name)
            priority = ("icon", "preview", "thumb", "cover", "logo", "banner")
            names.sort(key=lambda n: (0 if any(k in n.lower() for k in priority) else 1, len(n), n.lower()))
            for image_name in names[:24]:
                one_tmp = Path(tempfile.mkdtemp(prefix="ets2mm_img_one_"))
                try:
                    if _run_extractor(path, one_tmp, partial=image_name):
                        for candidate in one_tmp.rglob("*"):
                            if candidate.is_file() and candidate.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"):
                                data = candidate.read_bytes()
                                if len(data) > 100:
                                    found = (data, image_name)
                                    with _file_bytes_lock:
                                        _first_image_cache[cache_key] = found
                                    _disk_cache_put(cache_key, image_name, data)
                                    return found
                except OSError:
                    pass
                finally:
                    shutil.rmtree(one_tmp, ignore_errors=True)
            with _file_bytes_lock:
                _first_image_cache[cache_key] = None
            return None
        else:
            # List entries once, then extract only the first image path found.
            # Never use a broad wildcard extraction: old map packages can
            # contain thousands of textures and would otherwise exhaust memory.
            listed = subprocess.run(
                [str(tool), str(path), "-l"], capture_output=True, text=True,
                timeout=min(_TIMEOUT_SECONDS, 8), creationflags=_SP_HIDE,
                startupinfo=_SP_STARTUPINFO,
            )
            import re as _re
            image_name = next((m.group(0) for m in _re.finditer(
                r"(?im)([^\r\n\\/]+\.(?:jpg|jpeg|png|webp|bmp|gif))\s*$",
                listed.stdout or "") ), None)
            if not image_name:
                with _file_bytes_lock:
                    _first_image_cache[cache_key] = None
                return None
            args = [str(tool), str(path), "-o", str(tmp), "-f", image_name, "-q"]
        try:
            result = subprocess.run(
                args, capture_output=True, timeout=min(_TIMEOUT_SECONDS, 8),
                creationflags=_SP_HIDE, startupinfo=_SP_STARTUPINFO,
            )
        except (subprocess.TimeoutExpired, OSError):
            result = None
        if result is not None and result.returncode == 0:
            for candidate in tmp.rglob("*"):
                if candidate.is_file() and candidate.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"):
                    try:
                        data = candidate.read_bytes()
                        if len(data) > 100:
                            found = (data, candidate.name)
                            with _file_bytes_lock:
                                _first_image_cache[cache_key] = found
                            return found
                    except OSError:
                        continue
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    with _file_bytes_lock:
        _first_image_cache[cache_key] = None
    return None


def extract_files_batch(archive_path, inner_names, should_stop=None) -> dict:
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
            proc = subprocess.Popen(
                [str(_EXTRACTOR), str(path), "--deep", f"--partial={multi}",
                 "-d", str(tmp), "-s"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=_SP_HIDE, startupinfo=_SP_STARTUPINFO,
            )
            deadline = time.monotonic() + _TIMEOUT_SECONDS
            while proc.poll() is None:
                if should_stop and should_stop():
                    proc.terminate()
                    try:
                        proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    break
                if time.monotonic() >= deadline:
                    proc.kill()
                    break
                time.sleep(0.05)
            ok = proc.returncode == 0 and not (should_stop and should_stop())
        except OSError:
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
        if should_stop and should_stop():
            break
        result[n] = extract_file_bytes(path, n, should_stop=should_stop)
    return result


def extract_archive_to_directory(archive_path, destination) -> bool:
    """Fully extract an external SCS# archive into destination.

    This is intentionally opt-in for workflows such as localization that need
    to discover many unknown def/locale files at once.
    """
    path = Path(archive_path)
    dest = Path(destination)
    if not path.is_file() or _detect_magic(path) != "scs_hashfs" or not _EXTRACTOR.exists():
        return False
    try:
        dest.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [str(_EXTRACTOR), str(path), "--deep", "-d", str(dest)],
            capture_output=True, timeout=max(_TIMEOUT_SECONDS, 120),
            creationflags=_SP_HIDE, startupinfo=_SP_STARTUPINFO,
        )
        return proc.returncode == 0 and any(dest.rglob("*"))
    except (subprocess.TimeoutExpired, OSError):
        return False


def extract_l10n_tree_to_directory(archive_path, destination, target_locale: str = "zh_cn",
                                   should_stop=None) -> bool:
    """Extract only localization-relevant trees to a temporary directory.

    The caller can then scan the temporary directory with the normal archive
    reader and remove it afterwards. Encrypted packages are extracted with a
    small number of wildcard requests instead of starting one process per SII
    file; a listed-file fallback is kept for extractor builds without wildcard
    support.
    """
    path = Path(archive_path)
    dest = Path(destination)
    if not path.is_file() or (should_stop and should_stop()):
        return False
    magic = _detect_magic(path)
    if magic not in ("scs_hashfs", "aem", "zip"):
        return False
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False

    locale = str(target_locale or "zh_cn").replace("\\", "/").strip("/")
    tree_key = hashlib.md5(f"{_cache_key(path)}|{locale.lower()}".encode("utf-8")).hexdigest()
    cached_root = _L10N_TREE_CACHE_DIR / tree_key
    marker = cached_root / "_complete.json"
    try:
        if marker.exists() and time.time() - json.loads(marker.read_text(encoding="utf-8")).get("ts", 0) < _ENTRY_CACHE_TTL_SECONDS:
            for item in cached_root.iterdir():
                if item.name != marker.name:
                    target = dest / item.name
                    if item.is_dir():
                        shutil.copytree(item, target, dirs_exist_ok=True)
                    elif item.is_file():
                        shutil.copy2(item, target)
            return any(p.is_file() for p in dest.rglob("*"))
    except Exception:
        pass
    # Extractor's partial matcher treats a directory path as recursive. Using
    # ``/def`` (rather than ``/def/*``) is supported by the bundled extractor
    # and avoids a second full archive scan caused by wildcard expansion.
    # The bundled HashFS extractor accepts comma-separated partial paths, so
    # keep def and locale in one archive pass. SXC accepts one path pattern per
    # invocation, therefore encrypted ZIP/AEM packages use two passes.
    patterns = [f"/def,/locale/{locale}"] if magic == "scs_hashfs" else ["/def", f"/locale/{locale}"]
    runner = _run_extractor if magic == "scs_hashfs" else _run_sxc
    for pattern in patterns:
        if should_stop and should_stop():
            return False
        ok = runner(path, dest, partial=pattern, should_stop=should_stop,
                    timeout_seconds=max(_TIMEOUT_SECONDS * 4, 60)) if magic == "scs_hashfs" else \
            runner(path, dest, filename=pattern, should_stop=should_stop,
                   timeout_seconds=max(_TIMEOUT_SECONDS * 4, 60))
        if not ok and should_stop and should_stop():
            return False

    if any(p.is_file() for p in dest.rglob("*")):
        try:
            if cached_root.exists():
                shutil.rmtree(cached_root, ignore_errors=True)
            cached_root.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(dest, cached_root, dirs_exist_ok=True)
            marker.write_text(json.dumps({"ts": time.time()}), encoding="utf-8")
        except OSError:
            pass
        return True

    # Fallback for older SXC builds that do not expand wildcards.
    listed = list_external_entries(path)
    selected = [n for n in listed if n.lower().startswith(("def/", f"locale/{locale.lower()}/"))]
    if not selected:
        return False
    extracted = extract_files_batch(path, selected, should_stop=should_stop)
    for name, data in extracted.items():
        if should_stop and should_stop():
            return False
        if not data:
            continue
        target = dest / name.replace("/", os.sep)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        except OSError:
            continue
    if any(p.is_file() for p in dest.rglob("*")):
        try:
            if cached_root.exists():
                shutil.rmtree(cached_root, ignore_errors=True)
            cached_root.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(dest, cached_root, dirs_exist_ok=True)
            marker.write_text(json.dumps({"ts": time.time()}), encoding="utf-8")
        except OSError:
            pass
        return True
    return False


def list_external_entries(archive_path) -> list[str]:
    """List logical paths from an encrypted archive for selective workflows.

    SXC can enumerate encrypted ZIP/AEM packages without extracting their
    contents. HashFS uses extractor's deep listing. This is intentionally a
    listing-only API so localization can request just def/locale files.
    """
    path = Path(archive_path)
    if not path.is_file():
        return []
    key = _cache_key(path)
    cache = _load_entry_cache()
    cached = cache.get(key)
    if isinstance(cached, dict) and time.time() - cached.get("ts", 0) < _ENTRY_CACHE_TTL_SECONDS:
        entries = cached.get("entries")
        if isinstance(entries, list):
            return [str(x) for x in entries]
    magic = _detect_magic(path)
    if magic == "scs_hashfs":
        tool = _EXTRACTOR
        args = [str(tool), str(path), "--deep", "--list"]
        timeout = max(_TIMEOUT_SECONDS * 4, 45)
    elif magic in ("aem", "zip"):
        tool = _SXC
        args = [str(tool), str(path), "-l"]
        timeout = max(_TIMEOUT_SECONDS * 2, 30)
    else:
        return []
    if not tool.exists():
        return []
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_SP_HIDE,
            startupinfo=_SP_STARTUPINFO,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    import re as _re
    out: list[str] = []
    for line in (result.stdout or "").splitlines():
        value = line.strip().replace("\\", "/")
        if not value.startswith("/"):
            continue
        value = value.lstrip("/")
        if value and value not in out:
            out.append(value)
    # Some extractor builds print paths with a leading marker or mixed case;
    # retain only normalized logical paths and discard directory-only lines.
    entries = [x for x in out if "." in Path(x).name]
    if entries:
        cache[key] = {"ts": time.time(), "entries": entries}
        _save_entry_cache(cache)
    return entries


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
