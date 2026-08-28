"""ETS2 游戏数据提取层
从已启用 mod 的 SCS 包中提取城市/国家/港口数据。
"""
from __future__ import annotations

import re
import tempfile
import shutil
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from .sii_parser import parse_sii, SiiUnit
from .scs_archive import ScsArchiveReader


@dataclass
class CityData:
    unit_name: str = ""
    city_name: str = ""
    short_name: str = ""
    country: str = ""
    source_mod: str = ""


@dataclass
class CountryData:
    unit_name: str = ""
    name: str = ""
    country_code: str = ""
    source_mod: str = ""


@dataclass
class FerryData:
    unit_name: str = ""
    ferry_name: str = ""
    source_mod: str = ""


@dataclass
class GameDataResult:
    cities: List[CityData] = field(default_factory=list)
    countries: List[CountryData] = field(default_factory=list)
    ferries: List[FerryData] = field(default_factory=list)
    city_names: List[str] = field(default_factory=list)
    country_names: List[str] = field(default_factory=list)
    ferry_names: List[str] = field(default_factory=list)
    native_locale_dict: Dict[str, str] = field(default_factory=dict)


@dataclass
class FileWithPriority:
    file_path: str
    file_text: str
    source_mod: str
    priority: int


def _parse_include_paths(text: str) -> List[str]:
    """从 SII 文本中提取 @include "path" 指令"""
    paths = []
    for m in re.finditer(r'@include\s+"([^"]+)"', text):
        paths.append(m.group(1))
    return paths


def _extract_cities_from_text(text: str, source_mod: str) -> List[CityData]:
    units = parse_sii(text)
    result = []
    for u in units:
        if u.unit_type != "city_data":
            continue
        c = CityData(
            unit_name=u.unit_name,
            city_name=u.get("city_name", "") or "",
            short_name=u.get("short_city_name", "") or "",
            country=u.get("country", "") or "",
            source_mod=source_mod,
        )
        if c.city_name:
            result.append(c)
    return result


def _extract_countries_from_text(text: str, source_mod: str) -> List[CountryData]:
    units = parse_sii(text)
    result = []
    for u in units:
        if u.unit_type != "country_data":
            continue
        c = CountryData(
            unit_name=u.unit_name,
            name=u.get("name", "") or "",
            country_code=u.get("country_code", "") or "",
            source_mod=source_mod,
        )
        if c.name:
            result.append(c)
    return result


def _extract_ferries_from_text(text: str, source_mod: str) -> List[FerryData]:
    units = parse_sii(text)
    result = []
    for u in units:
        if u.unit_type != "ferry_data":
            continue
        f = FerryData(
            unit_name=u.unit_name,
            ferry_name=u.get("ferry_name", "") or "",
            source_mod=source_mod,
        )
        if f.ferry_name:
            result.append(f)
    return result


def _find_def_files(reader: ScsArchiveReader, base_name: str) -> List[str]:
    """查找 def/ 下的索引文件，包括 infix 多文件"""
    found = []
    candidates = []

    if reader._mode == "zip" and reader._zf:
        for name in reader._zf.namelist():
            lower = name.lower()
            if re.match(rf"^def/{base_name}(\.[^/]+)?\.sii$", lower):
                candidates.append(name)
            elif re.match(rf"^{base_name}(\.[^/]+)?\.sii$", lower) and "/" not in lower:
                candidates.append(name)
    elif reader._mode == "dir":
        def_dir = reader.path / "def"
        if def_dir.exists():
            for p in def_dir.iterdir():
                if p.is_file() and re.match(rf"^{base_name}(\..+)?\.sii$", p.name, re.IGNORECASE):
                    candidates.append(f"def/{p.name}")

    base_file = f"def/{base_name}.sii"
    if base_file in candidates:
        found.append(base_file)
        candidates.remove(base_file)
    found.extend(candidates)
    return found


def _unescape_locale(s: str) -> str:
    """反转义 SII 字符串中的 \\n / \\t / \\\\ """
    return s.replace("\\n", "\n").replace("\\t", "\t").replace("\\\\", "\\")


def _parse_localization_db(text: str) -> List[Tuple[str, str]]:
    """解析 localization_db 中的 key[]/val[] 平行数组"""
    pairs: List[Tuple[str, str]] = []
    keys: List[str] = []
    vals: List[str] = []
    for m in re.finditer(r'key\[\]\s*:\s*"((?:[^"\\]|\\.)*)"', text):
        keys.append(_unescape_locale(m.group(1)))
    for m in re.finditer(r'val\[\]\s*:\s*"((?:[^"\\]|\\.)*)"', text):
        vals.append(_unescape_locale(m.group(1)))
    for i in range(min(len(keys), len(vals))):
        pairs.append((keys[i], vals[i]))
    return pairs


