"""
Steam Workshop 服务：通过官方公开 API 批量抓取已订阅模组的真实标题。

API: POST https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/
参数: itemcount=N, publishedfileids[0]=xxx, publishedfileids[1]=yyy, ...
无需 API Key，无需登录。
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, List, Optional


# ===== 配置 =====
API_URL = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
BATCH_SIZE = 50              # 单次最多查询数量（Steam 官方限制保守值）
HTTP_TIMEOUT = 8             # 秒，超时直接放弃，不阻塞主流程
CACHE_TTL_SECONDS = 30 * 24 * 3600  # 缓存 30 天
USER_AGENT = "ETS2ModManager/1.0 (+https://github.com/local/ets2mm)"
APP_ID_ETS2 = 227300         # 仅用于断言 / 过滤

_lock = threading.Lock()
_cache: Optional[Dict[str, Dict]] = None   # {workshop_id: {"title": str, "ts": int}}
_cache_dirty: bool = False
_opener: Optional[object] = None


def _get_opener():
    """返回带系统代理的 URL opener（惰性初始化）；代理时跳过 SSL 主机校验。"""
    global _opener
    if _opener is not None:
        return _opener
    handlers = []
    try:
        proxies = urllib.request.getproxies()
        if proxies:
            handlers.append(urllib.request.ProxyHandler(proxies))
    except Exception:
        pass
    # 自定义 SSL 上下文：通过代理时跳过证书验证（避免公司/校园 HTTPS 中间人代理导致握手失败）
    try:
        import ssl as _ssl
        _ctx = _ssl.create_default_context()
        _ctx.check_hostname = False
        _ctx.verify_mode = _ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=_ctx))
    except Exception:
        pass
    _opener = urllib.request.build_opener(*handlers) if handlers else urllib.request.build_opener()
    return _opener


def _get_cache_path() -> Path:
    """缓存文件位置：<项目根>/assets/cache/workshop_titles.json。"""
    # 相对当前文件定位：src/services/steam_workshop_service.py -> ../../assets/cache
    here = Path(__file__).resolve().parent.parent.parent
    folder = here / "assets" / "cache"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return folder / "workshop_titles.json"


def _load_cache() -> Dict[str, Dict]:
    global _cache, _cache_dirty
    with _lock:
        if _cache is not None:
            return _cache
        cp = _get_cache_path()
        if cp.exists():
            try:
                with cp.open("r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if not isinstance(loaded, dict):
                    loaded = {}
            except (OSError, json.JSONDecodeError):
                loaded = {}
            # ---- R5 opt: 双 TTL 过期清理（读取时删除 >2*TTL 的僵尸条目，避免缓存无限膨胀）----
            now = int(time.time())
            purge_ttl = 2 * CACHE_TTL_SECONDS   # 读取期 TTL 30 天 + 2*T=60 天宽限期，超 90 天删除
            cleaned = 0
            if isinstance(loaded, dict):
                stale_ids = [mid for mid, e in loaded.items()
                             if isinstance(e, dict) and (now - int(e.get("ts", 0))) > purge_ttl]
                for sid in stale_ids:
                    del loaded[sid]
                    cleaned += 1
            if cleaned > 0:
                _cache_dirty = True   # 加载时做了清理也要写盘
                import sys as _sys_r5
                print(f"[steam_ws] 清理了 {cleaned} 个超过 60 天的僵尸缓存条目", file=_sys_r5.stderr)
            _cache = loaded
        else:
            _cache = {}
        return _cache


def _save_cache() -> None:
    global _cache_dirty
    with _lock:
        if _cache is None or not _cache_dirty:
            return
        cp = _get_cache_path()
        try:
            tmp = cp.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(_cache, f, ensure_ascii=False, indent=2)
            os.replace(tmp, cp)
            _cache_dirty = False
        except OSError:
            pass


def get_cached_title(workshop_id: str) -> Optional[str]:
    """只读缓存，不发请求。命中且未过期返回标题，否则返回 None。"""
    if not workshop_id or not workshop_id.isdigit():
        return None
    cache = _load_cache()
    entry = cache.get(workshop_id)
    if not entry:
        return None
    ts = entry.get("ts", 0)
    if time.time() - ts > CACHE_TTL_SECONDS:
        return None
    title = entry.get("title")
    if title and isinstance(title, str) and title.strip():
        return title.strip()
    return None


def _post_batch(ids: List[str]) -> Dict[str, str]:
    """对一批 ID 发 POST 请求，返回 {id: title} 的 dict（只含成功条目）。"""
    global _cache_dirty
    out: Dict[str, str] = {}
    if not ids:
        return out
    data = {"itemcount": str(len(ids))}
    for i, mid in enumerate(ids):
        data[f"publishedfileids[{i}]"] = mid
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(API_URL, data=body, method="POST")
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with _get_opener().open(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            obj = json.loads(raw)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
        return out
    try:
        items = obj["response"]["publishedfiledetails"]
    except (KeyError, TypeError):
        return out
    now = int(time.time())
    cache = _load_cache()
    for it in items:
        mid = str(it.get("publishedfileid", ""))
        if not mid:
            continue
        result = it.get("result", 0)
        if result != 1:
            # result=9 = 已删除/私密，也写一条负缓存占位，避免下次再请求
            cache[mid] = {"title": "", "ts": now, "result": result}
            _cache_dirty = True
            continue
        title = it.get("title") or ""
        title = str(title).strip()
        consumer = it.get("consumer_app_id")
        if title:
            out[mid] = title
            cache[mid] = {
                "title": title,
                "ts": now,
                "consumer_app_id": consumer,
            }
            _cache_dirty = True
    return out


def fetch_titles(ids: Iterable[str],
                 force_refresh: bool = False,
                 save_cache: bool = True) -> Dict[str, str]:
    """批量查询 Workshop 标题。失败不抛异常，返回已成功的条目（可能为空 dict）。

    - ids: 可迭代的 workshop_id 字符串（非数字会被自动过滤）
    - force_refresh: True 时忽略本地缓存，强制回源
    - save_cache: 结束时是否落盘缓存
    """
    ids_clean: List[str] = []
    seen = set()
    for x in ids:
        if not x or not isinstance(x, str):
            continue
        x = x.strip()
        if not x.isdigit() or x in seen:
            continue
        seen.add(x)
        ids_clean.append(x)
    if not ids_clean:
        return {}

    now = time.time()
    out: Dict[str, str] = {}
    pending: List[str] = []
    cache = _load_cache()
    for mid in ids_clean:
        entry = cache.get(mid)
        if not force_refresh and entry:
            ts = entry.get("ts", 0)
            if now - ts <= CACHE_TTL_SECONDS:
                t = (entry.get("title") or "").strip()
                if t:
                    out[mid] = t
                # 负缓存也视为已处理（已知 result != 1）
                continue
        pending.append(mid)

    # 分批回源（R5 opt: 合并 save_cache 调用，每批不再重复 json.dump + os.replace）
    for i in range(0, len(pending), BATCH_SIZE):
        batch = pending[i:i + BATCH_SIZE]
        batch_out = _post_batch(batch)
        out.update(batch_out)

    # 所有批次结束后一次性落盘（_post_batch 中已置 _cache_dirty = True）
    if save_cache:
        _save_cache()

    return out


def fetch_and_fill_mods(mods: Iterable[object], save_cache: bool = True) -> int:
    """对一组 Mod 对象：筛选出 workshop 类型、标题仍为纯数字的，批量查询 Steam 并回填 display_name。
    返回实际被回填的数量。"""
    candidates: Dict[str, object] = {}
    for m in mods:
        try:
            if getattr(m, "package_type", None) != "workshop":
                continue
            mid = getattr(m, "mod_id", None)
            if not mid or not str(mid).isdigit():
                continue
            # 只看 manifest.display_name：空或纯数字 → 需要 Steam API 查名
            mani = getattr(m, "manifest", None)
            if mani is not None:
                dn = (getattr(mani, "display_name", None) or "").strip()
                if not dn or dn.isdigit():
                    candidates[str(mid)] = m
            else:
                candidates[str(mid)] = m
        except Exception:
            continue
    if not candidates:
        return 0

    titles = fetch_titles(candidates.keys(), save_cache=save_cache)
    filled = 0
    for mid, t in titles.items():
        mod = candidates.get(mid)
        if mod is None:
            continue
        mani = getattr(mod, "manifest", None)
        if mani is None:
            continue
        try:
            mani.display_name = t
            filled += 1
        except Exception:
            pass
    return filled
