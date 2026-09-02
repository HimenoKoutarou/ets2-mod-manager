from __future__ import annotations

import os
import sys
import winreg
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List


ETS2_APPID = "227300"


@dataclass
class ETS2Paths:
    """ETS2 所有相关路径集合"""
    documents_dir: Path                        # .../Documents/Euro Truck Simulator 2
    mod_dir: Path                              # .../mod
    mods_info_path: Path                       # .../mods_info.sii
    profiles_dir: Path                         # .../profiles (本地)
    steam_profiles_dir: Optional[Path]         # .../steam_profiles (如果存在)
    workshop_content_dir: Optional[Path]       # Steam/steamapps/workshop/content/227300
    steam_cloud_dir: Optional[Path]            # Steam/userdata/<uid>/227300/remote/profiles


def _find_documents_dir() -> Optional[Path]:
    """定位 Documents\\Euro Truck Simulator 2"""
    userprofile = os.environ.get("USERPROFILE")
    candidates = []
    if userprofile:
        candidates.append(Path(userprofile) / "Documents" / "Euro Truck Simulator 2")
        candidates.append(Path(userprofile) / "OneDrive" / "Documents" / "Euro Truck Simulator 2")
        candidates.append(Path(userprofile) / "我的文档" / "Euro Truck Simulator 2")
    # 也可尝试注册表 Known Folder，不过上面基本够用
    for c in candidates:
        if c.exists():
            return c
    # 兜底返回第一个（让上层能显示不存在）
    return candidates[0] if candidates else None


def _find_steam_workshop() -> Optional[Path]:
    """找 Steam Workshop content/227300 目录"""
    # 尝试多个常见 Steam 安装位置
    steams = []
    for drive in ["C:", "D:", "E:", "F:", "G:"]:
        # ``Path("E:")`` is drive-relative on Windows (``E:SteamLibrary``),
        # which may resolve against an unrelated current directory. Build an
        # absolute drive root before appending Steam folders.
        root = Path(drive + "\\")
        steams.append(root / "Program Files (x86)" / "Steam" / "steamapps" / "workshop" / "content" / ETS2_APPID)
        steams.append(root / "Steam" / "steamapps" / "workshop" / "content" / ETS2_APPID)
        steams.append(root / "SteamLibrary" / "steamapps" / "workshop" / "content" / ETS2_APPID)
    # 注册表查找 Steam 安装路径
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            steam_path, _ = winreg.QueryValueEx(k, "SteamPath")
            p = Path(steam_path) / "steamapps" / "workshop" / "content" / ETS2_APPID
            steams.append(p)
    except OSError:
        pass
    for s in steams:
        if s.exists():
            return s
    return None


def _find_steam_cloud() -> Optional[Path]:
    """找 .../Steam/userdata/<uid>/227300/remote/profiles"""
    # 先找 Steam 安装目录 / userdata
    userdata_candidates = []
    for drive in ["C:", "D:", "E:", "F:", "G:"]:
        root = Path(drive + "\\")
        for sp in [root / "Program Files (x86)" / "Steam" / "userdata",
                   root / "Program Files" / "Steam" / "userdata",
                   root / "Steam" / "userdata"]:
            if sp.exists():
                userdata_candidates.append(sp)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            steam_path, _ = winreg.QueryValueEx(k, "SteamPath")
            p = Path(steam_path) / "userdata"
            if p.exists():
                userdata_candidates.append(p)
    except OSError:
        pass
    for ud in userdata_candidates:
        # 遍历每个 uid 子目录
        for uid_dir in ud.iterdir():
            if not uid_dir.is_dir():
                continue
            pr = uid_dir / ETS2_APPID / "remote" / "profiles"
            if pr.exists():
                return pr
            # 兼容：检查 remote 下其他子目录结构（如 remote/profile 单数，或 profile folder 直接放在 remote 下）
            remote_dir = uid_dir / ETS2_APPID / "remote"
            if remote_dir.exists() and remote_dir.is_dir():
                try:
                    for sub in remote_dir.iterdir():
                        if not sub.is_dir():
                            continue
                        # 检查该子目录下是否有直接包含 profile.sii 的文件夹（形如 <profile_hash>/profile.sii）
                        found = False
                        for profile_hash_dir in sub.iterdir():
                            if profile_hash_dir.is_dir() and (profile_hash_dir / "profile.sii").exists():
                                found = True
                                break
                        if found:
                            return remote_dir
                except OSError:
                    continue
    return None


def detect_paths() -> ETS2Paths:
    """自动探测所有路径"""
    doc = _find_documents_dir() or Path.home() / "Documents" / "Euro Truck Simulator 2"

    steam_profiles_dir: Optional[Path] = None
    sp_primary = doc / "steam_profiles"
    if sp_primary.exists():
        steam_profiles_dir = sp_primary
    else:
        candidates = sorted(
            [p for p in doc.glob("steam_profiles*") if p.is_dir() and p.name != "steam_profiles"],
            key=lambda p: p.name,
            reverse=True,
        )
        for c in candidates:
            try:
                if c.exists() and c.is_dir():
                    steam_profiles_dir = c
                    break
            except OSError:
                continue

    return ETS2Paths(
        documents_dir=doc,
        mod_dir=doc / "mod",
        mods_info_path=doc / "mods_info.sii",
        profiles_dir=doc / "profiles",
        steam_profiles_dir=steam_profiles_dir,
        workshop_content_dir=_find_steam_workshop(),
        steam_cloud_dir=_find_steam_cloud(),
    )


def game_version_from_log(doc_dir: Path) -> str:
    """尝试从 game.log.txt 第一行里解析当前游戏版本（可选功能）"""
    log_path = doc_dir / "game.log.txt"
    if not log_path.exists():
        return ""
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            first = f.readline()
        # 形如 "00:00:00.001 : [sys] Euro Truck Simulator 2 init ver. 1.57.2.7s (64-bit) ..."
        m = __import__("re").search(r"ver\.\s*([\d.s]+)", first)
        return m.group(1) if m else ""
    except OSError:
        return ""
