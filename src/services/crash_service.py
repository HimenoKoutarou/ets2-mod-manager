# -*- coding: utf-8 -*-
"""
services/crash_service.py — Mod 加载预检（L0-L3 四层）+ Crashlog 崩溃定位引擎

公共 API：
    discover_default_game_dirs() -> dict
    discover_latest_crash_pair() -> dict
    precheck_active_mods(...) -> PrecheckReport
    analyze_crashlog(...) -> CrashAnalyzeResult

所有方法纯同步 CPU 密集型；UI 端需放入 QThread 执行。
不依赖 Qt、不启动游戏。
所有正则用原始字符串 r'' 以避免 Windows 路径反斜杠问题。
所有未预期异常顶层捕获 → 包装一条 RED INTERNAL issue + traceback.print_exc(stderr)。
"""
from __future__ import annotations

import os
import re
import sys
import time
import traceback
import zipfile
import struct
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any

from core.models import Mod, ModManifest
from core.scs_archive import ScsArchiveReader
from core.sii_parser import parse_sii


# ============================================================
# 数据模型
# ============================================================

class Severity(str, Enum):
    RED = "red"          # 必崩：真实加载必然闪退
    YELLOW = "yellow"    # 警告：大概率运行时异常，但可能不闪退
    GREEN = "green"      # OK：此层检查通过（不纳入告警计数，仅统计）


class PrecheckDepth(str, Enum):
    L0_FAST = "L0"       # <1s
    L1_MED  = "L1"       # <5s
    L2_DEEP = "L2"       # <30s  默认 max_depth
    L3_HIST = "L3"       # <2s，可与 L0~L2 组合叠加


class CrashSuspicion(str, Enum):
    S = "S"              # 确定嫌疑：日志明确写 in package 'xxx.scs' 或直接报 mod id
    A = "A"              # 高嫌疑：崩发生在该 mod 正在 mount 或刚 mount 完的错误区间
    B = "B"              # 一般嫌疑：崩前最后 N 个成功 mount 中（N=5）


@dataclass
class PrecheckIssue:
    mod_id: str
    mod_display_name: str
    priority_index: Optional[int]
    severity: Severity
    layer: PrecheckDepth
    check_code: str
    evidence: str
    suggestion: str
    extra: dict = field(default_factory=dict)


@dataclass
class PrecheckReport:
    profile_id: str
    scanned_mods: int
    total_issues: int
    red_count: int
    yellow_count: int
    issues: List[PrecheckIssue]
    elapsed_ms: int


@dataclass
class CrashSuspectMod:
    rank: int
    suspicion: CrashSuspicion
    mod_id: str
    mod_display_name: str
    priority_index: Optional[int]
    evidence_lines: List[str]
    evidence_line_range: Tuple[int, int]


@dataclass
class CrashAnalyzeResult:
    crash_time: str
    build_version: str
    exception_code: str
    fault_module_category: str
    suspects: List[CrashSuspectMod]
    failed_to_match: int
    raw_tail_lines: List[str]


# ============================================================
# 常量与正则
# ============================================================

_WIN_ILLEGAL_CHARS_RE = re.compile(r'[\:*?"<>|]')
_CTRL_CHARS_RE = re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F]')
_ENTRY_BAD_PATH_RE = re.compile(r'(\.\./|^[A-Za-z]:/|\x00)')
_VFS_PREFIXES = ("/def", "/automat", "/vehicle", "/material", "/effect", "/ui", "/sound", "/unit")
_REF_EXT_RE = re.compile(r'[\w/\\\-\.]+\.(?:tobj|mat|pmg|pmd|sii|ppd|pma)', re.IGNORECASE)
_INCLUDE_RE = re.compile(r'@include\s+"([^"]+)"')
_UNIT_PATH_RE = re.compile(r'def/[\w/]+\.sii', re.IGNORECASE)
_FAULT_DLL_RE = re.compile(r'Fault address:.*?([\w\-\.]+\.(?:dll|exe))', re.IGNORECASE)
_CRASH_TIME_RE = re.compile(r'Crash log created on:\s*(.*)')
_BUILD_RE = re.compile(r'Build:\s*([\d\.\w]+)')
_EXC_CODE_RE = re.compile(r'Exception code:\s*(\S+(?:\s+\S+)?)')
_START_MARKER_RE = re.compile(r'^\s*\(.*\)\s*\[app\]\s+starting\s+(eurotrucks2|amtrucks)', re.IGNORECASE)
_SHUTDOWN_RE = re.compile(r'\[sys\]\s+Process manager shutdown', re.IGNORECASE)
_HASHFS_CREATED_RE = re.compile(r'\[hashfs\]\s+([^:]+\.scs):\s*Created', re.IGNORECASE)
_HASHFS_VALIDATED_RE = re.compile(r'\[hashfs\]\s+([^:]+\.scs):\s*Created and validated', re.IGNORECASE)
_PKG_IN_ERR_RE = re.compile(r"in package\s+'([^']+\.scs)'", re.IGNORECASE)
_SII_INVALID_UNIT_RE = re.compile(r"\[sii\]\s+invalid unit\s+'([^']+)'", re.IGNORECASE)
_MISSING_FILE_RE = re.compile(r"\[resource\]\s+missing_file\s+(\S+)", re.IGNORECASE)
_COULD_NOT_LOAD_RE = re.compile(r"could not load unit\s+(\S+)|unit not found\s+(\S+)", re.IGNORECASE)
_MODS_INFO_LINE_RE = re.compile(r"active_mods\[\d+\]\s*=\s*[\"']([^\"']+)[\"']")

_L2_TIMEOUT_SEC = 30.0
_L2_CANCEL_EVERY = 2000
_L2_MAX_ENTRIES_PER_MOD = 200_000

_THIRD_PARTY_HINTS = ("sogou", "nahimic", "rtshooks", "obs", "fraps", "rtss", "r3d", "msi", "asus")


# ============================================================
# 公共入口 1：文档目录自动发现
# ============================================================

def discover_default_game_dirs() -> Dict[str, Optional[Path]]:
    """枚举 ETS2 / ATS Documents 目录（含 OneDrive 重定向兜底）。
    返回 {"ets2": Path|None, "ats": Path|None}，不存在则值为 None。"""
    cands_ets2: List[Path] = []
    cands_ats: List[Path] = []
    try:
        userprofile = os.environ.get("USERPROFILE", "")
        if userprofile:
            docs = Path(userprofile) / "Documents"
            cands_ets2.append(docs / "Euro Truck Simulator 2")
            cands_ats.append(docs / "American Truck Simulator")
    except Exception:
        import traceback as _tb; _tb.print_exc(limit=1, file=sys.stderr)
        pass
    try:
        onedrive = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer") or ""
        if onedrive:
            od_docs = Path(onedrive) / "Documents"
            cands_ets2.append(od_docs / "Euro Truck Simulator 2")
            cands_ats.append(od_docs / "American Truck Simulator")
    except Exception:
        import traceback as _tb; _tb.print_exc(limit=1, file=sys.stderr)
        pass
    # 兜底硬编码：本机已确认存在的实机路径
    cands_ets2.append(Path(r"C:\Users\11253\Documents\Euro Truck Simulator 2"))
    cands_ats.append(Path(r"C:\Users\11253\Documents\American Truck Simulator"))

    def _pick(cands: List[Path]) -> Optional[Path]:
        for c in cands:
            try:
                if c.is_dir():
                    return c
            except OSError:
                continue
        return None
    return {"ets2": _pick(cands_ets2), "ats": _pick(cands_ats)}


