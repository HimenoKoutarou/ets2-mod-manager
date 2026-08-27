from __future__ import annotations

import os
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .models import Mod, ModManifest
from .scs_archive import ScsArchiveReader
from .sii_parser import parse_mods_info


"""
模组目录扫描器
扫描来源：
1. 本地 /mod 目录（.scs / .zip / 文件夹三种格式）
2. Steam Workshop content/227300 目录（每个目录是一个订阅模组）
3. （可选）mods_info.sii —— 用于给模组打上 "已被游戏识别" 的时间戳标记
"""


PACKAGE_SUFFIXES = {".scs", ".zip"}


def _unique_list(items: List[str]) -> List[str]:
    """去重且保序"""
    seen = set()
    out = []
    for it in items:
        if not it or it in seen:
            continue
        seen.add(it)
        out.append(it)
    return out



def _mod_id_from_package(path: Path) -> str:
    """根据包路径得到唯一 mod_id：
    - 文件：文件名去除扩展名
    - 目录：目录名本身
    """
    if path.is_dir():
        return path.name
    return path.stem


def _dir_size(p: Path, max_depth: int = 4) -> int:
    """递归统计目录大小（字节），限制最大深度避免符号链接环/巨型目录卡死。"""
    total = 0
    try:
        p_resolved = p.resolve()
        top_parts = len(p_resolved.parts)
        for root, _, files in os.walk(p, followlinks=False):
            # 深度限制
            try:
                depth = len(Path(root).resolve().parts) - top_parts
            except OSError:
                depth = -1
            if depth > max_depth:
                continue
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except OSError:
        pass
    return total




def _try_nested_scs(mod: Mod, package_path: Path) -> bool:
    """共享：遍历第一层子 .scs/.zip 包，解析 manifest/icon/description 填充到 mod。
    返回是否有字段被填充。"""
    filled = False
    try:
        sub_packages = sorted([pp for pp in package_path.iterdir()
                               if pp.is_file() and pp.suffix.lower() in (".scs", ".zip")])[:10]
    except OSError:
        sub_packages = []
    for sp in sub_packages:
        try:
            with ScsArchiveReader(sp) as r2:
                mf2 = r2.parse_manifest()
                if not mod.manifest.display_name and mf2.display_name:
                    mod.manifest = mf2
                    filled = True
                ic2 = r2.read_icon(mf2.icon_filename or mod.manifest.icon_filename or "")
                if ic2.is_available and not mod.icon.is_available:
                    mod.icon = ic2
                    filled = True
                d2 = r2.read_description(mf2.description_filename or mod.manifest.description_filename or "")
                if d2 and not mod.description:
                    mod.description = d2
                    filled = True
                if mod.manifest.display_name and mod.icon.is_available:
                    break
        except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile, ValueError, UnicodeDecodeError, LookupError):
            continue
        except Exception as _e:
            import sys as _sys_r6, traceback as _tb_r6
            print(f"[mod_scan] 子 .scs 嵌套兜底解析 意外异常 {type(_e).__name__}: {_e} -> {package_path}", file=_sys_r6.stderr)
            _tb_r6.print_exc(limit=1, file=_sys_r6.stderr)
            continue
    return filled