def _find_locale_files(reader: ScsArchiveReader, target_locale: str) -> List[str]:
    """按 target_locale 查找 locale/{target_locale}/local_module.*.sii"""
    found: List[str] = []
    if reader._mode == "zip" and reader._zf:
        for name in reader._zf.namelist():
            lower = name.lower()
            prefix = f"locale/{target_locale}/local_module.".lower()
            if lower.startswith(prefix) and lower.endswith(".sii"):
                found.append(name)
    elif reader._mode == "dir":
        locale_dir = reader.path / "locale" / target_locale
        if locale_dir.exists():
            for p in locale_dir.iterdir():
                if p.is_file() and p.name.lower().startswith("local_module.") and p.name.lower().endswith(".sii"):
                    found.append(f"locale/{target_locale}/{p.name}")
    return found


def _extract_native_locale(reader: ScsArchiveReader, target_locale: str = "zh_cn") -> Dict[str, str]:
    """从 mod 包的 locale/{target_locale}/ 目录提取翻译字典"""
    result: Dict[str, str] = {}
    for lf in _find_locale_files(reader, target_locale):
        text = reader.read_text(lf)
        if not text:
            continue
        for k, v in _parse_localization_db(text):
            if k and v and k not in result:
                result[k] = v
    return result


# legacy, 仅用于单元测试，生产流程不再调用
def extract_from_mod(mod_path: str, mod_display_name: str = "") -> Tuple[List[CityData], List[CountryData], List[FerryData], Dict[str, str]]:
    """legacy: 从单个 mod 包中提取城市/国家/港口数据，并附带 mod 内置 locale/zh_cn 翻译字典"""
    cities: List[CityData] = []
    countries: List[CountryData] = []
    ferries: List[FerryData] = []
    native_locale: Dict[str, str] = {}
    source = mod_display_name or mod_path

    try:
        reader = ScsArchiveReader(mod_path)
    except Exception:
        return cities, countries, ferries, native_locale

    if reader._mode == "external":
        reader.close()
        return cities, countries, ferries, native_locale

    _seen_inc: set[str] = set()
    for cf in _find_def_files(reader, "city"):
        text = reader.read_text(cf)
        if not text:
            continue
        for inc in _parse_include_paths(text):
            if inc in _seen_inc:
                continue
            _seen_inc.add(inc)
            sui_text = reader.read_text(inc)
            if sui_text:
                cities.extend(_extract_cities_from_text(sui_text, source))
        cities.extend(_extract_cities_from_text(text, source))

    _seen_inc2: set[str] = set()
    for cf in _find_def_files(reader, "country"):
        text = reader.read_text(cf)
        if not text:
            continue
        for inc in _parse_include_paths(text):
            if inc in _seen_inc2:
                continue
            _seen_inc2.add(inc)
            sui_text = reader.read_text(inc)
            if sui_text:
                countries.extend(_extract_countries_from_text(sui_text, source))
        countries.extend(_extract_countries_from_text(text, source))

    _seen_inc3: set[str] = set()
    for ff in _find_def_files(reader, "ferry"):
        text = reader.read_text(ff)
        if not text:
            continue
        for inc in _parse_include_paths(text):
            if inc in _seen_inc3:
                continue
            _seen_inc3.add(inc)
            sub_text = reader.read_text(inc)
            if sub_text:
                ferries.extend(_extract_ferries_from_text(sub_text, source))
        ferries.extend(_extract_ferries_from_text(text, source))

    native_locale = _extract_native_locale(reader, "zh_cn")

    reader.close()
    return cities, countries, ferries, native_locale


# legacy, 仅用于单元测试，生产流程不再调用
def merge_game_data(
    mod_results: List[Tuple[str, List[CityData], List[CountryData], List[FerryData], Dict[str, str]]]
) -> GameDataResult:
    """legacy: 合并多个 mod 的提取结果，按优先级去重"""
    result = GameDataResult()
    seen_city_names = set()
    seen_country_names = set()
    seen_ferry_names = set()

    for mod_path, cities, countries, ferries, native_locale in mod_results:
        for c in cities:
            if c.city_name and c.city_name not in seen_city_names:
                seen_city_names.add(c.city_name)
                result.cities.append(c)
                result.city_names.append(c.city_name)
        for c in countries:
            if c.name and c.name not in seen_country_names:
                seen_country_names.add(c.name)
                result.countries.append(c)
                result.country_names.append(c.name)
        for f in ferries:
            if f.ferry_name and f.ferry_name not in seen_ferry_names:
                seen_ferry_names.add(f.ferry_name)
                result.ferries.append(f)
                result.ferry_names.append(f.ferry_name)
        for k, v in native_locale.items():
            if k and v and k not in result.native_locale_dict:
                result.native_locale_dict[k] = v

    return result