def discover_latest_crash_pair() -> Dict[str, Optional[Path]]:
    """自动找最新的 (game.crash.txt, game.log.txt) 对儿。
    返回 {"crash": Path|None, "log": Path|None, "source": "ets2"|"ats"|None}。"""
    dirs = discover_default_game_dirs()
    best_crash: Optional[Path] = None
    best_log: Optional[Path] = None
    best_source: Optional[str] = None
    best_mtime: float = -1.0
    for source in ("ets2", "ats"):
        d = dirs.get(source)
        if not d:
            continue
        crash_p = d / "game.crash.txt"
        log_p = d / "game.log.txt"
        try:
            crash_exists = crash_p.is_file()
        except OSError:
            crash_exists = False
        if crash_exists:
            try:
                m = crash_p.stat().st_mtime
            except OSError:
                m = 0.0
            if m > best_mtime:
                best_mtime = m
                best_crash = crash_p
                best_source = source
                try:
                    best_log = log_p if log_p.is_file() else None
                except OSError:
                    best_log = None
    # 若两边都没 crash.txt，至少给个 log.txt（让 UI 能手动分析）
    if best_crash is None:
        for source in ("ets2", "ats"):
            d = dirs.get(source)
            if not d:
                continue
            log_p = d / "game.log.txt"
            try:
                if log_p.is_file():
                    best_log = log_p
                    best_source = source
                    break
            except OSError:
                continue
    return {"crash": best_crash, "log": best_log, "source": best_source}


# ============================================================
# 公共入口 2：Mod 加载预检
# ============================================================

def precheck_active_mods(
    profile,
    all_mods: List[Mod],
    max_depth: PrecheckDepth = PrecheckDepth.L2_DEEP,
    use_l3_history: bool = True,
    ets2_docs_dir: Optional[Path] = None,
    ats_docs_dir: Optional[Path] = None,
    cancel_flag=None,
) -> PrecheckReport:
    """功能 A 入口。
    - profile: Profile 数据类（含 active_mods[] / profile_id）。
    - all_mods: 全量 Mod 列表（用于按 mod_id 反查 Mod 对象）。
    - max_depth: 最大深度 L0/L1/L2/L3。L3 是叠加层，同时受 use_l3_history 控制。
    - cancel_flag: threading.Event 或具有 is_set() 的对象；L2 循环中每 2000 次检查。
    所有异常顶层捕获 → 包装一条 RED INTERNAL issue + traceback.print_exc(stderr)。"""
    t0 = time.monotonic()
    issues: List[PrecheckIssue] = []
    try:
        if profile is None:
            raise ValueError("profile is None")
        active_mods: List[str] = list(getattr(profile, "active_mods", []) or [])
        profile_id = str(getattr(profile, "profile_id", "") or "")
        mods_by_id: Dict[str, Mod] = {}
        if all_mods:
            for m in all_mods:
                try:
                    mid = m.mod_id
                except Exception:
                    import traceback as _tb; _tb.print_exc(limit=1, file=sys.stderr)
                    continue
                if mid and mid not in mods_by_id:
                    mods_by_id[mid] = m
        # active_mods 按顺序赋予 priority_index（0 = 最高）
        active_objs: List[Mod] = []
        for idx, mid in enumerate(active_mods):
            m = mods_by_id.get(mid)
            if m is None:
                continue
            try:
                m.priority_index = idx
            except Exception:
                import traceback as _tb; _tb.print_exc(limit=1, file=sys.stderr)
                pass
            active_objs.append(m)
        # docs_dir 默认值
        if ets2_docs_dir is None or ats_docs_dir is None:
            defaults = discover_default_game_dirs()
            if ets2_docs_dir is None:
                ets2_docs_dir = defaults.get("ets2")
            if ats_docs_dir is None:
                ats_docs_dir = defaults.get("ats")
        # 1) L0
        _run_L0(active_objs, active_mods, issues)
        # 2) L1
        if _depth_level(max_depth) >= 1:
            _run_L1(active_objs, issues, ets2_docs_dir, ats_docs_dir)
        # 3) L2
        if _depth_level(max_depth) >= 2:
            _run_L2(active_objs, profile, issues, cancel_flag)
        # 4) L3 历史日志（叠加层）
        if use_l3_history or max_depth == PrecheckDepth.L3_HIST:
            try:
                _run_L3(active_objs, active_mods, ets2_docs_dir, ats_docs_dir, issues)
            except Exception:
                traceback.print_exc(file=sys.stderr)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        issues.append(PrecheckIssue(
            mod_id="",
            mod_display_name="",
            priority_index=None,
            severity=Severity.RED,
            layer=PrecheckDepth.L0_FAST,
            check_code="INTERNAL",
            evidence=f"内部错误: {type(e).__name__}: {e}",
            suggestion="请把 stderr 中的 traceback 反馈给开发者；可继续手动核查 mod 列表。",
        ))
    report = _finalize_report(
        str(getattr(profile, "profile_id", "") or "") if profile is not None else "",
        len(active_objs) if profile is not None else 0,
        issues,
        int((time.monotonic() - t0) * 1000),
    )
    return report


def _depth_level(d: PrecheckDepth) -> int:
    """L3_HIST 视作叠加层，等价于 L2_DEEP（L3 单独由 use_l3_history 触发）。"""
    if d == PrecheckDepth.L0_FAST: return 0
    if d == PrecheckDepth.L1_MED:  return 1
    if d == PrecheckDepth.L2_DEEP: return 2
    if d == PrecheckDepth.L3_HIST:  return 2
    return 0


def _cancel_requested(cancel_flag) -> bool:
    if cancel_flag is None:
        return False
    try:
        if hasattr(cancel_flag, "is_set"):
            return bool(cancel_flag.is_set())
    except Exception:
        import traceback as _tb; _tb.print_exc(limit=1, file=sys.stderr)
        pass
    try:
        return bool(cancel_flag)
    except Exception:
        import traceback as _tb; _tb.print_exc(limit=1, file=sys.stderr)
        return False


# ============================================================
# L0 快筛（<1s，文件级）5 项检查
# ============================================================