def _try_nested_subdir(mod: Mod, package_path: Path) -> bool:
    """共享：遍历 universal/数字版本号/alt 子目录，解析 manifest.sii + icon + description。
    返回是否有字段被填充。"""
    from .sii_parser import parse_sii
    from .models import ModIcon

    filled = False
    try:
        sub_dirs = [pp for pp in package_path.iterdir() if pp.is_dir()]
    except OSError:
        sub_dirs = []
    def _sort_key(d: Path):
        name = d.name
        if name.lower() == "universal":
            return (0, 0, name.lower())
        m = re.match(r"(\d+)", name)
        if m:
            return (1, -int(m.group(1)), name.lower())
        return (2, 0, name.lower())
    sub_dirs_sorted = sorted(sub_dirs, key=_sort_key)[:20]

    for sd in sub_dirs_sorted:
        mani = sd / "manifest.sii"
        if not mani.exists():
            continue
        try:
            text = mani.read_text(encoding="utf-8", errors="replace")
            units = parse_sii(text)
        except (OSError, ValueError, UnicodeDecodeError, LookupError):
            continue
        except Exception as _e:
            import sys as _sys_r6, traceback as _tb_r6
            print(f"[mod_scan] 子目录 manifest.sii 解析 意外异常 {type(_e).__name__}: {_e} -> {package_path}", file=_sys_r6.stderr)
            _tb_r6.print_exc(limit=1, file=_sys_r6.stderr)
            continue

        pkg = next((u for u in units if u.unit_type == "mod_package"),
                   units[0] if units else None)
        if not pkg:
            continue
        mf = mod.manifest if mod.manifest else ModManifest()
        if not mf.package_name:
            mf.package_name = pkg.unit_name or ""
        if not mf.package_version:
            mf.package_version = pkg.get("package_version", "") or ""
        if not mf.display_name:
            mf.display_name = pkg.get("display_name", "") or ""
            if mf.display_name:
                filled = True
        if not mf.author:
            mf.author = pkg.get("author", "") or ""
        if not mf.categories:
            mf.categories = [c for c in pkg.get_list("category") if c]
        if not mf.icon_filename:
            mf.icon_filename = pkg.get("icon", "") or ""
        if not mf.description_filename:
            mf.description_filename = pkg.get("description_file", "") or ""
        if not mf.compatible_versions:
            mf.compatible_versions = [v for v in pkg.get_list("compatible_versions") if v]
        if not mf.dlc_dependencies:
            mf.dlc_dependencies = [dd for dd in pkg.get_list("dlc_dependencies") if dd]
        mod.manifest = mf

        # icon 兜底
        if not mod.icon.is_available:
            candidates = []
            if mf.icon_filename:
                candidates.append(sd / mf.icon_filename)
            candidates += [
                sd / "mod_icon.jpg",
                sd / "icon.jpg",
                sd / "mod_icon.png",
                sd / "icon.png",
            ]
            for icp in candidates:
                try:
                    if icp.exists() and icp.is_file():
                        data = icp.read_bytes()
                        if data:
                            fmt = icp.suffix.lower().lstrip(".") or "jpg"
                            mod.icon = ModIcon(raw_bytes=data, format=fmt, source_path=str(icp))
                            filled = True
                            break
                except (OSError, ValueError):
                    continue
                except Exception as _e:
                    import sys as _sys_r6, traceback as _tb_r6
                    print(f"[mod_scan] 子目录 icon 读取 意外异常 {type(_e).__name__}: {_e} -> {package_path}", file=_sys_r6.stderr)
                    _tb_r6.print_exc(limit=1, file=_sys_r6.stderr)
                    continue

        # description 兜底
        if not mod.description:
            candidates = []
            if mf.description_filename:
                candidates.append(sd / mf.description_filename)
            candidates += [
                sd / "mod_description.txt",
                sd / "description.txt",
                sd / "mod.txt",
            ]
            for dp in candidates:
                try:
                    if dp.exists() and dp.is_file():
                        txt = dp.read_text(encoding="utf-8", errors="replace")
                        if txt:
                            mod.description = txt
                            filled = True
                            break
                except (OSError, ValueError, UnicodeDecodeError):
                    continue
                except Exception as _e:
                    import sys as _sys_r6, traceback as _tb_r6
                    print(f"[mod_scan] 子目录 description 读取 意外异常 {type(_e).__name__}: {_e} -> {package_path}", file=_sys_r6.stderr)
                    _tb_r6.print_exc(limit=1, file=_sys_r6.stderr)
                    continue

        if mod.manifest.display_name and mod.icon.is_available:
            break
    return filled