def collect_all_def_files(
    active_mods: List[Tuple[str, str]]
) -> Tuple[Dict[str, FileWithPriority], Dict[str, Dict[str, str]]]:
    """
    扫描所有已启用mod，收集def文件和locale翻译文件，路径冲突时保留最高优先级mod的版本

    Args:
        active_mods: [(mod_path, display_name), ...] 已按优先级排序（0为最高）

    Returns:
        (def_files_dict: {file_path: FileWithPriority},
         native_locale_by_lang: {locale_name: {key: val}})
    """
    def_files_dict: Dict[str, FileWithPriority] = {}
    native_locale_by_lang: Dict[str, Dict[str, str]] = {}

    for priority, (mod_path, display_name) in enumerate(active_mods):
        source_mod = display_name or mod_path
        try:
            reader = ScsArchiveReader(mod_path)
        except Exception:
            continue

        if reader._mode == "external":
            # Encrypted SCS# packages cannot enumerate entries directly. For
            # localization, fully extract them on demand so def/locale files
            # are discoverable instead of silently skipped.
            try:
                from services.external_extractor_service import extract_archive_to_directory
                tmp_root = Path(tempfile.mkdtemp(prefix="ets2mm_l10n_"))
                if extract_archive_to_directory(mod_path, tmp_root):
                    reader.close()
                    reader = ScsArchiveReader(tmp_root)
                else:
                    shutil.rmtree(tmp_root, ignore_errors=True)
                    reader.close()
                    continue
            except Exception:
                reader.close()
                continue

        all_files: List[str] = []
        if reader._mode == "zip" and reader._zf:
            all_files = reader._zf.namelist()
        elif reader._mode == "dir":
            def _walk_dir(base_dir, prefix=""):
                results = []
                for p in base_dir.iterdir():
                    rel = f"{prefix}/{p.name}" if prefix else p.name
                    if p.is_dir():
                        results.extend(_walk_dir(p, rel))
                    elif p.is_file():
                        results.append(rel)
                return results
            all_files = _walk_dir(reader.path)

        locale_pattern = re.compile(r"^locale/([^/]+)/local_module\.[^/]+\.sii$", re.IGNORECASE)

        for fname in all_files:
            # Extractor output on Windows uses backslashes; normalize before
            # applying game-relative `def/` and `locale/` patterns.
            fname_norm = fname.replace("\\", "/")
            lower = fname_norm.lower()
            if lower.startswith("def/") and (lower.endswith(".sii") or lower.endswith(".sui")):
                if fname_norm not in def_files_dict:
                    text = reader.read_text(fname_norm)
                    if text is not None:
                        def_files_dict[fname_norm] = FileWithPriority(
                            file_path=fname_norm,
                            file_text=text,
                            source_mod=source_mod,
                            priority=priority,
                        )
            else:
                m = locale_pattern.match(fname_norm)
                if m:
                    lang = m.group(1).lower()
                    if lang not in native_locale_by_lang:
                        native_locale_by_lang[lang] = {}
                    text = reader.read_text(fname_norm)
                    if text:
                        for k, v in _parse_localization_db(text):
                            if k and v and k not in native_locale_by_lang[lang]:
                                native_locale_by_lang[lang][k] = v

        reader.close()
        if 'tmp_root' in locals():
            shutil.rmtree(tmp_root, ignore_errors=True)
            del tmp_root

    return def_files_dict, native_locale_by_lang