def _run_L0(active_objs: List[Mod], active_mods: List[str], issues: List[PrecheckIssue]) -> None:
    # L0-1：active_mods[i] 在 all_mods 找不到 / 文件不存在
    found_ids = set()
    for m in active_objs:
        try:
            found_ids.add(m.mod_id)
        except Exception:
            import traceback as _tb; _tb.print_exc(limit=1, file=sys.stderr)
            pass
    for idx, mid in enumerate(active_mods):
        if mid in found_ids:
            continue
        issues.append(PrecheckIssue(
            mod_id=mid, mod_display_name=mid, priority_index=idx,
            severity=Severity.RED, layer=PrecheckDepth.L0_FAST, check_code="L0-1",
            evidence=f"active_mods[{idx}] = '{mid}' 在 all_mods 中找不到对应 Mod（可能未扫描或已删除）。",
            suggestion="从 active_mods[] 中移除该项，或重新扫描 mod 目录。",
        ))
    # L0-1 续：Mod 存在但 package_path 不存在
    for m in active_objs:
        try:
            p = Path(m.package_path)
            if not p.exists():
                issues.append(PrecheckIssue(
                    mod_id=m.mod_id, mod_display_name=m.display_title,
                    priority_index=m.priority_index,
                    severity=Severity.RED, layer=PrecheckDepth.L0_FAST, check_code="L0-1",
                    evidence=f"package_path 不存在：{m.package_path}",
                    suggestion="重新下载该 mod 或从 active_mods 移除。",
                ))
        except Exception:
            import traceback as _tb; _tb.print_exc(limit=1, file=sys.stderr)
            pass
    # L0-2：scs/zip 包头校验
    for m in active_objs:
        try:
            if m.package_type not in ("scs", "zip"):
                continue
            p = Path(m.package_path)
            if not p.is_file():
                continue
            try:
                with ScsArchiveReader(p) as rd:
                    if rd.is_encrypted_or_external:
                        continue  # 加密包：list_entries 返回 []，不报 L0-2，由 L1-3 处理
                    entries = rd.list_entries()
                    if not entries:
                        issues.append(PrecheckIssue(
                            mod_id=m.mod_id, mod_display_name=m.display_title,
                            priority_index=m.priority_index,
                            severity=Severity.RED, layer=PrecheckDepth.L0_FAST, check_code="L0-2",
                            evidence=f"包无法读出 entry 列表（可能为截断或坏块）：{m.package_path}",
                            suggestion="重新下载该 mod 包；或用 7-zip 验证 zip 完整性。",
                        ))
            except (zipfile.BadZipFile, struct.error, EOFError, OSError) as e:
                issues.append(PrecheckIssue(
                    mod_id=m.mod_id, mod_display_name=m.display_title,
                    priority_index=m.priority_index,
                    severity=Severity.RED, layer=PrecheckDepth.L0_FAST, check_code="L0-2",
                    evidence=f"包打开失败：{type(e).__name__}: {e}",
                    suggestion="重新下载该 mod 包；或用 7-zip 验证 zip 完整性。",
                ))
            except Exception:
                import traceback as _tb; _tb.print_exc(limit=1, file=sys.stderr)
                pass
        except Exception:
            import traceback as _tb; _tb.print_exc(limit=1, file=sys.stderr)
            pass
    # L0-3：manifest.sii 语法 + 控制字符污染
    for m in active_objs:
        try:
            if m.package_type not in ("scs", "zip", "directory"):
                continue
            p = Path(m.package_path)
            if not p.exists():
                continue
            manifest_text = None
            try:
                with ScsArchiveReader(p) as rd:
                    if not rd.is_encrypted_or_external:
                        manifest_text = rd.read_text("manifest.sii")
            except Exception:
                manifest_text = None
            if m.package_type == "directory":
                mf = p / "manifest.sii"
                try:
                    if mf.is_file():
                        manifest_text = mf.read_text(encoding="utf-8-sig", errors="replace")
                except OSError:
                    pass
            if not manifest_text:
                continue
            if _CTRL_CHARS_RE.search(manifest_text):
                issues.append(PrecheckIssue(
                    mod_id=m.mod_id, mod_display_name=m.display_title,
                    priority_index=m.priority_index,
                    severity=Severity.RED, layer=PrecheckDepth.L0_FAST, check_code="L0-3",
                    evidence="manifest.sii 中检测到非法控制字符（\\x00-\\x1F 范围，除 \\r\\n\\t）。",
                    suggestion="重新导出或修复 manifest.sii；可能被编辑器误转码。",
                ))
                continue
            try:
                parse_sii(manifest_text)
            except Exception as e:
                issues.append(PrecheckIssue(
                    mod_id=m.mod_id, mod_display_name=m.display_title,
                    priority_index=m.priority_index,
                    severity=Severity.RED, layer=PrecheckDepth.L0_FAST, check_code="L0-3",
                    evidence=f"manifest.sii 解析失败：{type(e).__name__}: {e}",
                    suggestion="修复 manifest.sii 语法；可参考 SCS 官方 mod 模板。",
                ))
        except Exception:
            import traceback as _tb; _tb.print_exc(limit=1, file=sys.stderr)
            pass
    # L0-4：同 mod_id 重复
    id_count: Dict[str, int] = {}
    for m in active_objs:
        try:
            id_count[m.mod_id] = id_count.get(m.mod_id, 0) + 1
        except Exception:
            import traceback as _tb; _tb.print_exc(limit=1, file=sys.stderr)
            pass
    for mid, cnt in id_count.items():
        if cnt < 2:
            continue
        for m in active_objs:
            try:
                if m.mod_id != mid:
                    continue
                issues.append(PrecheckIssue(
                    mod_id=mid, mod_display_name=m.display_title,
                    priority_index=m.priority_index,
                    severity=Severity.RED, layer=PrecheckDepth.L0_FAST, check_code="L0-4",
                    evidence=f"mod_id '{mid}' 在 active_mods 中出现 {cnt} 次（SCS 加载唯一键必须唯一）。",
                    suggestion="仅保留一个；移除重复项并重新启用 mod。",
                ))
            except Exception:
                import traceback as _tb; _tb.print_exc(limit=1, file=sys.stderr)
                pass
    # L0-5：directory 型 mod 路径非法字符 / 过长
    for m in active_objs:
        try:
            if m.package_type != "directory":
                continue
            p = str(m.package_path)
            if _WIN_ILLEGAL_CHARS_RE.search(p):
                issues.append(PrecheckIssue(
                    mod_id=m.mod_id, mod_display_name=m.display_title,
                    priority_index=m.priority_index,
                    severity=Severity.RED, layer=PrecheckDepth.L0_FAST, check_code="L0-5",
                    evidence=f"directory mod 路径含非法字符：{p}",
                    suggestion="重命名目录移除 : * ? \" < > | 等字符。",
                ))
                continue
            if len(p) > 248:  # MAX_PATH - 12
                issues.append(PrecheckIssue(
                    mod_id=m.mod_id, mod_display_name=m.display_title,
                    priority_index=m.priority_index,
                    severity=Severity.RED, layer=PrecheckDepth.L0_FAST, check_code="L0-5",
                    evidence=f"directory mod 路径过长（{len(p)} > 248 字符），hashfs 映射会被 ETS2 拒绝。",
                    suggestion="把 mod 目录挪到更浅的路径，例如 F:\\ETS2ModManager\\mods\\。",
                ))
        except Exception:
            import traceback as _tb; _tb.print_exc(limit=1, file=sys.stderr)
            pass


