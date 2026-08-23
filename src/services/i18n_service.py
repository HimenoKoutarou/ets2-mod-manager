"""
国际化 (i18n) 服务
- 资源文件：assets/i18n/{lang}.json
- 支持语言：zh_CN (简体中文) / en_US (English) / ru_RU (Русский)
- 提供 _() / tr() 翻译函数，以及 PySide6 Signal 通知界面刷新语言
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Dict, Optional

# 使用 RLock 以支持同一线程多次获取锁

try:
    from PySide6.QtCore import QObject, Signal
    _HAS_QT = True
except Exception:
    _HAS_QT = False


_LOCK = threading.RLock()
_DICT_CACHE: Dict[str, Dict[str, str]] = {}
_VALID_LANGS = ("zh_CN", "en_US", "ru_RU")

# ---------- ----------
def _module_root_dir() -> Path:
    """基于 __file__ 向上 3 级得到 <项目根>，即 run.py 所在目录。"""
    return Path(__file__).resolve().parent.parent.parent


def _app_config_dir() -> Path:
    app_dir = None
    try:
        if _HAS_QT:
            from PySide6.QtCore import QCoreApplication
            fp = QCoreApplication.applicationFilePath()
            if fp:
                app_dir = Path(fp).resolve().parent
        if app_dir is None or not str(app_dir):
            app_dir = _module_root_dir()
    except Exception:
        app_dir = _module_root_dir()
    try:
        cfg = app_dir / "config"
        cfg.mkdir(parents=True, exist_ok=True)
        return cfg
    except Exception:
        home_cfg = Path.home() / ".ets2_mod_manager" / "config"
        home_cfg.mkdir(parents=True, exist_ok=True)
        return home_cfg


def _language_pref_path() -> Path:
    return _app_config_dir() / "language.json"


def load_language_preference() -> str:
    p = _language_pref_path()
    if not p.exists():
        return "zh_CN"
    try:
        with p.open("r", encoding="utf-8-sig") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            lang = str(raw.get("language", "zh_CN"))
            if lang in _VALID_LANGS:
                return lang
        return "zh_CN"
    except Exception:
        return "zh_CN"


def save_language_preference(lang: str) -> None:
    if lang not in _VALID_LANGS:
        return
    p = _language_pref_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            json.dump({"language": lang}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# 初始化：默认简体中文
_CURRENT_LANG: str = load_language_preference()


def _i18n_dir() -> Path:
    here = Path(__file__).resolve().parent.parent.parent
    return here / "assets" / "i18n"


def _dict_path(lang: str) -> Path:
    return _i18n_dir() / f"{lang}.json"


def _load_dict(lang: str) -> Dict[str, str]:
    """加载语言字典，带缓存；不存在则返回空字典。"""
    with _LOCK:
        cached = _DICT_CACHE.get(lang)
        if cached is not None:
            return cached
    p = _dict_path(lang)
    d: Dict[str, str] = {}
    if p.exists():
        try:
            try:
                with p.open("r", encoding="utf-8-sig") as f:
                    raw = json.load(f)
            except UnicodeDecodeError:
                with p.open("r", encoding="utf-8") as f:
                    raw = json.load(f)
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if isinstance(k, str) and isinstance(v, str):
                        d[k] = v
        except (OSError, json.JSONDecodeError):
            d = {}
    with _LOCK:
        _DICT_CACHE[lang] = d
    return d


def current_language() -> str:
    with _LOCK:
        return _CURRENT_LANG


def available_languages() -> tuple[str, ...]:
    return _VALID_LANGS


def language_display_name(lang: str) -> str:
    return {
        "zh_CN": "简体中文",
        "en_US": "English",
        "ru_RU": "Русский",
    }.get(lang, lang)


def set_language(lang: str, emit: bool = True, persist: bool = True) -> bool:
    """切换语言。返回是否发生实际变化。"""
    global _CURRENT_LANG
    if lang not in _VALID_LANGS:
        return False
    with _LOCK:
        if _CURRENT_LANG == lang:
            return False
        _CURRENT_LANG = lang
        _load_dict(lang)
    # 持久化到配置文件
    if persist:
        save_language_preference(lang)
    if emit and _HAS_QT:
        try:
            I18nNotifier.instance().languageChanged.emit(lang)
        except Exception:
            pass
    return True


def _fallback_lookup(key: str) -> str:
    """按当前语言查字典，找不到则回退到 zh_CN，最后返回 key 本身。"""
    lang = current_language()
    d = _load_dict(lang)
    if key in d:
        return d[key]
    if lang != "zh_CN":
        fallback = _load_dict("zh_CN")
        if key in fallback:
            return fallback[key]
    return key


def tr(key: str, **kwargs) -> str:
    """翻译函数。支持 {placeholder} 模板替换。"""
    if not key:
        return key
    text = _fallback_lookup(key)
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            pass
    return text


def _(key: str, **kwargs) -> str:
    return tr(key, **kwargs)


if _HAS_QT:
    class _I18nNotifier(QObject):
        languageChanged = Signal(str)

        @classmethod
        def instance(cls) -> "_I18nNotifier":
            if not hasattr(cls, "_inst"):
                cls._inst = cls()
            return cls._inst

    I18nNotifier = _I18nNotifier
else:
    class I18nNotifier:
        @staticmethod
        def instance():
            return None
