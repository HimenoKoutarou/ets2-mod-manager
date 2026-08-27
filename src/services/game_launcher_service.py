"""services/game_launcher_service.py — 发现 eurotrucks2.exe 并启动游戏 + 监控崩溃。

公共接口：
    find_game_exe() -> Optional[Path]      发现 ETS2 / ATS exe 路径
    launch_and_watch(exe_path, docs_dir, on_exit) -> GameLaunchHandle
                                          启动游戏，后台等退出，退出后回调 on_exit(crashed, crash_txt, log_txt)
"""
from __future__ import annotations

import os
import sys
import time
import winreg
import subprocess
import threading
from pathlib import Path
from typing import Optional, Callable, Any

ETS2_APPID = "227300"
ATS_APPID = "270880"


def find_game_exe() -> Optional[Path]:
    """通过 Steam 注册表 + 常见安装路径扫描 eurotrucks2.exe / amtrucks.exe。"""
    # 1. Steam 注册表 SteamPath
    steam_install = None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            steam_path, _ = winreg.QueryValueEx(k, "SteamPath")
            steam_install = Path(steam_path)
    except OSError:
        pass

    # 2. 扫描 Steam libraryfolders + 常见路径
    search_roots = []
    if steam_install:
        search_roots.append(steam_install / "steamapps" / "common")
        # 读 libraryfolders.vdf
        vdf = steam_install / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            try:
                text = vdf.read_text(encoding="utf-8", errors="ignore")
                import re
                for m in re.finditer(r'"path"\s*"([^"]+)"', text):
                    p = Path(m.group(1).replace("\\\\", "\\"))
                    search_roots.append(p / "steamapps" / "common")
            except Exception:
                pass

    # 常见盘符
    for drive in ["C:", "D:", "E:", "F:", "G:"]:
        for pattern in [
            Path(drive) / "Program Files (x86)" / "Steam" / "steamapps" / "common",
            Path(drive) / "Steam" / "steamapps" / "common",
            Path(drive) / "SteamLibrary" / "steamapps" / "common",
        ]:
            if pattern not in search_roots:
                search_roots.append(pattern)

    # 3. 在每个 common/ 下找 Euro Truck Simulator 2 / American Truck Simulator
    exe_names = ["eurotrucks2.exe", "amtrucks.exe"]
    for root in search_roots:
        if not root.exists():
            continue
        for game_dir_name in ["Euro Truck Simulator 2", "American Truck Simulator"]:
            game_dir = root / game_dir_name
            if not game_dir.is_dir():
                continue
            # exe 在 game_dir/bin/win_x64/ 下
            for exe_name in exe_names:
                for sub in ["bin/win_x64", "bin/win_x86", ""]:
                    exe = game_dir / sub / exe_name if sub else game_dir / exe_name
                    if exe.exists():
                        return exe
            # 兜底：递归搜 2 层
            try:
                for child in game_dir.rglob("*.exe"):
                    if child.name.lower() in exe_names:
                        return child
            except OSError:
                continue
    return None


def find_game_docs_dir() -> Optional[Path]:
    """发现 ETS2 Documents 目录（用于 crash.txt / log.txt 定位）。"""
    userprofile = os.environ.get("USERPROFILE")
    if not userprofile:
        return None
    candidates = [
        Path(userprofile) / "Documents" / "Euro Truck Simulator 2",
        Path(userprofile) / "OneDrive" / "Documents" / "Euro Truck Simulator 2",
        Path(userprofile) / "Documents" / "American Truck Simulator",
        Path(userprofile) / "OneDrive" / "Documents" / "American Truck Simulator",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


class GameLaunchHandle:
    """游戏启动句柄：持有进程对象 + watchdog 线程。"""
    def __init__(self, process: subprocess.Popen, docs_dir: Path,
                 on_exit: Callable[[bool, Optional[Path], Optional[Path]], None]):
        self.process = process
        self.docs_dir = docs_dir
        self.on_exit = on_exit
        self._crash_mtime_before: float = 0.0
        self._watchdog: Optional[threading.Thread] = None
        self._stopped = False

    def start(self) -> None:
        # 记录启动前 crash.txt mtime
        crash_txt = self.docs_dir / "game.crash.txt"
        if crash_txt.exists():
            self._crash_mtime_before = crash_txt.stat().st_mtime

        self._watchdog = threading.Thread(target=self._watch, daemon=True)
        self._watchdog.start()

    def _watch(self) -> None:
        """等待游戏进程退出，然后判断是否崩溃。"""
        try:
            self.process.wait()
        except Exception:
            pass

        if self._stopped:
            return

        time.sleep(0.5)  # 等文件系统刷新

        crash_txt = self.docs_dir / "game.crash.txt"
        log_txt = self.docs_dir / "game.log.txt"

        crashed = False
        # 判断 1：crash.txt mtime 变新
        if crash_txt.exists():
            mtime_after = crash_txt.stat().st_mtime
            if mtime_after > self._crash_mtime_before:
                crashed = True

        # 判断 2：进程退出码非 0
        rc = self.process.returncode
        if rc is not None and rc != 0:
            crashed = True

        try:
            self.on_exit(
                crashed,
                crash_txt if crash_txt.exists() else None,
                log_txt if log_txt.exists() else None,
            )
        except Exception:
            pass

    def is_running(self) -> bool:
        return self.process.poll() is None

    def stop(self) -> None:
        self._stopped = True
        try:
            if self.process.poll() is None:
                self.process.terminate()
        except Exception:
            pass


def launch_and_watch(
    exe_path: Path,
    docs_dir: Optional[Path] = None,
    on_exit: Optional[Callable[[bool, Optional[Path], Optional[Path]], None]] = None,
) -> Optional[GameLaunchHandle]:
    """启动游戏并监控。返回 GameLaunchHandle 或 None（启动失败）。"""
    if not exe_path.exists():
        return None

    if docs_dir is None:
        docs_dir = find_game_docs_dir()
    if docs_dir is None:
        return None

    try:
        proc = subprocess.Popen(
            [str(exe_path)],
            cwd=str(exe_path.parent),
        )
    except Exception:
        return None

    handle = GameLaunchHandle(proc, docs_dir, on_exit or (lambda *a: None))
    handle.start()
    return handle
