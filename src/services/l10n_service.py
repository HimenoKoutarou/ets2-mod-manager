"""汉化服务层
四层翻译：mod内置原生 -> 本地字典 -> UFL内置翻译库 -> MyMemory API。
同时区分“没有翻译”和“def 没有 city_name_localized/locale key”。
"""
from __future__ import annotations

import csv
import json
import re
import unicodedata
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class TranslationEntry:
    source: str = ""
    translated: str = ""
    status: str = "pending"  # native/local/ufl/api/pending/failed/missing_value/missing_locale
    source_mod: str = ""
    category: str = "city"
    # Whether the active target locale already contains this key in the
    # scanned mod set.  A missing key is different from a missing translation:
    # the generated localization mod must add the key when it is translated.
    locale_key_present: bool = False
    def_locale_key_present: bool = True
    locale_key: str = ""
    unit_name: str = ""
    # Community defs may spell the same locale identifier using the display
    # name (or a different punctuation/diacritic form).  Keep those aliases
    # for lookup only; ``source`` remains the canonical key written to SII.
    lookup_candidates: List[str] = field(default_factory=list)
    matched_key: str = ""


@dataclass
class L10nResult:
    cities: List[TranslationEntry] = field(default_factory=list)
    countries: List[TranslationEntry] = field(default_factory=list)
    ferries: List[TranslationEntry] = field(default_factory=list)
    hints: List[TranslationEntry] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.cities) + len(self.countries) + len(self.ferries) + len(self.hints)

    @property
    def translated_count(self) -> int:
        return sum(1 for e in self.all_entries if e.status in ("native", "local", "ufl", "api"))

    @property
    def pending_count(self) -> int:
        return sum(1 for e in self.all_entries if e.status in ("pending", "failed", "missing_value", "missing_locale"))

    @property
    def all_entries(self) -> List[TranslationEntry]:
        return self.cities + self.countries + self.ferries + self.hints