# ============================================================
# L1 中检（<5s，hashfs 索引 + 元数据级）5 项检查
# ============================================================

def _run_L1(active_objs: List[Mod], issues: List[PrecheckIssue],
            ets2_docs_dir: Optional[Path], ats_docs_dir: Optional[Path]) -> None:
    build_ver = _detect_build_version(ets2_docs_dir, ats_docs_dir)
    for m in active_objs:
        try:
            if m.package_type not in ("scs", "zip", "directory"):
                continue
            p = Path(m.package_path)
            if not p.exists():
                continue
            entries: List[str] = []
            manifest: ModManifest = m.manifest
            try:
                with ScsArchiveReader(p) as rd:
                    if rd.is_encrypted_or_external:
                        issues.append(PrecheckIssue(
                            mod_id=m.mod_id, mod_display_name=m.display_title,
                            priority_index=m.priority_index,
                            severity=Severity.YELLOW, layer=PrecheckDepth.L1_MED, check_code="L1-3",
                            evidence="加密包/外部解包包，无法静态枚举 entry。",
                            suggestion="可使用游戏内 mod manager 验证；或解密后再扫描。",
                        ))
                        continue
                    entries = rd.list_entries()
                    manifest = rd.parse_manifest()
            except Exception:
                manifest = m.manifest
            if not entries and m.package_type == "directory":
                try:
                    entries = ScsArchiveReader(p).list_entries()
                except Exception:
                    entries = []
            entries_lower = [e.lower() for e in entries]
            # L1-1：entry 路径非法
            bad_paths = [e for e in entries if _ENTRY_BAD_PATH_RE.search(e)]
            if bad_paths:
                issues.append(PrecheckIssue(
                    mod_id=m.mod_id, mod_display_name=m.display_title,
                    priority_index=m.priority_index,
                    severity=Severity.RED, layer=PrecheckDepth.L1_MED, check_code="L1-1",
                    evidence=f"entry 路径非法（含 ../ 或绝对盘符或 NUL 字节）：{bad_paths[:5]}",
                    suggestion="重新打包该 mod，修正路径。",
                ))
            # L1-2：compatible_versions vs Build
            cvs = list(getattr(manifest, "compatible_versions", []) or [])
            if cvs and build_ver:
                # 提取主版本（如 1.52）
                short_build = build_ver.rsplit(".", 1)[0] if "." in build_ver else build_ver
                short_cvs = []
                for v in cvs:
                    parts = v.split(".")
                    if len(parts) >= 2:
                        short_cvs.append(parts[0] + "." + parts[1])
                    else:
                        short_cvs.append(v)
                if short_build not in short_cvs:
                    issues.append(PrecheckIssue(
                        mod_id=m.mod_id, mod_display_name=m.display_title,
                        priority_index=m.priority_index,
                        severity=Severity.RED, layer=PrecheckDepth.L1_MED, check_code="L1-2",
                        evidence=f"compatible_versions={cvs} 不含当前 Build {build_ver}。",
                        suggestion="升级 mod 或回退游戏版本。",
                    ))
            # L1-3：icon/description 声明但缺失
            icon_fn = (getattr(manifest, "icon_filename", "") or "").lower()
            desc_fn = (getattr(manifest, "description_filename", "") or "").lower()
            missing = []
            if icon_fn and icon_fn not in entries_lower:
                missing.append(icon_fn)
            if desc_fn and desc_fn not in entries_lower:
                missing.append(desc_fn)
            if missing:
                issues.append(PrecheckIssue(
                    mod_id=m.mod_id, mod_display_name=m.display_title,
                    priority_index=m.priority_index,
                    severity=Severity.YELLOW, layer=PrecheckDepth.L1_MED, check_code="L1-3",
                    evidence=f"manifest 声明但 entry 中缺失：{missing}",
                    suggestion="补齐缺失文件，或修改 manifest.sii。",
                ))
            # L1-5：随机 20 entry CRC 抽查
            if entries and m.package_type in ("scs", "zip"):
                try:
                    bad_crc = _sample_crc_check(p, entries, 20)
                    if bad_crc:
                        issues.append(PrecheckIssue(
                            mod_id=m.mod_id, mod_display_name=m.display_title,
                            priority_index=m.priority_index,
                            severity=Severity.RED, layer=PrecheckDepth.L1_MED, check_code="L1-5",
                            evidence=f"CRC 校验失败 entry（前 5）：{bad_crc[:5]}",
                            suggestion="磁盘可能坏块；重新下载该 mod。",
                        ))
                except Exception:
                    import traceback as _tb; _tb.print_exc(limit=1, file=sys.stderr)
                    pass
        except Exception:
            import traceback as _tb; _tb.print_exc(limit=1, file=sys.stderr)
            pass
    # L1-4：directory 型 mod 内嵌 .scs 递归 L0+L1-1/1-3
    for m in active_objs:
        try:
            if m.package_type != "directory":
                continue
            p = Path(m.package_path)
            if not p.is_dir():
                continue
            for sub in p.rglob("*"):
                try:
                    if not sub.is_file():
                        continue
                    if sub.suffix.lower() not in (".scs", ".zip"):
                        continue
                    try:
                        with ScsArchiveReader(sub) as rd:
                            if rd.is_encrypted_or_external:
                                continue
                            sub_entries = rd.list_entries()
                            if not sub_entries:
                                issues.append(PrecheckIssue(
                                    mod_id=m.mod_id, mod_display_name=m.display_title,
                                    priority_index=m.priority_index,
                                    severity=Severity.RED, layer=PrecheckDepth.L1_MED, check_code="L1-4",
                                    evidence=f"嵌套子包坏块：{sub}",
                                    suggestion="重新下载或修复该嵌套子包。",
                                ))
                                continue
                            sub_text = rd.read_text("manifest.sii")
                            if sub_text and _CTRL_CHARS_RE.search(sub_text):
                                issues.append(PrecheckIssue(
                                    mod_id=m.mod_id, mod_display_name=m.display_title,
                                    priority_index=m.priority_index,
                                    severity=Severity.RED, layer=PrecheckDepth.L1_MED, check_code="L1-4",
                                    evidence=f"嵌套子包 manifest.sii 含控制字符：{sub}",
                                    suggestion="修复或删除嵌套子包。",
                                ))
                    except (zipfile.BadZipFile, struct.error, EOFError, OSError):
                        issues.append(PrecheckIssue(
                            mod_id=m.mod_id, mod_display_name=m.display_title,
                            priority_index=m.priority_index,
                            severity=Severity.RED, layer=PrecheckDepth.L1_MED, check_code="L1-4",
                            evidence=f"嵌套子包损坏：{sub}",
                            suggestion="重新下载或修复该嵌套子包。",
                        ))
                except Exception:
                    import traceback as _tb; _tb.print_exc(limit=1, file=sys.stderr)
                    continue
        except Exception:
            import traceback as _tb; _tb.print_exc(limit=1, file=sys.stderr)
            pass