def _build_mod_from_package(package_path: Path, package_type: str,
                             mods_info_index: Dict[str, int],
                             skip_nested: bool = False) -> Mod:
    """对单个包路径构造 Mod 对象（尝试解析 manifest/icon/description）。

    性能优化：skip_nested=True 时跳过嵌套兜底解析（Workshop 多层版本目录
    的 manifest/icon/description 查找），让快速扫描阶段只读主包；
    嵌套兜底延后到 async_parse 阶段调 _enrich_nested_fallback 异步执行，
    避免扫描大量 Workshop 目录时同步阻塞。
    """
    mod_id = _mod_id_from_package(package_path)
    size = 0
    mtime = 0.0
    try:
        if package_path.is_dir():
            size = _dir_size(package_path)
            mtime = package_path.stat().st_mtime
        else:
            size = package_path.stat().st_size
            mtime = package_path.stat().st_mtime
    except OSError:
        pass

    mod = Mod(
        mod_id=mod_id,
        package_path=str(package_path),
        package_type=package_type,
        file_size=size,
        last_modified=mtime,
        mods_info_timestamp=mods_info_index.get(mod_id, 0),
    )

    # 尝试读取内容
    try:
        with ScsArchiveReader(package_path) as reader:
            mf = reader.parse_manifest()
            mod.manifest = mf
            mod.icon = reader.read_icon(mf.icon_filename or "")
            mod.description = reader.read_description(mf.description_filename or "")
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile, ValueError, UnicodeDecodeError, LookupError):
        pass
    except Exception as _e:
        import sys as _sys_r6, traceback as _tb_r6
        print(f"[mod_scan] 主包 manifest/icon/description 解析 意外异常 {type(_e).__name__}: {_e} -> {package_path}", file=_sys_r6.stderr)
        _tb_r6.print_exc(limit=1, file=_sys_r6.stderr)
        pass


    # 嵌套兜底 1/2：workshop 常把 manifest/icon 放在第一层子 .scs 包内
    need_nested = (not mod.manifest.display_name and not mod.icon.is_available and not mod.description)
    if skip_nested:
        if need_nested and package_path.is_dir():
            mod._needs_nested_fallback = True  # type: ignore[attr-defined]
        need_nested = False  # 本轮跳过
    if need_nested and package_path.is_dir():
        _try_nested_scs(mod, package_path)

    # 嵌套兜底 2/2：Workshop 常不放 .scs，manifest/icon 直接在子目录（universal / 128_content / alt 等）下
    need_nested2 = (not mod.manifest.display_name and not mod.icon.is_available and not mod.description)
    if need_nested2 and package_path.is_dir():
        _try_nested_subdir(mod, package_path)

    # 最后一道 fallback：manifest.display_name 为空的 mod（很多作者不写 display_name 字段）
    # 标题常常在 mod_description.txt / mod_info.txt 的第一行（纯文本）。
    if not mod.manifest.display_name:
        candidates_files = [mod.manifest.description_filename] if mod.manifest.description_filename else []
        candidates_files += ["mod_description.txt", "description.txt", "mod_info.txt", "mod.txt"]
        # 去重 + 过滤空
        seen = set()
        search_locs = []   # (kind, base_path, filename)
        # 1) 根 package
        search_locs.append(("ROOT", None, None))
        # 2) 第一层已知子目录（universal/、128_content/、alt/、zip 内所有层）
        # 实际读交给 extractor
        # description 兜底读取（只填充 mod.description，不再从中提取 display_name）
        pp = Path(mod.package_path)
        if not mod.description:
            try:
                with ScsArchiveReader(pp) as reader:
                    for fname in _unique_list(candidates_files):
                        txt = reader.read_text(fname)
                        if not txt:
                            for prefix in ["universal/", "alt/", "neu/"]:
                                txt = reader.read_text(prefix + fname)
                                if txt: break
                        if txt:
                            mod.description = txt.strip()
                            break
            except Exception:
                pass
        if not mod.description and pp.is_dir():
            for sd in [pp] + [d for d in pp.iterdir() if d.is_dir()][:10]:
                for fname in _unique_list(candidates_files):
                    fp = sd / fname
                    try:
                        if fp.exists() and fp.is_file():
                            txt = fp.read_text(encoding="utf-8", errors="replace")
                            if txt:
                                mod.description = txt.strip()
                                break
                    except Exception:
                        continue
                if mod.description: break

    # ===== 最终兜底：从 workshop 目录下子包文件名提取友好名 =====
    # 适用于：AES 加密 zip（无法读内部）、作者没写 display_name 的 universal.scs 等
    if (not mod.manifest.display_name or mod.manifest.display_name.strip().isdigit()) and package_path.is_dir():
        try:
            sub_files = sorted([pp for pp in package_path.iterdir()
                                if pp.is_file() and pp.suffix.lower() in (".scs", ".zip")])
        except OSError:
            sub_files = []
        BLACKLIST = {"universal", "content", "data", "base", "main", "mod", "package"}
        best_candidate = ""
        for sf in sub_files:
            stem = sf.stem
            cleaned = stem
            import re as _re
            m2 = _re.match(r"^(\d+)[_\-](.+)$", stem)
            if m2:
                cleaned = m2.group(2)
            cleaned2 = _re.sub(r"[_\-]?content$", "", cleaned, flags=_re.IGNORECASE).strip("_-")
            if cleaned2:
                cleaned = cleaned2
            if cleaned.lower() in BLACKLIST:
                continue
            if cleaned.isdigit():
                continue
            nice = cleaned.replace("_", " ").replace("-", " ").strip()
            if nice:
                nice = " ".join(w[:1].upper() + w[1:] if w else w for w in nice.split())
                if 2 <= len(nice) <= 80:
                    best_candidate = nice
                    break
        if best_candidate:
            mod.manifest.display_name = best_candidate

    # 如果最终 manifest.package_name 为空 / 点开头的垃圾（如 .package_name .manifest ""），fallback 到包路径的 mod_id
    if (not mod.manifest.package_name
            or mod.manifest.package_name.startswith(".")
            or mod.manifest.package_name.lower() in {"manifest", "package_name", "mods_info", "nameless", "mod_package"}):
        mod.manifest.package_name = mod.mod_id
    return mod