class L10nService:
    """汉化翻译服务"""

    SUPPORTED_LOCALES: List[str] = [
        "bg_bg", "ca_es", "cs_cz", "da_dk", "de_de", "el_gr", "en_gb", "en_us", "es_es", "es_la",
        "et_ee", "eu_es", "fi_fi", "fr_fr", "gl_es", "hr_hr", "hu_hu", "it_it", "ja_jp", "ka_ge",
        "ko_kr", "lt_lt", "lv_lv", "mk_mk", "nl_nl", "no_no", "pl_pl", "pl_si", "pt_br", "pt_pt",
        "ro_ro", "ru_ru", "sk_sk", "sl_sl", "sr_sp", "sr_sr", "sv_se", "tr_tr", "uk_uk", "vi_vn",
        "zh_cn", "zh_tw",
    ]

    LOCALE_DISPLAY_NAMES: Dict[str, str] = {
        "bg_bg": "Български",
        "ca_es": "Català",
        "cs_cz": "Čeština",
        "da_dk": "Dansk",
        "de_de": "Deutsch",
        "el_gr": "Ελληνικά",
        "en_gb": "English (UK)",
        "en_us": "English (US)",
        "es_es": "Español (ES)",
        "es_la": "Español (LA)",
        "et_ee": "Eesti",
        "eu_es": "Euskara",
        "fi_fi": "Suomi",
        "fr_fr": "Français",
        "gl_es": "Galego",
        "hr_hr": "Hrvatski",
        "hu_hu": "Magyar",
        "it_it": "Italiano",
        "ja_jp": "日本語",
        "ka_ge": "ქართული",
        "ko_kr": "한국어",
        "lt_lt": "Lietuvių",
        "lv_lv": "Latviešu",
        "mk_mk": "Македонски",
        "nl_nl": "Nederlands",
        "no_no": "Norsk",
        "pl_pl": "Polski",
        "pl_si": "Slovenčina",
        "pt_br": "Português (BR)",
        "pt_pt": "Português (PT)",
        "ro_ro": "Română",
        "ru_ru": "Русский",
        "sk_sk": "Slovenčina",
        "sl_sl": "Slovenščina",
        "sr_sp": "Српски",
        "sr_sr": "Srpski",
        "sv_se": "Svenska",
        "tr_tr": "Türkçe",
        "uk_uk": "Українська",
        "vi_vn": "Tiếng Việt",
        "zh_cn": "简体中文",
        "zh_tw": "繁體中文",
    }

    def __init__(self, config_dir: Path, target_locale: str = "zh_cn"):
        self._config_dir = config_dir
        self._dict_path = config_dir / "l10n_dict.json"
        self._ufl_path: Optional[Path] = None
        self._ufl_paths: List[Path] = []
        self._local_dict: Dict[str, str] = {}
        self._ufl_dict: Dict[str, str] = {}
        self._native_locale_dict: Dict[str, str] = {}
        self._native_locale_folded: Dict[str, str] = {}
        self._local_dict_folded: Dict[str, str] = {}
        self._ufl_dict_folded: Dict[str, str] = {}
        self._native_locale_compact: Dict[str, Optional[str]] = {}
        self._local_dict_compact: Dict[str, Optional[str]] = {}
        self._ufl_dict_compact: Dict[str, Optional[str]] = {}
        self._target_locale = target_locale if target_locale in self.SUPPORTED_LOCALES else "zh_cn"
        self._load_local_dict()

    def set_native_locale(self, native_dict: Dict[str, str]):
        """设置 mod 内置原生翻译字典（最高优先级）"""
        self._native_locale_dict = dict(native_dict) if native_dict else {}
        folded: Dict[str, str] = {}
        for key, value in self._native_locale_dict.items():
            raw = str(key or "").strip()
            if not raw:
                continue
            # Keep both a plain casefolded key and a Unicode-normalized form.
            # Community Mods differ in NFC/NFD spelling and occasionally leave
            # harmless whitespace or @@ wrappers around locale identifiers.
            for candidate in (raw, self._normalize_locale_key(raw)):
                if candidate:
                    folded[candidate.casefold()] = value
        self._native_locale_folded = folded
        self._native_locale_compact = self._build_compact_index(self._native_locale_dict)

    @staticmethod
    def _normalize_locale_key(value: str) -> str:
        text = str(value or "").strip()
        if text.startswith("@@") and text.endswith("@@"):
            text = text[2:-2].strip()
        text = unicodedata.normalize("NFKC", text)
        return " ".join(text.split())

    @classmethod
    def _compact_locale_key(cls, value: str) -> str:
        """Fold harmless punctuation/diacritic differences as a last resort."""
        text = unicodedata.normalize("NFKD", cls._normalize_locale_key(value)).casefold()
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        return "".join(ch for ch in text if ch.isalnum())

    @classmethod
    def _build_folded_index(cls, values: Dict[str, str]) -> Dict[str, str]:
        folded: Dict[str, str] = {}
        for key, value in (values or {}).items():
            normalized = cls._normalize_locale_key(key)
            if normalized:
                folded[normalized.casefold()] = value
        return folded

    @classmethod
    def _build_compact_index(cls, values: Dict[str, str]) -> Dict[str, Optional[str]]:
        compact: Dict[str, Optional[str]] = {}
        for key in (values or {}):
            normalized = cls._compact_locale_key(key)
            if not normalized:
                continue
            if normalized in compact and compact[normalized] != key:
                # Do not guess when two keys collapse to the same spelling.
                compact[normalized] = None
            else:
                compact[normalized] = key
        return compact

    @classmethod
    def _unique_lookup_candidates(cls, source: str, candidates: Optional[List[str]]) -> List[str]:
        result: List[str] = []
        seen: set[str] = set()
        for value in [source, *(candidates or [])]:
            text = str(value or "").strip()
            if not text:
                continue
            normalized = cls._normalize_locale_key(text)
            folded = normalized.casefold()
            if not folded or folded in seen:
                continue
            seen.add(folded)
            result.append(text)
        return result

    @classmethod
    def _lookup_index(
        cls,
        values: Dict[str, str],
        folded: Dict[str, str],
        compact: Dict[str, Optional[str]],
        candidates: List[str],
    ) -> Tuple[bool, str, str]:
        for candidate in candidates:
            normalized = cls._normalize_locale_key(candidate)
            folded_key = normalized.casefold()
            if folded_key in folded:
                return True, candidate, str(folded[folded_key] or "")
        for candidate in candidates:
            compact_key = cls._compact_locale_key(candidate)
            original = compact.get(compact_key) if compact_key else None
            if original:
                return True, original, str(values.get(original, "") or "")
        return False, "", ""

    def set_ufl_mod(self, ufl_mod_path: Path):
        """设置 UFL 汉化 mod 路径并加载翻译字典"""
        self.set_ufl_mods([ufl_mod_path] if ufl_mod_path else [])

    def set_ufl_mods(self, ufl_mod_paths: List[Path]) -> None:
        """Load and merge several installed UFL/localization packages."""
        paths = [Path(path) for path in (ufl_mod_paths or []) if path]
        self._ufl_paths = paths
        self._ufl_path = paths[-1] if paths else None
        merged: Dict[str, str] = {}
        for path in paths:
            for key, value in self._extract_ufl_translations(path).items():
                if key:
                    merged[key] = value
        self._ufl_dict = merged
        self._ufl_dict_folded = self._build_folded_index(self._ufl_dict)
        self._ufl_dict_compact = self._build_compact_index(self._ufl_dict)

    def set_target_locale(self, locale: str) -> bool:
        """切换目标语言。若成功则重新加载 UFL 翻译。返回是否成功。"""
        if locale not in self.SUPPORTED_LOCALES:
            return False
        self._target_locale = locale
        if self._ufl_paths:
            self.set_ufl_mods(self._ufl_paths)
        return True

    def get_target_locale(self) -> str:
        return self._target_locale

    def _is_direct_target_text(self, value: str) -> bool:
        """Whether a literal def value is already written in the target language."""
        text = str(value or "")
        if self._target_locale in ("zh_cn", "zh_tw"):
            return bool(re.search(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", text))
        return False

    def _load_local_dict(self):
        if self._dict_path.exists():
            try:
                with open(self._dict_path, "r", encoding="utf-8") as f:
                    self._local_dict = json.load(f)
            except Exception:
                self._local_dict = {}
        self._local_dict_folded = self._build_folded_index(self._local_dict)
        self._local_dict_compact = self._build_compact_index(self._local_dict)

    def save_local_dict(self):
        self._config_dir.mkdir(parents=True, exist_ok=True)
        with open(self._dict_path, "w", encoding="utf-8") as f:
            json.dump(self._local_dict, f, ensure_ascii=False, indent=2)

    def _extract_ufl_translations(self, ufl_path: Path) -> Dict[str, str]:
        """从 himeno_sena.ufl.scs 中提取所有翻译对（按当前 target_locale）"""
        result: Dict[str, str] = {}
        if not ufl_path or not ufl_path.exists():
            return result
        prefix = f"locale/{self._target_locale}/"
        try:
            with zipfile.ZipFile(ufl_path, "r") as zf:
                for name in zf.namelist():
                    normalized_name = str(name or "").replace("\\", "/").lstrip("./").casefold()
                    if not normalized_name.startswith(prefix.casefold()):
                        continue
                    if not normalized_name.endswith((".sii", ".sui")):
                        continue
                    try:
                        data = zf.read(name)
                        text = data.decode("utf-8", errors="replace")
                    except Exception:
                        continue
                    pairs = self._parse_localization_db(text)
                    for k, v in pairs:
                        if k and v and k not in result:
                            result[k] = v
        except Exception:
            pass
        return result

    def _parse_localization_db(self, text: str) -> List[Tuple[str, str]]:
        """解析 localization_db 中的 key[]/val[] 平行数组"""
        pairs: List[Tuple[str, str]] = []
        keys: List[str] = []
        vals: List[str] = []
        for m in re.finditer(r'key\[\]\s*:\s*"((?:[^"\\]|\\.)*)"', text):
            keys.append(self._unescape(m.group(1)))
        for m in re.finditer(r'val\[\]\s*:\s*"((?:[^"\\]|\\.)*)"', text):
            vals.append(self._unescape(m.group(1)))
        for i, key in enumerate(keys):
            pairs.append((key, vals[i] if i < len(vals) else ""))
        return pairs

    def _unescape(self, s: str) -> str:
        return (s.replace("\\r", "\r")
                .replace("\\n", "\n")
                .replace("\\t", "\t")
                .replace('\\"', '"')
                .replace("\\\\", "\\"))

    def _locale_to_lang_code(self, locale: str) -> str:
        """将 locale 名转为 MyMemory API 使用的语言代码"""
        if locale == "zh_cn":
            return "zh"
        if locale == "zh_tw":
            return "zh-TW"
        if locale in ("en_gb", "en_us"):
            return "en"
        if locale in ("pt_br", "pt_pt"):
            return "pt"
        if locale in ("sr_sp", "sr_sr"):
            return "sr"
        return locale.split("_")[0]

    def translate(self, source: str, category: str = "city", source_mod: str = "",
                 allow_api: bool = False, *, locale_key: str = "",
                 def_locale_key_present: bool = True, unit_name: str = "",
                 lookup_candidates: Optional[List[str]] = None) -> TranslationEntry:
        """Resolve one entry from local/native dictionaries.

        Online translation is opt-in.  The dialog uses this method while
        rendering scan results, so the default must never block the GUI on a
        network request.  ``batch_translate`` is the explicit online path.
        """
        if not source or not source.strip():
            return TranslationEntry(source=source, status="pending", category=category, source_mod=source_mod)

        normalized_source = self._normalize_locale_key(source)
        folded_source = normalized_source.casefold()
        candidates = self._unique_lookup_candidates(source, lookup_candidates)
        locale_key_present = (
            source in self._native_locale_dict
            or source.casefold() in self._native_locale_folded
            or folded_source in self._native_locale_folded
        )

        def entry(status: str, translated: str = "", matched_key: str = "") -> TranslationEntry:
            return TranslationEntry(
                source=source,
                translated=translated,
                status=status,
                category=category,
                source_mod=source_mod,
                locale_key_present=locale_key_present,
                def_locale_key_present=def_locale_key_present,
                locale_key=locale_key or source,
                unit_name=unit_name,
                lookup_candidates=candidates,
                matched_key=matched_key,
            )

        # 0. mod 内置原生翻译（最高优先级）
        # The game treats locale keys case-insensitively in practice.  Prefer
        # the folded table because it also preserves the last (highest
        # priority) spelling encountered while merging Mod locale files.
        native_found, native_key, native_value = self._lookup_index(
            self._native_locale_dict,
            self._native_locale_folded,
            self._native_locale_compact,
            candidates,
        )
        if native_found and native_value.strip():
            return entry("native", native_value, native_key)

        # Some map definitions contain a literal Chinese city/country name
        # instead of an @@locale_key@@ reference.  The game displays that text
        # directly, so it is already localized even though no locale entry
        # exists to merge.
        if self._is_direct_target_text(source):
            return entry("native", source)

        # 1. 本地字典
        local_found, local_key, local_value = self._lookup_index(
            self._local_dict,
            self._local_dict_folded,
            self._local_dict_compact,
            candidates,
        )
        if local_found:
            return entry("local", local_value, local_key)

        # 2. UFL内置库
        ufl_found, ufl_key, ufl_value = self._lookup_index(
            self._ufl_dict,
            self._ufl_dict_folded,
            self._ufl_dict_compact,
            candidates,
        )
        if ufl_found and ufl_value.strip():
            return entry("ufl", ufl_value, ufl_key)

        # 3. MyMemory API (only when explicitly requested by a caller)
        if allow_api:
            api_result = self._translate_via_api(source)
            if api_result:
                return entry("api", api_result)

        # 4. 翻译失败
        return entry("missing_value" if locale_key_present else "missing_locale")

    def _translate_via_api(self, text: str) -> Optional[str]:
        """调用 MyMemory 翻译 API（根据 target_locale 自动确定目标语言）"""
        try:
            tl = self._locale_to_lang_code(self._target_locale)
            langpair = f"en|{tl}"
            encoded = urllib.parse.quote(text)
            url = f"https://api.mymemory.translated.net/get?q={encoded}&langpair={langpair}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            translated = data.get("responseData", {}).get("translatedText", "")
            if not translated or translated.strip().lower() == text.strip().lower():
                return None
            if "City name" in translated or "PLEASE SELECT" in translated.upper():
                return None
            return translated
        except Exception:
            return None

    def update_translation(self, source: str, translated: str):
        """用户手动修正翻译，写入本地字典"""
        if source and translated:
            self._local_dict[source] = translated
            self._local_dict_folded = self._build_folded_index(self._local_dict)
            self._local_dict_compact = self._build_compact_index(self._local_dict)
            self.save_local_dict()

    def clear_translation(self, source: str) -> None:
        """删除手动翻译，让条目恢复为原生/UFL/待填写状态。"""
        if source and source in self._local_dict:
            del self._local_dict[source]
            self._local_dict_folded = self._build_folded_index(self._local_dict)
            self._local_dict_compact = self._build_compact_index(self._local_dict)
            self.save_local_dict()

    def batch_translate(self, entries: List[TranslationEntry], progress_callback=None,
                        should_stop=None) -> None:
        """批量翻译"""
        total = len(entries)
        changed = False
        for i, entry in enumerate(entries):
            if should_stop and should_stop():
                break
            if progress_callback:
                progress_callback(i, total, entry.source)
            if entry.status in ("native", "local", "ufl", "api"):
                continue
            if not entry.source:
                continue
            resolved = self.translate(
                entry.source,
                entry.category,
                entry.source_mod,
                allow_api=False,
                locale_key=entry.locale_key,
                def_locale_key_present=entry.def_locale_key_present,
                unit_name=entry.unit_name,
                lookup_candidates=entry.lookup_candidates,
            )
            entry.locale_key_present = resolved.locale_key_present
            entry.matched_key = resolved.matched_key
            if resolved.status in ("native", "local", "ufl"):
                entry.translated = resolved.translated
                entry.status = resolved.status
                continue
            api_result = self._translate_via_api(entry.source)
            if api_result:
                entry.translated = api_result
                entry.status = "api"
                self._local_dict[entry.source] = api_result
                self._local_dict_folded = self._build_folded_index(self._local_dict)
                self._local_dict_compact = self._build_compact_index(self._local_dict)
                changed = True
            else:
                entry.status = "missing_value" if entry.locale_key_present else "missing_locale"
        if progress_callback:
            progress_callback(total, total, "")
        if changed:
            # Persist successful API results so a later scan can resolve them
            # locally without repeating network calls.
            try:
                self.save_local_dict()
            except OSError:
                pass

    @staticmethod
    def _escape_sii(value: str) -> str:
        """Escape a string for a quoted SII field."""
        return (str(value or "")
                .replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("\r", "\\r")
                .replace("\n", "\\n")
                .replace("\t", "\\t"))

    def validate_dict_entry(self, key: str, value: str) -> List[str]:
        """校验词典条目，返回错误/警告信息列表。空列表表示通过。"""
        issues: List[str] = []

        # 1. key 为空/全空格
        if key is None or not key.strip():
            issues.append("错误: key 为空或全是空格")
            return issues

        # 2. 长度限制
        if len(key) > 200:
            issues.append(f"错误: key 长度 {len(key)} 超过 200 字符限制")
        if len(value) > 500:
            issues.append(f"错误: value 长度 {len(value)} 超过 500 字符限制")

        # 3. 控制字符检测 (\x00-\x08, \x0B-\x0C, \x0E-\x1F)
        ctrl_pattern = re.compile(r'[\x00-\x08\x0B-\x0C\x0E-\x1F]')
        if ctrl_pattern.search(key):
            issues.append("错误: key 含非法控制字符")
        if ctrl_pattern.search(value):
            issues.append("错误: value 含非法控制字符")

        # 4. 注入检测
        inject_patterns = [
            r'<\s*script',
            r'javascript\s*:',
            r'onerror\s*=',
            r'onload\s*=',
            r"'\s*;\s*DROP",
            r"'\s*;\s*DELETE",
            r'UNION\s+SELECT',
            r'OR\s+1\s*=\s*1',
            r'\{\{\s*\}\}',
            r'<%\s*%>',
            r'SiiNunit',
            r'unit\s*:',
            r'\.\./',
        ]
        combined_text = f"{key} {value}"
        for pat in inject_patterns:
            if re.search(pat, combined_text, re.IGNORECASE):
                issues.append(f"错误: 检测到可疑注入模式 [{pat}]")
                break

        # 5. 连续 \r 或 \n (警告: 截断到第一行)
        if '\r' in value or '\n' in value:
            issues.append("警告: value 含换行符，将截断到第一行")

        # 6. value 含 3+ \ufffd
        if value.count('\ufffd') >= 3:
            issues.append(f"错误: value 含 {value.count(chr(0xFFFD))} 个替换字符 (可能编码损坏)")

        # 7. key 中字符是否为 字母/数字/空格/_/-.
        key_pattern = re.compile(r'^[A-Za-z0-9 _/\-./]*$')
        if not key_pattern.match(key):
            issues.append("警告: key 含非标准字符 (允许: 字母/数字/空格/_/-/./)")

        return issues

    def import_custom_dict(self, dict_path: Path, merge: bool = True) -> Tuple[int, int, List[str]]:
        """导入用户自定义词典
        支持 JSON / CSV / TXT。
        Returns: (成功条数, 跳过条数, 错误/警告信息列表)
        """
        success_count = 0
        skip_count = 0
        messages: List[str] = []

        if not dict_path.exists():
            skip_count = 0
            messages.append(f"错误: 文件不存在: {dict_path}")
            return (success_count, skip_count, messages)

        suffix = dict_path.suffix.lower()
        raw_pairs: List[Tuple[str, str]] = []

        try:
            if suffix == ".json":
                with open(dict_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    for k, v in data.items():
                        raw_pairs.append((str(k), str(v)))
                else:
                    messages.append("错误: JSON 文件顶层必须是对象")
            elif suffix == ".csv":
                with open(dict_path, "r", encoding="utf-8-sig", newline="") as f:
                    reader = csv.reader(f)
                    for i, row in enumerate(reader, 1):
                        if len(row) >= 2:
                            raw_pairs.append((row[0], row[1]))
                        elif len(row) == 1 and row[0].strip():
                            messages.append(f"警告: 第{i}行 CSV 缺少 value 列，已跳过")
                            skip_count += 1
            elif suffix == ".txt":
                with open(dict_path, "r", encoding="utf-8-sig") as f:
                    for i, line in enumerate(f, 1):
                        line = line.rstrip("\n").rstrip("\r")
                        if not line.strip():
                            continue
                        if "=" in line:
                            k, v = line.split("=", 1)
                            raw_pairs.append((k.strip(), v.strip()))
                        elif ":" in line:
                            k, v = line.split(":", 1)
                            raw_pairs.append((k.strip(), v.strip()))
                        else:
                            messages.append(f"警告: 第{i}行 TXT 无 = 或 : 分隔符，已跳过")
                            skip_count += 1
            else:
                messages.append(f"错误: 不支持的文件格式: {suffix}")
                return (success_count, skip_count, messages)
        except Exception as e:
            messages.append(f"错误: 读取文件失败: {e}")
            return (success_count, skip_count, messages)

        # 校验并导入
        valid_pairs: Dict[str, str] = {}
        for idx, (k, v) in enumerate(raw_pairs, 1):
            # 先处理换行警告 (截断到第一行)
            v_clean = v
            if '\r' in v_clean or '\n' in v_clean:
                v_clean = re.split(r'[\r\n]', v_clean)[0]

            issues = self.validate_dict_entry(k, v_clean)
            has_error = any(msg.startswith("错误:") for msg in issues)
            for issue in issues:
                messages.append(f"[第{idx}项] {issue}")
            if has_error:
                skip_count += 1
                continue
            if not k or not v_clean:
                skip_count += 1
                continue
            if k in valid_pairs:
                messages.append(f"警告: 第{idx}项 key 重复，后续值覆盖前者")
            valid_pairs[k] = v_clean

        success_count = len(valid_pairs)

        if merge:
            self._local_dict.update(valid_pairs)
            self._local_dict_folded = self._build_folded_index(self._local_dict)
            self._local_dict_compact = self._build_compact_index(self._local_dict)
            try:
                self.save_local_dict()
            except Exception as e:
                messages.append(f"错误: 保存本地字典失败: {e}")

        return (success_count, skip_count, messages)

    def generate_l10n_mod(self, result: L10nResult, output_path: Path, mod_name: str = "Generated L10n") -> Path:
        """生成汉化 mod .scs 文件。

        Locale 按来源 Mod 拆分为多个 SII，便于审阅和维护。对于 def 中
        缺少 localized 字段的城市，同时生成一个最小 city_data 覆盖定义，
        将 ``city_name_localized`` 补成 ``@@Key@@``。
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        display_name_suffix = self.LOCALE_DISPLAY_NAMES.get(self._target_locale, self._target_locale)
        full_mod_name = f"{mod_name} ({display_name_suffix})"

        def _safe_name(value: str) -> str:
            value = re.sub(r'[^A-Za-z0-9._-]+', '_', str(value or '').strip())
            return (value.strip('._-') or 'unknown_mod')[:80]

        def _group_entries(entries):
            groups = {}
            for e in entries:
                if not e.translated:
                    continue
                groups.setdefault(e.source_mod or 'unknown_mod', []).append(e)
            return groups

        grouped = {}
        for category, entries in (
            ('Cities', result.cities),
            ('Countries', result.countries),
            ('Ferries', result.ferries),
            ('Hints', result.hints),
        ):
            for source_mod, values in _group_entries(entries).items():
                grouped.setdefault(source_mod, {}).setdefault(category, []).extend(values)

        locale_files = []
        for source_mod, categories in grouped.items():
            lines = ['SiiNunit', '{', 'localization_db : .localization', '{']
            for category in ('Cities', 'Countries', 'Ferries', 'Hints'):
                values = categories.get(category, [])
                if not values:
                    continue
                lines.append(f'\t# {_safe_name(source_mod)} {category}')
                for e in values:
                    lines.append(f'\tkey[]: "{self._escape_sii(e.source)}"')
                    lines.append(f'\tval[]: "{self._escape_sii(e.translated)}"')
            lines.extend(['}', '}'])
            filename = (
                'local_module.generated.sii'
                if len(grouped) == 1
                else f'local_module.{_safe_name(source_mod)}.sii'
            )
            locale_files.append((f'locale/{self._target_locale}/{filename}', '\n'.join(lines)))

        # Fallback file keeps the output valid even when every entry is still
        # untranslated. Normally grouped contains at least one source Mod.
        if not locale_files:
            locale_files.append((
                f'locale/{self._target_locale}/local_module.generated.sii',
                'SiiNunit\n{\nlocalization_db : .localization\n{\n}\n}',
            ))

        manifest = (
            'SiiNunit\n{\nmod_package : .unnamed\n{\n'
            f'\tpackage_version: "1.0"\n'
            f'\tdisplay_name: "{self._escape_sii(full_mod_name)}"\n'
            f'\tauthor: "ETS2ModManager"\n'
            f'\tcategory[]: "map"\n'
            f'\tdescription_file: "description.txt"\n'
            '}\n}\n'
        )

        desc = f"{full_mod_name}\n由 ETS2 Mod Manager 自动生成\n"

        # Generate def overrides for units that had no localized field.
        # Keep each source Mod and entity type in its own SII file.
        def_groups = {}
        def_specs = (
            ('Cities', 'city', result.cities, 'city_data', 'city_name_localized'),
            ('Countries', 'country', result.countries, 'country_data', 'name_localized'),
            ('Ferries', 'ferry', result.ferries, 'ferry_data', 'ferry_name_localized'),
        )
        for label, folder, entries, unit_type, localized_field in def_specs:
            for e in entries:
                # Add the missing localized field even before a translation is
                # entered. The UI then lets the user fill the locale value by
                # hand; automatic translation is deliberately not involved.
                if e.def_locale_key_present or not e.unit_name:
                    continue
                key = e.locale_key or e.source
                if key:
                    def_groups.setdefault((folder, label, e.source_mod or 'unknown_mod'), []).append((e, key, unit_type, localized_field))

        def_files = {}
        for (folder, label, source_mod), entries in def_groups.items():
            lines = ['SiiNunit', '{', f'# {_safe_name(source_mod)} {label}']
            for e, key, unit_type, localized_field in entries:
                lines.extend([
                    # unit_name comes from the SII parser's identifier token
                    # and must stay unquoted so it overrides the original.
                    f'{unit_type} : {e.unit_name}',
                    '{',
                    f'\t{localized_field}: "@@{self._escape_sii(key)}@@"',
                    '}',
                ])
            lines.append('}')
            def_files[f'def/{folder}/generated_{_safe_name(source_mod)}.sii'] = '\n'.join(lines) + '\n'

        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for path, content in locale_files:
                zf.writestr(path, content)
            for path, content in def_files.items():
                zf.writestr(path, content)
            zf.writestr("manifest.sii", manifest)
            zf.writestr("description.txt", desc)

        return output_path