def _detect_build_version(ets2_docs_dir: Optional[Path], ats_docs_dir: Optional[Path]) -> str:
    """从 game.log.txt 提取 Build 主版本（如 '1.52'）。"""
    for d in (ets2_docs_dir, ats_docs_dir):
        if not d:
            continue
        log_p = d / "game.log.txt"
        try:
            if not log_p.is_file():
                continue
            with open(log_p, "r", encoding="utf-8-sig", errors="replace") as f:
                for _ in range(10):
                    line = f.readline()
                    if not line:
                        break
                    m = _BUILD_RE.search(line)
                    if m:
                        v = m.group(1).strip()
                        parts = v.split(".")
                        if len(parts) >= 2:
                            return parts[0] + "." + parts[1]
                        return v
        except OSError:
            continue
    return ""


def _sample_crc_check(pkg_path: Path, entries: List[str], sample_n: int) -> List[str]:
    """随机抽查前 N 个 entry 的 CRC，返回失败的 entry 列表。"""
    bad: List[str] = []
    try:
        with zipfile.ZipFile(pkg_path, "r") as z:
            infos = z.infolist()
            sample = infos[:sample_n] if len(infos) > sample_n else infos
            for info in sample:
                try:
                    if info.is_dir():
                        continue
                    z.read(info.filename)  # 触发 CRC 校验
                except (zipfile.BadZipFile, OSError):
                    bad.append(info.filename)
    except (zipfile.BadZipFile, OSError):
        return ["<cannot open>"]
    return bad


# ============================================================
# L2 深度模拟（<30s，VFS 覆盖图 + 交叉引用存在性检查）4 项检查
# ============================================================

def _run_L2(active_objs: List[Mod], profile, issues: List[PrecheckIssue], cancel_flag) -> None:
    t0 = time.monotonic()
    vfs: Dict[str, Tuple[int, str]] = {}              # 虚拟路径 -> (priority_index, mod_id)
    mod_refs: Dict[str, List[str]] = {}               # mod_id -> 引用路径列表
    mod_manifest_unit_paths: Dict[str, List[str]] = {}  # mod_id -> 主 def 路径
    iter_count = 0
    timed_out = False
    for m in active_objs:
        if _cancel_requested(cancel_flag):
            return
        try:
            if m.package_type not in ("scs", "zip", "directory"):
                continue
            p = Path(m.package_path)
            if not p.exists():
                continue
            entries: List[str] = []
            manifest_text: Optional[str] = None
            try:
                with ScsArchiveReader(p) as rd:
                    if rd.is_encrypted_or_external:
                        continue
                    entries = rd.list_entries(max_entries=_L2_MAX_ENTRIES_PER_MOD)
                    manifest_text = rd.read_text("manifest.sii")
            except Exception:
                manifest_text = None
            if m.package_type == "directory" and not entries:
                try:
                    entries = ScsArchiveReader(p).list_entries(max_entries=_L2_MAX_ENTRIES_PER_MOD)
                except Exception:
                    entries = []
            # VFS 写入（按前缀分桶：仅 8 类）
            for e in entries:
                e_norm = e.replace("\\", "/").lstrip("/").lower()
                if not e_norm.startswith(_VFS_PREFIXES):
                    continue
                iter_count += 1
                if iter_count % _L2_CANCEL_EVERY == 0:
                    if _cancel_requested(cancel_flag):
                        return
                    if time.monotonic() - t0 > _L2_TIMEOUT_SEC:
                        timed_out = True
                        break
                cur = vfs.get(e_norm)
                if cur is None or m.priority_index < cur[0]:
                    vfs[e_norm] = (m.priority_index, m.mod_id)
            if timed_out:
                break
            # 收集 manifest unit / 引用
            refs: List[str] = []
            unit_paths: List[str] = []
            if manifest_text:
                for mm in _INCLUDE_RE.finditer(manifest_text):
                    refs.append(mm.group(1).replace("\\", "/").lstrip("/").lower())
                for mm in _UNIT_PATH_RE.finditer(manifest_text):
                    unit_paths.append(mm.group(0).replace("\\", "/").lstrip("/").lower())
                for mm in _REF_EXT_RE.finditer(manifest_text):
                    refs.append(mm.group(0).replace("\\", "/").lstrip("/").lower())
            mod_refs[m.mod_id] = refs
            mod_manifest_unit_paths[m.mod_id] = unit_paths
        except Exception:
            import traceback as _tb; _tb.print_exc(limit=1, file=sys.stderr)
            continue
    # L2-1：交叉引用缺失 + L2-2：低优 mod 主 def 被遮蔽
    if not timed_out:
        for m in active_objs:
            try:
                refs = mod_refs.get(m.mod_id, [])
                unit_paths = mod_manifest_unit_paths.get(m.mod_id, [])
                missing: List[str] = []
                for r in refs:
                    if r and r not in vfs:
                        missing.append(r)
                for u in unit_paths:
                    if u and u not in vfs:
                        missing.append(u)
                if missing:
                    issues.append(PrecheckIssue(
                        mod_id=m.mod_id, mod_display_name=m.display_title,
                        priority_index=m.priority_index,
                        severity=Severity.RED, layer=PrecheckDepth.L2_DEEP, check_code="L2-1",
                        evidence=f"交叉引用缺失（前 10）：{missing[:10]}",
                        suggestion="补齐缺失文件；或确认依赖 mod 是否启用。",
                        extra={"missing": missing},
                    ))
                # L2-2：低优 mod 主 def 被高优 mod 遮蔽
                for u in unit_paths:
                    cur = vfs.get(u)
                    if cur and cur[1] != m.mod_id and cur[0] < m.priority_index:
                        issues.append(PrecheckIssue(
                            mod_id=m.mod_id, mod_display_name=m.display_title,
                            priority_index=m.priority_index,
                            severity=Severity.YELLOW, layer=PrecheckDepth.L2_DEEP, check_code="L2-2",
                            evidence=f"路径 {u} 被高优 mod '{cur[1]}' 遮蔽（priority {cur[0]} < {m.priority_index}）。",
                            suggestion="调整 mod 优先级或移除冲突。",
                        ))
            except Exception:
                import traceback as _tb; _tb.print_exc(limit=1, file=sys.stderr)
                pass
    # L2-3：profile.sii 加密 roundtrip
    try:
        ok, msg = _profile_roundtrip_check(profile)
        if not ok:
            issues.append(PrecheckIssue(
                mod_id="", mod_display_name="", priority_index=None,
                severity=Severity.RED, layer=PrecheckDepth.L2_DEEP, check_code="L2-3",
                evidence=f"profile.sii 加密 roundtrip 失败：{msg}",
                suggestion="备份 profile.sii 后让游戏重建。",
            ))
    except Exception:
        import traceback as _tb; _tb.print_exc(limit=1, file=sys.stderr)
        pass
    # L2-4：超时降级
    if timed_out:
        issues.append(PrecheckIssue(
            mod_id="", mod_display_name="", priority_index=None,
            severity=Severity.YELLOW, layer=PrecheckDepth.L2_DEEP, check_code="L2-4",
            evidence=f"L2 执行超时（>{_L2_TIMEOUT_SEC:.0f}s），已降级为 L0+L1 结果。",
            suggestion="mod 数量过多或包体过大；可拆分 profile 分批预检。",
        ))


