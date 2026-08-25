"""汉化服务层
四层翻译：mod内置原生 -> 本地字典 -> UFL内置翻译库 -> MyMemory API -> 标红待手动补全
"""
from __future__ import annotations

import csv
import json
import re
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
    status: str = "pending"  # "native" / "local" / "ufl" / "api" / "pending" / "failed"
    source_mod: str = ""
    category: str = "city"


@dataclass
class L10nResult:
    cities: List[TranslationEntry] = field(default_factory=list)
    countries: List[TranslationEntry] = field(default_factory=list)
    ferries: List[TranslationEntry] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.cities) + len(self.countries) + len(self.ferries)

    @property
    def translated_count(self) -> int:
        return sum(1 for e in self.all_entries if e.status in ("native", "local", "ufl", "api"))

    @property
    def pending_count(self) -> int:
        return sum(1 for e in self.all_entries if e.status in ("pending", "failed"))

    @property
    def all_entries(self) -> List[TranslationEntry]:
        return self.cities + self.countries + self.ferries


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
        self._local_dict: Dict[str, str] = {}
        self._ufl_dict: Dict[str, str] = {}
        self._native_locale_dict: Dict[str, str] = {}
        self._target_locale = target_locale if target_locale in self.SUPPORTED_LOCALES else "zh_cn"
        self._load_local_dict()

    def set_native_locale(self, native_dict: Dict[str, str]):
        """设置 mod 内置原生翻译字典（最高优先级）"""
        self._native_locale_dict = dict(native_dict) if native_dict else {}

    def set_ufl_mod(self, ufl_mod_path: Path):
        """设置 UFL 汉化 mod 路径并加载翻译字典"""
        self._ufl_path = ufl_mod_path
        self._ufl_dict = self._extract_ufl_translations(ufl_mod_path)

    def set_target_locale(self, locale: str) -> bool:
        """切换目标语言。若成功则重新加载 UFL 翻译。返回是否成功。"""
        if locale not in self.SUPPORTED_LOCALES:
            return False
        self._target_locale = locale
        if self._ufl_path:
            self._ufl_dict = self._extract_ufl_translations(self._ufl_path)
        return True

    def get_target_locale(self) -> str:
        return self._target_locale

    def _load_local_dict(self):
        if self._dict_path.exists():
            try:
                with open(self._dict_path, "r", encoding="utf-8") as f:
                    self._local_dict = json.load(f)
            except Exception:
                self._local_dict = {}

    def save_local_dict(self):
        self._config_dir.mkdir(parents=True, exist_ok=True)
        with open(self._dict_path, "w", encoding="utf-8") as f:
            json.dump(self._local_dict, f, ensure_ascii=False, indent=2)

    def _extract_ufl_translations(self, ufl_path: Path) -> Dict[str, str]:
        """从 himeno_sena.ufl.scs 中提取所有翻译对（按当前 target_locale）"""
        result: Dict[str, str] = {}
        if not ufl_path or not ufl_path.exists():
            return result
        prefix = f"locale/{self._target_locale}/local_module."
        try:
            with zipfile.ZipFile(ufl_path, "r") as zf:
                for name in zf.namelist():
                    if not name.startswith(prefix):
                        continue
                    if not name.endswith(".sii"):
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
        for i in range(min(len(keys), len(vals))):
            pairs.append((keys[i], vals[i]))
        return pairs

    def _unescape(self, s: str) -> str:
        return s.replace("\\n", "\n").replace("\\t", "\t").replace("\\\\", "\\")

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

    def translate(self, source: str, category: str = "city", source_mod: str = "") -> TranslationEntry:
        """翻译单个词条"""
        if not source or not source.strip():
            return TranslationEntry(source=source, status="pending", category=category, source_mod=source_mod)

        # 0. mod 内置原生翻译（最高优先级）
        if source in self._native_locale_dict:
            return TranslationEntry(
                source=source, translated=self._native_locale_dict[source],
                status="native", category=category, source_mod=source_mod
            )

        # 1. 本地字典
        if source in self._local_dict:
            return TranslationEntry(
                source=source, translated=self._local_dict[source],
                status="local", category=category, source_mod=source_mod
            )

        # 2. UFL内置库
        if source in self._ufl_dict:
            return TranslationEntry(
                source=source, translated=self._ufl_dict[source],
                status="ufl", category=category, source_mod=source_mod
            )

        # 3. MyMemory API
        api_result = self._translate_via_api(source)
        if api_result:
            return TranslationEntry(
                source=source, translated=api_result,
                status="api", category=category, source_mod=source_mod
            )

        # 4. 翻译失败
        return TranslationEntry(source=source, status="failed", category=category, source_mod=source_mod)

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
            self.save_local_dict()

    def batch_translate(self, entries: List[TranslationEntry], progress_callback=None) -> None:
        """批量翻译"""
        total = len(entries)
        for i, entry in enumerate(entries):
            if progress_callback:
                progress_callback(i, total, entry.source)
            if entry.status in ("native", "local", "ufl", "api"):
                continue
            if not entry.source:
                continue
            if entry.source in self._native_locale_dict:
                entry.translated = self._native_locale_dict[entry.source]
                entry.status = "native"
                continue
            if entry.source in self._local_dict:
                entry.translated = self._local_dict[entry.source]
                entry.status = "local"
                continue
            if entry.source in self._ufl_dict:
                entry.translated = self._ufl_dict[entry.source]
                entry.status = "ufl"
                continue
            api_result = self._translate_via_api(entry.source)
            if api_result:
                entry.translated = api_result
                entry.status = "api"
                self._local_dict[entry.source] = api_result
            else:
                entry.status = "failed"
        if progress_callback:
            progress_callback(total, total, "")

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
            try:
                self.save_local_dict()
            except Exception as e:
                messages.append(f"错误: 保存本地字典失败: {e}")

        return (success_count, skip_count, messages)

    def generate_l10n_mod(self, result: L10nResult, output_path: Path, mod_name: str = "Generated L10n") -> Path:
        """生成汉化 mod .scs 文件（按 target_locale 输出目录和显示名）"""
        output_path.parent.mkdir(parents=True, exist_ok=True)

        display_name_suffix = self.LOCALE_DISPLAY_NAMES.get(self._target_locale, self._target_locale)
        full_mod_name = f"{mod_name} ({display_name_suffix})"

        lines = ['SiiNunit', '{', 'localization_db : .localization', '{']

        lines.append('\t# Cities')
        for e in result.cities:
            if e.translated:
                lines.append(f'\tkey[]: "{e.source}"')
                lines.append(f'\tval[]: "{e.translated}"')

        lines.append('\t# Countries')
        for e in result.countries:
            if e.translated:
                lines.append(f'\tkey[]: "{e.source}"')
                lines.append(f'\tval[]: "{e.translated}"')

        lines.append('\t# Ferries')
        for e in result.ferries:
            if e.translated:
                lines.append(f'\tkey[]: "{e.source}"')
                lines.append(f'\tval[]: "{e.translated}"')

        lines.append('}')
        lines.append('}')
        sii_content = '\n'.join(lines)

        manifest = (
            'SiiNunit\n{\nmod_package : .unnamed\n{\n'
            f'\tpackage_version: "1.0"\n'
            f'\tdisplay_name: "{full_mod_name}"\n'
            f'\tauthor: "ETS2ModManager"\n'
            f'\tcategory[]: "map"\n'
            f'\tdescription_file: "description.txt"\n'
            '}\n}\n'
        )

        desc = f"{full_mod_name}\n由 ETS2 Mod Manager 自动生成\n"

        sii_file_path = f"locale/{self._target_locale}/local_module.generated.sii"

        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(sii_file_path, sii_content)
            zf.writestr("manifest.sii", manifest)
            zf.writestr("description.txt", desc)

        return output_path