class ModScanner:
    def __init__(self,
                 local_mod_dir: Optional[Path],
                 workshop_dir: Optional[Path],
                 mods_info_path: Optional[Path] = None):
        self.local_mod_dir = Path(local_mod_dir) if local_mod_dir else None
        self.workshop_dir = Path(workshop_dir) if workshop_dir else None
        self.mods_info_path = Path(mods_info_path) if mods_info_path else None

    def load_mods_info_index(self) -> Dict[str, int]:
        if self.mods_info_path and self.mods_info_path.exists():
            try:
                return parse_mods_info(str(self.mods_info_path))
            except Exception:
                return {}
        return {}

    def scan(self, skip_manifest_parse: bool = False) -> List[Mod]:
        """
        扫描所有模组。
        如果 skip_manifest_parse=True 则不解析 .scs 内部（只得到文件名/大小/时间等基础信息），
        用于第一次快速显示列表，后续可逐个懒加载。
        """
        mods_index: Dict[str, Mod] = {}
        mi_index = self.load_mods_info_index()

        # 1) 本地 mod 目录
        if self.local_mod_dir and self.local_mod_dir.exists():
            try:
                for item in self.local_mod_dir.iterdir():
                    try:
                        mod = self._classify_and_build(item, mi_index, skip_manifest_parse)
                    except Exception:
                        continue
                    if mod is not None:
                        mods_index[mod.mod_id] = mod
            except OSError:
                pass

        # 2) Workshop 目录：每个子目录是一个订阅模组
        if self.workshop_dir and self.workshop_dir.exists():
            try:
                for item in self.workshop_dir.iterdir():
                    if not item.is_dir():
                        continue
                    ws_id = item.name
                    # Workshop 包名形如 "1234567890" 纯数字
                    try:
                        if skip_manifest_parse:
                            # 快速路径：不解析子包 manifest.sii（不解包），直接 minimal，后面会 fallback 到 ws_id 做索引
                            mod = _build_mod_minimal(item, "workshop", mi_index)
                        else:
                            # 性能优化：Workshop 目录解析主包即返回，嵌套兜底延后到 async_parse
                            mod = _build_mod_from_package(item, "workshop", mi_index, skip_nested=True)
                    except Exception:
                        continue
                    if mod is not None:
                        # 只在 manifest.package_name 是垃圾值时才用 ws_id 兜底（保留内部真实 unit_name 匹配 profile）
                        _PKG_BLACKLIST = {"", "manifest", "package_name", "mods_info", "nameless", "mod_package"}
                        if (not mod.manifest.package_name
                                or mod.manifest.package_name.startswith(".")
                                or mod.manifest.package_name.strip().lower() in _PKG_BLACKLIST):
                            mod.manifest.package_name = ws_id
                        # 强制 mod_id = ws_id 纯数字目录名（保持 workshop 识别 ID；双索引机制可正常查询）
                        mod.mod_id = ws_id
                    if mod.mod_id not in mods_index:
                        mods_index[mod.mod_id] = mod
                    else:
                        # 本地已有同名 mod：合并 Workshop 路径到已有记录
                        existing = mods_index[mod.mod_id]
                        existing._workshop_path = mod.package_path  # type: ignore[attr-defined]
                        existing._has_workshop_dup = True  # type: ignore[attr-defined]
            except OSError:
                pass

        mods_list = list(mods_index.values())
        # Steam Workshop API 查询移至 main_window 后台线程执行（避免阻塞 UI）
        # 分类兜底：从 category_service 回填每个 mod 的分类（空串 = 未分类）
        if not skip_manifest_parse:
            try:
                from services import category_service as _cs
                name_hints = {m.mod_id: m.display_title for m in mods_list}
                for m in mods_list:
                    cat = _cs.get_category(m.mod_id)
                    if cat:
                        m._category_tag = cat
                # 更新 known_mods.json（用于分类持久化）
                _cs.touch_and_detect_new(
                    [m.mod_id for m in mods_list], name_hints=name_hints
                )
                _cs.save()
            except Exception:
                pass
        # 新模组检测：与上次会话对比（而非 known_mods.json 累积对比）
        try:
            from services.session_service import get_new_mod_ids_vs_last_session
            new_ids = get_new_mod_ids_vs_last_session(
                [m.mod_id for m in mods_list]
            )
        except Exception:
            new_ids = []
        return mods_list, new_ids

    # ----- 内部辅助 -----
    def _classify_and_build(self, item: Path, mi_index: Dict[str, int], skip_parse: bool) -> Optional[Mod]:
        if item.is_dir():
            if skip_parse:
                # 偷懒：直接构造一个空 manifest 的 Mod
                return _build_mod_minimal(item, "directory", mi_index)
            # 性能优化：本地目录也跳过嵌套兜底，延后到 async_parse
            return _build_mod_from_package(item, "directory", mi_index, skip_nested=True)
        # 文件：按后缀
        suf = item.suffix.lower()
        if suf in PACKAGE_SUFFIXES:
            ptype = "scs" if suf == ".scs" else "zip"
            if skip_parse:
                return _build_mod_minimal(item, ptype, mi_index)
            # 单文件不需要嵌套兜底（skip_nested=False 不影响，因为 package_path 不是目录）
            return _build_mod_from_package(item, ptype, mi_index)
        return None