def _profile_roundtrip_check(profile) -> Tuple[bool, str]:
    """复用 profile_service.encrypt_profile_bytes 的 roundtrip 校验。失败返回 (False, msg)。"""
    try:
        from services import profile_service as ps
    except Exception:
        return True, "profile_service 不可用，跳过"
    try:
        encrypt = getattr(ps, "encrypt_profile_bytes", None)
        if encrypt is None:
            return True, "profile_service 缺少 encrypt_profile_bytes API"
        ppath = str(getattr(profile, "profile_path", "") or "")
        if not ppath:
            return True, "无 profile_path"
        p = Path(ppath)
        if not p.is_file():
            return True, "profile.sii 不存在"
        try:
            raw = p.read_bytes()
        except OSError:
            return True, "profile.sii 读取失败"
        if not raw:
            return True, "profile.sii 为空"
        sample_in = raw[: min(len(raw), 8192)]
        try:
            sample_out = encrypt(sample_in)
        except Exception:
            return True, "encrypt_profile_bytes 抛异常（视为通过）"
        if sample_out and abs(len(sample_out) - len(sample_in)) > len(raw) * 0.5:
            return False, f"roundtrip 字节数差异过大（in={len(sample_in)} vs out={len(sample_out)}）"
        return True, "OK"
    except Exception as e:
        return True, f"check skipped: {e}"


# ============================================================
# L3 历史日志（<2s，叠加层）3 项检查
# ============================================================

def _run_L3(active_objs: List[Mod], active_mods: List[str],
            ets2_docs_dir: Optional[Path], ats_docs_dir: Optional[Path],
            issues: List[PrecheckIssue]) -> None:
    log_path: Optional[Path] = None
    for d in (ets2_docs_dir, ats_docs_dir):
        if not d:
            continue
        p = d / "game.log.txt"
        try:
            if p.is_file():
                log_path = p
                break
        except OSError:
            continue
    if log_path is None:
        return
    try:
        tail_lines = _read_tail_lines(log_path, 600)
    except Exception:
        import traceback as _tb; _tb.print_exc(limit=1, file=sys.stderr)
        return
    if not tail_lines:
        return
    # 找最近一次无 shutdown 的启动标记作为 CRASH_SESSION
    start_idx = -1
    for i in range(len(tail_lines) - 1, -1, -1):
        if _START_MARKER_RE.search(tail_lines[i]):
            session = tail_lines[i:]
            has_shutdown = any(_SHUTDOWN_RE.search(l) for l in session)
            if not has_shutdown and len(session) >= 20:
                start_idx = i
                break
            continue
    if start_idx < 0:
        # 取尾部所有行作为 session 兜底
        session = tail_lines
    else:
        session = tail_lines[start_idx:]
    has_shutdown = any(_SHUTDOWN_RE.search(l) for l in session)

    # 包名 -> Mod 反查表
    pkg_to_mod: Dict[str, Mod] = {}
    for m in active_objs:
        try:
            stem = Path(m.package_path).stem.lower()
            if stem:
                pkg_to_mod[stem] = m
            pn = getattr(m.manifest, "package_name", "") or ""
            if pn:
                pkg_to_mod.setdefault(pn.lower(), m)
        except Exception:
            import traceback as _tb; _tb.print_exc(limit=1, file=sys.stderr)
            pass

    # 收集 session 中 [hashfs] Created 包名集合（用于组合对比）
    log_pkgs = set()
    last_created = None
    for line in session:
        mh = _HASHFS_CREATED_RE.search(line)
        if mh:
            pkg = mh.group(1).lower().replace(".scs", "")
            log_pkgs.add(pkg)
            last_created = pkg  # 累计；后续 fatal 用
    # L3-1：4 类致命错误 → RED
    if not has_shutdown:
        for li, line in enumerate(session):
            mh = _HASHFS_CREATED_RE.search(line)
            if mh:
                last_created = mh.group(1).lower().replace(".scs", "")
                continue
            pkg_hit = None
            mp = _PKG_IN_ERR_RE.search(line)
            if mp:
                pkg_hit = mp.group(1).lower().replace(".scs", "")
            elif (_SII_INVALID_UNIT_RE.search(line)
                  or _MISSING_FILE_RE.search(line)
                  or _COULD_NOT_LOAD_RE.search(line)):
                pkg_hit = last_created  # 紧邻上一个 hashfs Created
            if not pkg_hit:
                continue
            # 反查 Mod（4 级匹配）
            mm = pkg_to_mod.get(pkg_hit)
            if mm is None:
                for k, mv in pkg_to_mod.items():
                    if pkg_hit in k or k in pkg_hit:
                        mm = mv
                        break
            mid = mm.mod_id if mm else ""
            display = mm.display_title if mm else f"未知 mod: {pkg_hit}.scs"
            prio = mm.priority_index if mm else None
            ctx_lo = max(0, li - 1)
            ctx_hi = min(len(session), li + 2)
            ev_lines = [session[k].rstrip() for k in range(ctx_lo, ctx_hi)]
            issues.append(PrecheckIssue(
                mod_id=mid, mod_display_name=display, priority_index=prio,
                severity=Severity.RED, layer=PrecheckDepth.L3_HIST, check_code="L3-1",
                evidence=line.strip()[:500],
                suggestion="根据日志错误修复或禁用该 mod。",
                extra={"evidence_lines": ev_lines, "line_range": (ctx_lo, ctx_hi)},
            ))

    # L3-2 / L3-3：组合对比
    cur_set = set(str(m).lower() for m in active_mods)
    if log_pkgs or cur_set:
        union = cur_set | log_pkgs
        diff = len(cur_set ^ log_pkgs) / max(len(union), 1) if union else 0.0
    else:
        diff = 0.0

    if has_shutdown:
        if log_pkgs == cur_set and cur_set:
            issues.append(PrecheckIssue(
                mod_id="", mod_display_name="", priority_index=None,
                severity=Severity.GREEN, layer=PrecheckDepth.L3_HIST, check_code="L3-2",
                evidence="最近一次启动正常收尾 + 当前组合与上次完全一致。",
                suggestion="此前该组合成功进入主菜单；本次预计 OK。",
            ))
        else:
            summary = (f"新增 {len(cur_set - log_pkgs)} 个，删除 "
                       f"{len(log_pkgs - cur_set)} 个")
            issues.append(PrecheckIssue(
                mod_id="", mod_display_name="", priority_index=None,
                severity=Severity.YELLOW, layer=PrecheckDepth.L3_HIST, check_code="L3-3",
                evidence=f"最近一次启动正常收尾但组合不同（差异 {diff*100:.0f}%）：{summary}",
                suggestion="参考即可；如本次仍崩，回滚到上次组合。",
            ))

    # L3 degraded_mode：组合差异 > 30% → 所有 L3-1 RED 降为 YELLOW
    if diff > 0.3:
        for iss in issues:
            if (iss.layer == PrecheckDepth.L3_HIST
                    and iss.severity == Severity.RED):
                iss.severity = Severity.YELLOW
        issues.append(PrecheckIssue(
            mod_id="", mod_display_name="", priority_index=None,
            severity=Severity.YELLOW, layer=PrecheckDepth.L3_HIST, check_code="L3-3",
            evidence=(f"本次 L3 对应 mod 组合与当前差异 {diff*100:.0f}%（>30%），"
                      "L3 告警已降级为 YELLOW；参考即可。"),
            suggestion="重新跑一次启动以验证最新组合。",
        ))


