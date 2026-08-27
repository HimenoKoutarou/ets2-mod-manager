"""
国际化 (i18n) 服务
- 资源文件：assets/i18n/{lang}.json
- 支持语言：zh_CN (简体中文) / en_US (English) / ru_RU (Русский)
- 提供 _() / tr() 翻译函数，以及 PySide6 Signal 通知界面刷新语言
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Dict, Optional

try:
    from PySide6.QtCore import QObject, Signal
    _HAS_QT = True
except Exception:
    _HAS_QT = False


_LOCK = threading.RLock()
_DICT_CACHE: Dict[str, Dict[str, str]] = {}
_VALID_LANGS = ("zh_CN", "en_US", "ru_RU")
_DEFAULT_LANG = "zh_CN"
# 注意：不要在模块顶层调用任何 Qt 路径 API（如 QCoreApplication.applicationFilePath），
# 否则 import 阶段 QApplication 尚未实例化会打印 warning：
#   QCoreApplication::applicationFilePath: Please instantiate the QApplication object first
# 当前语言采用惰性初始化，首次调用 current_language() / set_language() 时读取持久化配置。
_CURRENT_LANG: Optional[str] = None


def _module_root_dir() -> Path:
    """返回"用户代码的根目录"——写入配置、读取 assets 的基本目录。
    - 打包态 (sys.frozen=True)：EXE 同级目录；
    - onefile 临时目录若检测到，优先作为 assets 读取源；
    - 源码态：基于 __file__ 向上三级定位项目根。
    """
    frozen = getattr(sys, "frozen", False)
    if frozen:
        return Path(sys.executable).resolve().parent
    try:
        return Path(__file__).resolve().parent.parent.parent
    except Exception:
        return Path.cwd()


def _looks_like_python_interpreter(fp: Path) -> bool:
    """源码场景下 QApplication.applicationFilePath 返回的是 python.exe，
    不能作为"应用自身目录"，否则 config/ 会被建到 Python 安装目录下。
    识别策略：文件名以 python 开头（含 python3/pythonw/python3w 等）。"""
    try:
        name = fp.name.lower()
        return name.startswith("python") and name.endswith((".exe", ""))
    except Exception:
        return False


def _app_config_dir() -> Path:
    """解析用户运行时配置目录（language.json 等持久化数据）。

    候选优先级（按顺序，第一个可写的即使用）：
      1. EXE 运行模式：QCoreApplication.applicationFilePath() 的同级 config/。
         * 仅当 QApplication 已实例化 (instance() is not None)。
         * 且 applicationFilePath() 不是 Python 解释器本身（源码态过滤）。
      2. _module_root_dir() / config/（源码目录或 EXE 目录）。
      3. Path.home() / ".ets2_mod_manager" / config（上述不可写时兜底，如 Program Files）。
    """
    candidate_dirs = []
    try:
        if _HAS_QT:
            from PySide6.QtCore import QCoreApplication
            # 必须检查 instance()，否则 Qt C 端会直接 stderr 打印 warning 不抛异常。
            if QCoreApplication.instance() is not None:
                fp = QCoreApplication.applicationFilePath()
                if fp:
                    candidate_dirs.append(Path(fp).resolve().parent)
    except Exception:
        pass
    candidate_dirs.append(_module_root_dir())

    # 过滤源码模式下"python.exe"的父目录
    cleaned_dirs = []
    for d in candidate_dirs:
        if d is None:
            continue
        try:
            fp_test = d / (Path(sys.executable).name if getattr(sys, "frozen", False) else "python.exe")
        except Exception:
            fp_test = None
        # 真正的判断：如果 d / "python*.exe" 是 sys.executable，那 d 就是 Python 安装目录，跳过。
        try:
            exe = Path(sys.executable).resolve()
            if (d / exe.name).resolve() == exe:
                # 源码模式：这个目录是 Python 安装路径，跳过。
                continue
        except Exception:
            pass
        cleaned_dirs.append(d)

    if not cleaned_dirs:
        cleaned_dirs.append(_module_root_dir())

    # 去重保序
    seen = set()
    unique = []
    for d in cleaned_dirs:
        key = str(d)
        if key in seen:
            continue
        seen.add(key)
        unique.append(d)

    # 依次尝试可写性
    for d in unique:
        try:
            cfg = Path(d) / "config"
            cfg.mkdir(parents=True, exist_ok=True)
            probe = cfg / ".write_test"
            probe.write_bytes(b"ok")
            probe.unlink(missing_ok=True)
            return cfg
        except Exception:
            continue

    # 最终兜底：用户目录
    home_cfg = Path.home() / ".ets2_mod_manager" / "config"
    try:
        home_cfg.mkdir(parents=True, exist_ok=True)
        return home_cfg
    except Exception:
        return unique[0]


def _language_pref_path() -> Path:
    return _app_config_dir() / "language.json"


def load_language_preference() -> str:
    p = _language_pref_path()
    if not p.exists():
        return _DEFAULT_LANG
    try:
        with p.open("r", encoding="utf-8-sig") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            lang = str(raw.get("language", _DEFAULT_LANG))
            if lang in _VALID_LANGS:
                return lang
        return _DEFAULT_LANG
    except Exception:
        return _DEFAULT_LANG


def save_language_preference(lang: str) -> None:
    if lang not in _VALID_LANGS:
        return
    p = _language_pref_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            json.dump({"language": lang}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        import sys as _sys
        print(f"[i18n] 保存语言配置失败: {type(e).__name__}: {e}", file=_sys.stderr)


def _ensure_current_lang_loaded() -> None:
    """惰性读取语言偏好；仅在锁内由 current_language/set_language 调用。"""
    global _CURRENT_LANG
    if _CURRENT_LANG is None:
        _CURRENT_LANG = load_language_preference()


def _i18n_dir() -> Path:
    frozen = getattr(sys, "frozen", False)
    if frozen:
        base = Path(sys.executable).resolve().parent
        meipass = getattr(sys, "_MEIPASS", None)
        candidates = [base / "assets" / "i18n"]
        if meipass:
            candidates.insert(0, Path(meipass) / "assets" / "i18n")
        for c in candidates:
            if c.exists():
                return c
    try:
        here = Path(__file__).resolve().parent.parent.parent
        return here / "assets" / "i18n"
    except Exception:
        return Path.cwd() / "assets" / "i18n"


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
        _ensure_current_lang_loaded()
        return _CURRENT_LANG  # type: ignore[return-value]


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
    changed = False
    with _LOCK:
        _ensure_current_lang_loaded()
        if _CURRENT_LANG != lang:
            _CURRENT_LANG = lang
            changed = True
        _load_dict(lang)
    if changed and persist:
        save_language_preference(lang)
    if changed and emit and _HAS_QT:
        try:
            I18nNotifier.instance().languageChanged.emit(lang)
        except Exception as e:
            import sys as _sys
            print(f"[i18n] 发射 languageChanged 信号失败: {type(e).__name__}: {e}", file=_sys.stderr)
    return changed


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