def _build_mod_minimal(package_path: Path, ptype: str, mi_index: Dict[str, int]) -> Mod:
    mod_id = _mod_id_from_package(package_path)
    size = 0
    mtime = 0.0
    try:
        if package_path.is_dir():
            size = _dir_size(package_path)
            mtime = package_path.stat().st_mtime
        else:
            size = package_path.stat().st_size
            mtime = package_path.stat().st_mtime
    except OSError:
        pass
    mod = Mod(
        mod_id=mod_id,
        package_path=str(package_path),
        package_type=ptype,
        file_size=size,
        last_modified=mtime,
        mods_info_timestamp=mi_index.get(mod_id, 0),
    )
    mod.manifest.package_name = mod_id
    return mod


def _enrich_nested_fallback(mod: Mod) -> bool:
    """对标记了 _needs_nested_fallback 的 Mod 异步执行嵌套兜底解析。

    性能：将原 _build_mod_from_package 内的 Workshop 多层子目录兜底查找
    （子 .scs 包 + universal/alt/neu 子目录）从同步扫描阶段延后到 async_parse，
    避免扫描大量 Workshop 目录时同步阻塞。复用已打开的 ScsArchiveReader。

    返回是否有字段被填充。
    """
    if not getattr(mod, "_needs_nested_fallback", False):
        return False
    package_path = Path(mod.package_path)
    if not package_path.is_dir():
        mod._needs_nested_fallback = False  # type: ignore[attr-defined]
        return False

    filled = False

    # 兜底 1：复用共享函数
    if _try_nested_scs(mod, package_path):
        filled = True

    # 兜底 2：复用共享函数
    if not mod.manifest.display_name and not mod.icon.is_available:
        if _try_nested_subdir(mod, package_path):
            filled = True

    mod._needs_nested_fallback = False  # type: ignore[attr-defined]
    return filled