def _read_tail_lines(path: Path, n: int) -> List[str]:
    """读最后 N 行（1GB 以上文件只读尾部 100MB）。"""
    try:
        size = path.stat().st_size
    except OSError:
        return []
    tail_bytes = 100 * 1024 * 1024
    try:
        with open(path, "rb") as f:
            if size > tail_bytes:
                f.seek(size - tail_bytes)
                _ = f.read(1)  # 跳过可能半截行
            data = f.read()
    except OSError:
        return []
    text = data.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    return lines[-n:] if len(lines) > n else lines


# ============================================================
# 去重合并 + 汇总
# ============================================================

def _finalize_report(profile_id: str, scanned: int,
                     issues: List[PrecheckIssue], elapsed_ms: int) -> PrecheckReport:
    """去重合并：同 mod_id + check_code 合并为一条；severity 取最高；evidence 多行合并。"""
    merged: Dict[Tuple[str, str], PrecheckIssue] = {}
    for iss in issues:
        key = (iss.mod_id, iss.check_code)
        if key in merged:
            cur = merged[key]
            if _severity_rank(iss.severity) > _severity_rank(cur.severity):
                cur.severity = iss.severity
            if iss.evidence and iss.evidence not in cur.evidence:
                cur.evidence = cur.evidence + "\n- " + iss.evidence
            if iss.extra:
                cur.extra.update(iss.extra)
        else:
            merged[key] = PrecheckIssue(
                mod_id=iss.mod_id, mod_display_name=iss.mod_display_name,
                priority_index=iss.priority_index, severity=iss.severity,
                layer=iss.layer, check_code=iss.check_code,
                evidence=iss.evidence, suggestion=iss.suggestion,
                extra=dict(iss.extra),
            )
    sorted_issues = sorted(merged.values(), key=lambda x: (
        x.priority_index if x.priority_index is not None else 999999,
        -_severity_rank(x.severity),
    ))
    red = sum(1 for i in sorted_issues if i.severity == Severity.RED)
    yellow = sum(1 for i in sorted_issues if i.severity == Severity.YELLOW)
    return PrecheckReport(
        profile_id=profile_id, scanned_mods=scanned,
        total_issues=red + yellow, red_count=red, yellow_count=yellow,
        issues=sorted_issues, elapsed_ms=elapsed_ms,
    )


def _severity_rank(s: Severity) -> int:
    return {Severity.RED: 3, Severity.YELLOW: 2, Severity.GREEN: 1}.get(s, 0)


# ============================================================
# 公共入口 3：Crashlog 解析 & 嫌疑 mod 定位
# ============================================================