def _parse_sii_base_with_infix(
    merged_def_files: Dict[str, FileWithPriority],
    base_name: str,
    city_units: Dict[str, CityData],
    country_units: Dict[str, CountryData],
    ferry_units: Dict[str, FerryData],
):
    """
    按游戏加载顺序解析 def/{base_name}.sii + def/{base_name}.*.sii
    顺序：先 base.sii 内的 include -> base.sii 自身 -> 按字典序 infix 文件（include->自身）
    """
    index_files: List[str] = []
    base_file = f"def/{base_name}.sii"
    if base_file in merged_def_files:
        index_files.append(base_file)

    infix_files = []
    for fp in merged_def_files:
        if not fp.lower().startswith(f"def/{base_name}."):
            continue
        if not fp.lower().endswith(".sii"):
            continue
        if fp == base_file:
            continue
        infix_files.append(fp)
    infix_files.sort()
    index_files.extend(infix_files)

    for idx_file in index_files:
        fw = merged_def_files[idx_file]
        source_mod = fw.source_mod
        text = fw.file_text
        if not text:
            continue

        includes = _parse_include_paths(text)
        for inc in includes:
            inc_norm = inc.replace("\\", "/")
            if inc_norm in merged_def_files:
                inc_fw = merged_def_files[inc_norm]
                inc_text = inc_fw.file_text
                if inc_text:
                    inc_source = inc_fw.source_mod
                    if base_name == "city":
                        for c in _extract_cities_from_text(inc_text, inc_source):
                            if c.unit_name:
                                city_units[c.unit_name] = c
                    elif base_name == "country":
                        for c in _extract_countries_from_text(inc_text, inc_source):
                            if c.unit_name:
                                country_units[c.unit_name] = c
                    elif base_name == "ferry":
                        for f in _extract_ferries_from_text(inc_text, inc_source):
                            if f.unit_name:
                                ferry_units[f.unit_name] = f

        if base_name == "city":
            for c in _extract_cities_from_text(text, source_mod):
                if c.unit_name:
                    city_units[c.unit_name] = c
        elif base_name == "country":
            for c in _extract_countries_from_text(text, source_mod):
                if c.unit_name:
                    country_units[c.unit_name] = c
        elif base_name == "ferry":
            for f in _extract_ferries_from_text(text, source_mod):
                if f.unit_name:
                    ferry_units[f.unit_name] = f


def parse_from_merged_files(
    merged_def_files: Dict[str, FileWithPriority],
    native_locale: Dict[str, str],
) -> GameDataResult:
    """
    从已经按优先级合并好的def文件中，解析出城市/国家/港口数据 + 翻译字典

    执行顺序严格模拟游戏加载：
    1. 读 def/city.sii + 所有 infix def/city.*.sii
    2. 从上述文件解析 @include 指令
    3. 从 merged_def_files 中读取 .sui 文件（如果存在——不存在说明这个include来自base game但我们没扫描它，跳过）
    4. 解析 unit 时按 unit_name 最终去重：后面覆盖前面（因为infix文件可能在base之后重写unit）
    5. 同理处理 country/ferry
    """
    city_units: Dict[str, CityData] = {}
    country_units: Dict[str, CountryData] = {}
    ferry_units: Dict[str, FerryData] = {}

    _parse_sii_base_with_infix(merged_def_files, "city", city_units, country_units, ferry_units)
    _parse_sii_base_with_infix(merged_def_files, "country", city_units, country_units, ferry_units)
    _parse_sii_base_with_infix(merged_def_files, "ferry", city_units, country_units, ferry_units)

    # Encrypted/custom maps often keep readable leaf definitions under
    # def/city/*.sui, def/country/*.sui, etc., while their index .sii files are
    # protected. Parse every remaining SII/SUI directly as a fallback.
    parsed_paths = set()
    for base in ("city", "country", "ferry"):
        parsed_paths.add(f"def/{base}.sii")
        parsed_paths.update(p for p in merged_def_files if p.startswith(f"def/{base}.") and "/" not in p[len("def/"):])
    for path, fw in merged_def_files.items():
        if path in parsed_paths or not path.lower().endswith((".sii", ".sui")):
            continue
        text = fw.file_text
        if not text or text.startswith("3nK\x01"):
            continue
        try:
            for c in _extract_cities_from_text(text, fw.source_mod):
                if c.unit_name:
                    city_units[c.unit_name] = c
            for c in _extract_countries_from_text(text, fw.source_mod):
                if c.unit_name:
                    country_units[c.unit_name] = c
            for f in _extract_ferries_from_text(text, fw.source_mod):
                if f.unit_name:
                    ferry_units[f.unit_name] = f
        except Exception:
            continue

    result = GameDataResult()
    result.native_locale_dict = dict(native_locale)

    for c in city_units.values():
        result.cities.append(c)
        result.city_names.append(c.city_name)
    for c in country_units.values():
        result.countries.append(c)
        result.country_names.append(c.name)
    for f in ferry_units.values():
        result.ferries.append(f)
        result.ferry_names.append(f.ferry_name)

    return result


def extract_game_data_for_active_mods(
    active_mods: List[Tuple[str, str]],
    target_locale: str = "zh_cn",
) -> GameDataResult:
    """
    生产环境主入口
    流程：按优先级扫描所有mod -> 路径冲突时高优先级覆盖 -> 解析def -> 最终按unit_name去重
    """
    def_files, locales_by_lang = collect_all_def_files(active_mods)
    native_locale = locales_by_lang.get(target_locale, {})
    result = parse_from_merged_files(def_files, native_locale)
    return result