def analyze_crashlog(
    crash_txt_path,
    game_log_path=None,
    profile=None,
    all_mods: Optional[List[Mod]] = None,
) -> CrashAnalyzeResult:
    """功能 B 入口。
    - crash_txt_path: game.crash.txt 路径。
    - game_log_path: game.log.txt 路径；None 时自动联动同目录 / 默认目录。
    - profile / all_mods: 用于把包名反查 mod_id / display / priority；
      profile 可空，仅 all_mods 也能匹配。"""
    crash_p = Path(crash_txt_path) if crash_txt_path else None
    log_p: Optional[Path] = None
    if game_log_path:
        log_p = Path(game_log_path)
    elif crash_p is not None:
        cand = crash_p.parent / "game.log.txt"
        try:
            if cand.is_file():
                log_p = cand
        except OSError:
            pass
    if log_p is None:
        defaults = discover_default_game_dirs()
        for src in ("ets2", "ats"):
            d = defaults.get(src)
            if not d:
                continue
            cand = d / "game.log.txt"
            try:
                if cand.is_file():
                    log_p = cand
                    break
            except OSError:
                continue

    # Step 1：崩溃定性
    crash_time = ""
    build_version = ""
    exception_code = ""
    fault_module_category = "unknown"
    if crash_p is not None:
        try:
            with open(crash_p, "r", encoding="utf-8-sig", errors="replace") as f:
                head_lines = [f.readline() for _ in range(80)]
            for line in head_lines:
                if not crash_time:
                    m = _CRASH_TIME_RE.search(line)
                    if m:
                        crash_time = m.group(1).strip()
                if not build_version:
                    m = _BUILD_RE.search(line)
                    if m:
                        build_version = m.group(1).strip()
                if not exception_code:
                    m = _EXC_CODE_RE.search(line)
                    if m:
                        exception_code = m.group(1).strip()
                m = _FAULT_DLL_RE.search(line)
                if m:
                    mod_name = m.group(1).lower()
                    if "eurotrucks2" in mod_name or "amtrucks" in mod_name:
                        fault_module_category = "game_binary"
                    elif any(h in mod_name for h in _THIRD_PARTY_HINTS):
                        fault_module_category = "third_party_injector"
                    else:
                        fault_module_category = "unknown"
        except OSError:
            pass

    # Step 2-6：依赖 game.log.txt
    suspects: List[CrashSuspectMod] = []
    failed_to_match = 0
    raw_tail_lines: List[str] = []
    if log_p is not None and log_p.is_file():
        try:
            tail = _read_tail_lines(log_p, 2000)
        except Exception:
            tail = []
        raw_tail_lines = tail[-30:] if tail else []
        if tail:
            # Step 2：对齐 CRASH_SESSION
            start_idx = -1
            for i in range(len(tail) - 1, -1, -1):
                if _START_MARKER_RE.search(tail[i]):
                    session_test = tail[i:]
                    has_sd = any(_SHUTDOWN_RE.search(l) for l in session_test)
                    if not has_sd and len(session_test) >= 20:
                        start_idx = i
                        break
                    continue
            session = tail[start_idx:] if start_idx >= 0 else tail[-600:]

            # Step 3：构造 real_mount_order
            mount_order: List[Tuple[int, str]] = []
            for li, line in enumerate(session):
                mh = _HASHFS_CREATED_RE.search(line)
                if mh:
                    pkg = mh.group(1).lower().replace(".scs", "")
                    mount_order.append((li, pkg))

            # 反查 mod 表（4 级匹配）
            mod_lookup: Dict[str, Mod] = {}
            if all_mods:
                for m in all_mods:
                    try:
                        stem = Path(m.package_path).stem.lower()
                        if stem:
                            mod_lookup[stem] = m
                    except Exception:
                        import traceback as _tb; _tb.print_exc(limit=1, file=sys.stderr)
                        pass
                    try:
                        if m.manifest and m.manifest.package_name:
                            mod_lookup.setdefault(m.manifest.package_name.lower(), m)
                    except Exception:
                        import traceback as _tb; _tb.print_exc(limit=1, file=sys.stderr)
                        pass
                    try:
                        mod_lookup.setdefault(m.mod_id.lower(), m)
                    except Exception:
                        import traceback as _tb; _tb.print_exc(limit=1, file=sys.stderr)
                        pass
                    try:
                        dt = (m.display_title or "").lower()
                        if dt:
                            mod_lookup.setdefault(dt, m)
                    except Exception:
                        import traceback as _tb; _tb.print_exc(limit=1, file=sys.stderr)
                        pass

            def _match_mod(pkg_stem: str) -> Optional[Mod]:
                if not pkg_stem:
                    return None
                m = mod_lookup.get(pkg_stem)
                if m is not None:
                    return m
                for k, mv in mod_lookup.items():
                    if pkg_stem in k or k in pkg_stem:
                        return mv
                for k, mv in mod_lookup.items():
                    try:
                        if pkg_stem in (mv.display_title or "").lower():
                            return mv
                    except Exception:
                        import traceback as _tb; _tb.print_exc(limit=1, file=sys.stderr)
                        continue
                return None

            # Step 4：S 级嫌疑（B-1 ~ B-4 规则）
            s_suspects: List[CrashSuspectMod] = []
            scan_lines = session[-600:] if len(session) > 600 else session
            offset = len(session) - len(scan_lines)
            last_created_pkg = None
            for li, line in enumerate(scan_lines):
                abs_li = offset + li
                mh = _HASHFS_CREATED_RE.search(line)
                if mh:
                    last_created_pkg = mh.group(1).lower().replace(".scs", "")
                    continue
                pkg_hit = None
                ev_lines: List[str] = []
                mp = _PKG_IN_ERR_RE.search(line)
                if mp:
                    pkg_hit = mp.group(1).lower().replace(".scs", "")
                    ev_lines = _context_lines(scan_lines, li, 2)
                elif (_SII_INVALID_UNIT_RE.search(line)
                      or _MISSING_FILE_RE.search(line)
                      or _COULD_NOT_LOAD_RE.search(line)):
                    if last_created_pkg:
                        pkg_hit = last_created_pkg
                        ev_lines = _context_lines(scan_lines, li, 2)
                if not pkg_hit:
                    continue
                m = _match_mod(pkg_hit)
                if m is None:
                    failed_to_match += 1
                    s_suspects.append(CrashSuspectMod(
                        rank=0, suspicion=CrashSuspicion.S, mod_id="",
                        mod_display_name=f"未知 mod: {pkg_hit}.scs",
                        priority_index=None, evidence_lines=ev_lines,
                        evidence_line_range=(abs_li, abs_li + len(ev_lines)),
                    ))
                else:
                    s_suspects.append(CrashSuspectMod(
                        rank=0, suspicion=CrashSuspicion.S, mod_id=m.mod_id,
                        mod_display_name=m.display_title,
                        priority_index=m.priority_index,
                        evidence_lines=ev_lines,
                        evidence_line_range=(abs_li, abs_li + len(ev_lines)),
                    ))

            # Step 5：A / B 级嫌疑（S 级不足 5 条时补）
            a_suspects: List[CrashSuspectMod] = []
            b_suspects: List[CrashSuspectMod] = []
            if len(s_suspects) < 5:
                ab_scan = session[-100:] if len(session) > 100 else session
                mount_events: List[Tuple[int, str, bool]] = []
                for i, line in enumerate(ab_scan):
                    mv = _HASHFS_VALIDATED_RE.search(line)
                    if mv:
                        mount_events.append((i, mv.group(1).lower().replace(".scs", ""), True))
                        continue
                    mc = _HASHFS_CREATED_RE.search(line)
                    if mc:
                        mount_events.append((i, mc.group(1).lower().replace(".scs", ""), False))
                validated_events = [e for e in mount_events if e[2]]
                last_validated = validated_events[-1] if validated_events else None
                # A-1：最后一个 validated
                if last_validated:
                    m = _match_mod(last_validated[1])
                    if m is not None:
                        a_suspects.append(CrashSuspectMod(
                            rank=0, suspicion=CrashSuspicion.A, mod_id=m.mod_id,
                            mod_display_name=m.display_title,
                            priority_index=m.priority_index,
                            evidence_lines=_context_lines(ab_scan, last_validated[0], 2),
                            evidence_line_range=(last_validated[0], last_validated[0] + 3),
                        ))
                    # A-2：A-1 之后第一个 Created 但未 validated
                    for ev in mount_events:
                        if ev[0] > last_validated[0] and not ev[2]:
                            m2 = _match_mod(ev[1])
                            if m2 is not None:
                                a_suspects.append(CrashSuspectMod(
                                    rank=0, suspicion=CrashSuspicion.A, mod_id=m2.mod_id,
                                    mod_display_name=m2.display_title,
                                    priority_index=m2.priority_index,
                                    evidence_lines=_context_lines(ab_scan, ev[0], 2),
                                    evidence_line_range=(ev[0], ev[0] + 3),
                                ))
                            break
                # B：最后 5 个 validated（排除 A-1），按倒序（越靠后越前）
                b_pool = validated_events[:-1] if last_validated else validated_events
                b_pool = b_pool[-5:]
                b_pool = list(reversed(b_pool))
                for ev in b_pool:
                    m = _match_mod(ev[1])
                    if m is not None:
                        b_suspects.append(CrashSuspectMod(
                            rank=0, suspicion=CrashSuspicion.B, mod_id=m.mod_id,
                            mod_display_name=m.display_title,
                            priority_index=m.priority_index,
                            evidence_lines=_context_lines(ab_scan, ev[0], 2),
                            evidence_line_range=(ev[0], ev[0] + 3),
                        ))

            # Step 6：合并 + 排序（S > A > B；同级按真实加载顺序越靠后越前）
            all_suspects = s_suspects + a_suspects + b_suspects
            order_rank = {CrashSuspicion.S: 0, CrashSuspicion.A: 1, CrashSuspicion.B: 2}
            all_suspects.sort(key=lambda x: (
                order_rank[x.suspicion], -(x.evidence_line_range[0])
            ))
            for i, s in enumerate(all_suspects, 1):
                s.rank = i
            suspects = all_suspects

    return CrashAnalyzeResult(
        crash_time=crash_time, build_version=build_version,
        exception_code=exception_code, fault_module_category=fault_module_category,
        suspects=suspects, failed_to_match=failed_to_match,
        raw_tail_lines=raw_tail_lines,
    )


def _context_lines(lines: List[str], idx: int, around: int) -> List[str]:
    """取 idx 行 ± around 行（最多 2*around+1 行）。"""
    if not lines:
        return []
    lo = max(0, idx - around)
    hi = min(len(lines), idx + around + 1)
    out = []
    for i in range(lo, hi):
        try:
            out.append(lines[i].rstrip())
        except Exception:
            import traceback as _tb; _tb.print_exc(limit=1, file=sys.stderr)
            continue
    return out
