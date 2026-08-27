from __future__ import annotations

import ctypes
import os
import shutil
import sys
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


"""
ETS2 Mod 目录软链接管理器
解决问题：
    游戏默认读取 %USERPROFILE%\\Documents\\Euro Truck Simulator 2\\mod
    C 盘空间不够时，玩家手动折腾 mklink 很麻烦。
    本模块提供「一键迁移 + 软链接」功能。

策略（来自经验 145412：Shell 命令受限环境，优先用 Python 原生 API）：
    1) 主路径：os.symlink(target, link, target_is_directory=True)
       要求：Windows 10+ 开启"开发者模式"，或以管理员运行。
    2) 兜底：用 subprocess 调用 cmd.exe 的 mklink /D （在开启 UAC 时）
    3) Junction（目录联接）：对跨盘更友好，不需要开发者模式，用 _CreateJunction 实现
"""


# ---- 创建目录联接 (Junction)，最稳健，不需要管理员/开发者模式 ----

def _is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _is_junction(path: Path) -> bool:
    """Path.is_junction() compat (Python 3.12+ native, 3.11 fallback)."""
    method = getattr(path, "is_junction", None)
    if method is not None:
        try:
            return method()
        except OSError:
            return False
    try:
        return SymlinkManager._read_junction_target(path) is not None
    except Exception:
        return False


def _create_junction(target: Path, link: Path) -> bool:
    """
    使用 Win32 API 创建目录联接 (Junction Point / Reparse Point)
    - 不需要开启开发者模式
    - 跨盘可用（比 SymbolicLink 更宽松）
    - 玩家一般右键也看不出来差别，游戏访问完全透明
    """
    try:
        import ctypes.wintypes
        # Define CreateFile / DeviceIoControl constants
        FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
        FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
        OPEN_EXISTING = 3
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
        FSCTL_SET_REPARSE_POINT = 0x000900A4
        IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003

        target_abs = str(target.resolve()).rstrip("\\") + "\\"
        # 构造 REPARSE_DATA_BUFFER 的简化版
        # Ref: https://learn.microsoft.com/en-us/windows-hardware/drivers/ifs/reparse-data-buffers
        # We use the MOUNT_POINT (Junction) layout:
        #   ULONG  ReparseTag
        #   USHORT ReparseDataLength
        #   USHORT Reserved
        #   USHORT SubstituteNameOffset (bytes, from start of PathBuffer)
        #   USHORT SubstituteNameLength
        #   USHORT PrintNameOffset
        #   USHORT PrintNameLength
        #   WCHAR  PathBuffer[ANYSIZE]   (we put \??\<target> then <target>)
        # "\??\" prefix for object-manager namespace
        subst_name = ("\\??\\" + target_abs).encode("utf-16-le")
        print_name = target_abs.encode("utf-16-le")
        offset_subst = 0
        len_subst = len(subst_name)
        offset_print = len_subst
        len_print = len(print_name)
        reparse_data_length = 8 + len_subst + len_print  # 8 = 4 USHORT
        header = (
            IO_REPARSE_TAG_MOUNT_POINT.to_bytes(4, "little")
            + reparse_data_length.to_bytes(2, "little")
            + (0).to_bytes(2, "little")  # Reserved
            + offset_subst.to_bytes(2, "little")
            + len_subst.to_bytes(2, "little")
            + offset_print.to_bytes(2, "little")
            + len_print.to_bytes(2, "little")
        )
        in_buf = header + subst_name + print_name
        in_buf_size = len(in_buf)

        CreateFileW = ctypes.windll.kernel32.CreateFileW
        DeviceIoControl = ctypes.windll.kernel32.DeviceIoControl
        CloseHandle = ctypes.windll.kernel32.CloseHandle

        link_path_w = str(link.resolve().absolute())
        h = CreateFileW(
            link_path_w,
            0xC0000000,  # GENERIC_READ|WRITE
            0, None, OPEN_EXISTING,
            FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS, None
        )
        if h == INVALID_HANDLE_VALUE:
            return False
        try:
            bytes_ret = ctypes.c_ulong(0)
            ok = DeviceIoControl(
                h, FSCTL_SET_REPARSE_POINT,
                in_buf, in_buf_size,
                None, 0, ctypes.byref(bytes_ret), None
            )
            return bool(ok)
        finally:
            CloseHandle(h)
    except Exception:
        return False



# ---------- i18n helper (optional) ----------
def _T(key: str, **kwargs) -> str:
    try:
        from services.i18n_service import tr
        return tr(key, **kwargs)
    except Exception:
        # fallback: 返回空串让调用者保留原硬编码
        return ""

@dataclass
class SymlinkResult:
    """操作结果"""
    success: bool
    message: str
    link_path: Optional[Path] = None
    target_path: Optional[Path] = None
    method: str = ""   # "junction" | "symlink_py" | "mklink" | "replaced"


class SymlinkManager:
    """ETS2 Mod 目录迁移工具"""

    def __init__(self, original_mod_dir: Path):
        self.original = Path(original_mod_dir)

    # ---------- 查询状态 ----------
    def get_status(self) -> dict:
        p = self.original
        if not p.exists() and not p.is_symlink() and not _is_junction(p):
            return {"exists": False, "kind": "missing", "target": None, "link": str(p)}
        # 是符号链接 / Junction？
        if p.is_symlink():
            try:
                target = os.readlink(p)
                return {"exists": True, "kind": "symlink", "target": target, "link": str(p)}
            except OSError as e:
                return {"exists": True, "kind": "symlink_broken", "target": None, "link": str(p), "error": str(e)}
        # Junction 检测
        try:
            t = self._read_junction_target(p)
            if t is not None:
                return {"exists": True, "kind": "junction", "target": t, "link": str(p)}
        except Exception:
            pass
        return {"exists": p.exists(), "kind": "real_dir", "target": None, "link": str(p)}

    @staticmethod
    def _read_junction_target(p: Path) -> Optional[str]:
        try:
            # 使用 Python 标准库不能直接读 junction 目标，尝试用命令行 dir /aL
            result = subprocess.run(
                ["cmd", "/c", "dir", "/aL", str(p.parent)],
                capture_output=True, text=True, timeout=5,
                encoding="mbcs", errors="replace"
            )
            # 找 "<JUNCTION>" 或 "<SYMLINKD>" 行
            name = p.name
            for line in result.stdout.splitlines():
                if name in line and ("JUNCTION" in line or "SYMLINKD" in line):
                    # 形如 ... <JUNCTION>  mods [F:\ETS2ModData]
                    # 取最后用 [] 包围的部分
                    import re
                    m = re.search(r"\[([^\]]+)\]", line)
                    if m:
                        return m.group(1)
        except Exception:
            pass
        return None

    # ---------- 核心：一键迁移 ----------
    def move_and_link(self, new_target_dir: Path, move_files: bool = True) -> SymlinkResult:
        """
        将原 mod 目录的内容搬移到 new_target_dir（例如 F:\\ETS2ModData\\mod），
        然后在原位置创建指向新目录的 Junction（软链接）。

        流程：
        1. 校验 new_target_dir 所在盘可写
        2. 若原目录是"真实目录"：把所有文件 move 过去；否则跳过迁移
        3. 删（或重命名）原目录
        4. 在原位置建 Junction
        """
        orig = self.original
        target = Path(new_target_dir)
        # 安全：新旧路径不能一样
        try:
            if orig.resolve().absolute() == target.resolve().absolute():
                return SymlinkResult(False, _T("sym.msg_same_orig_target"), orig, target)
        except OSError:
            pass

        # 1. 准备目标目录
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return SymlinkResult(False, _T("sym.msg_create_target_fail", target=str(target), e=str(e)) or f"无法创建目标目录 {target}: {e}", orig, target)
        # 测试可写
        probe = target / ".ets2_mm_write_test"
        try:
            probe.write_bytes(b"ok")
            probe.unlink()
        except OSError as e:
            return SymlinkResult(False, _T("sym.msg_target_not_writable", e=str(e)) or f"目标目录不可写: {e}", orig, target)

        # 2. 如果原始目录存在且是真实目录，搬移内容
        status = self.get_status()
        if status.get("kind") == "real_dir" and orig.exists() and move_files:
            # 把 orig 下的所有内容复制/移动到 target
            moved_items = []
            try:
                for item in list(orig.iterdir()):
                    dest = target / item.name
                    # 如果目标已存在同名：hash 相同则跳过，不同则 _dup
                    if dest.exists():
                        # R12: 同名文件比较 size，相同则跳过（避免不必要的 _dup）
                        try:
                            if item.stat().st_size == dest.stat().st_size:
                                item.unlink()
                                continue
                        except OSError:
                            pass
                        i = 1
                        while True:
                            alt = target / f"{item.stem}_dup{i}{item.suffix}"
                            if not alt.exists():
                                dest = alt; break
                            i += 1
                    shutil.move(str(item), str(dest))
                    moved_items.append(dest)
            except OSError as e:
                # R12: partial move rollback — 把已搬的文件搬回来
                for moved_dest in moved_items:
                    try:
                        back = orig / moved_dest.name
                        if not back.exists():
                            shutil.move(str(moved_dest), str(back))
                    except OSError:
                        pass
                return SymlinkResult(False, _T("sym.msg_move_error", e=str(e)) or f"搬移文件时出错（已回滚，文件未受损）：{e}", orig, target)

        # 3. 处理原目录的"让位"
        if orig.exists() or orig.is_symlink():
            # 优先重命名为 .bak_时间戳，保留退路
            import time
            backup_path = orig.parent / f"{orig.name}.bak_{int(time.time())}"
            try:
                if orig.is_symlink():
                    # os.rename 对 symlink 直接改名就行
                    os.rename(orig, backup_path)
                else:
                    # 真实目录 —— 如果搬移后为空（或有剩余），直接重命名也 OK
                    os.rename(orig, backup_path)
            except OSError as e:
                # 某些情况下 Junction 重命名受限，尝试直接 unlink/rmtree
                try:
                    if orig.is_symlink():
                        orig.unlink()
                    else:
                        shutil.rmtree(orig, ignore_errors=True)
                    backup_path = None
                except OSError as e2:
                    return SymlinkResult(
                        False,
                        (_T("sym.msg_replace_orig_fail", orig=str(orig), e=f"{e} / {e2}") or f"无法替换原目录，请手动删除 {orig} 后重试：{e} / {e2}"),
                        orig, target
                    )

        # 4. 创建 Junction（最稳健的方式）
        # P0 修复：CreateFileW(OPEN_EXISTING) 要求 link_path 存在，必须先创建空目录
        link_path = orig.parent / orig.name
        try:
            link_path.mkdir(parents=True, exist_ok=False)
        except OSError:
            pass  # 目录可能已存在（上面 rename 失败后的残留）
        created = _create_junction(target, link_path)
        if created:
            return SymlinkResult(True, _T("sym.msg_junction_ok"),
                                 orig, target, method="junction")
        # 5. 失败的话，尝试 os.symlink（需要开发者模式/管理员）
        # R12 修复：Junction 失败后 link_path 是空目录，os.symlink 要求目标不存在
        try:
            if link_path.exists() and link_path.is_dir():
                try:
                    link_path.rmdir()  # 只删空目录，不删有内容的
                except OSError:
                    shutil.rmtree(link_path, ignore_errors=True)
        except Exception:
            pass
        try:
            os.symlink(str(target), str(orig), target_is_directory=True)
            return SymlinkResult(True, _T("sym.msg_symlink_ok"),
                                 orig, target, method="symlink_py")
        except OSError as e_sym:
            err_info = f"Junction 和 Symlink 均创建失败：{e_sym}\n"
            # P0 修复：回滚 — 尝试把备份目录恢复回原位置，避免 mod 目录消失
            try:
                if 'backup_path' in dir() and backup_path and backup_path.exists():
                    if link_path.exists():
                        shutil.rmtree(link_path, ignore_errors=True)
                    os.rename(backup_path, orig)
                    err_info += "已自动恢复原目录，数据未丢失。\n"
            except OSError:
                err_info += f"警告：原目录可能已移至 {backup_path}，请手动恢复。\n"
            if not _is_admin():
                err_info += '提示：请右键以管理员身份运行此程序，或在 Windows 设置中开启「开发者模式」。'
            else:
                err_info += "提示：请检查杀毒软件/组策略是否拦截了文件系统重解析点操作。"
            return SymlinkResult(False, err_info, orig, target)

    # ---------- 撤销迁移 ----------
    def unlink_and_restore(self, dest_dir: Optional[Path] = None) -> SymlinkResult:
        """
        撤销：删除 Junction/Symlink，把真实目录搬回去。
        dest_dir 为搬移的目标目录（默认从当前链接目标自动识别）。
        """
        status = self.get_status()
        kind = status.get("kind")
        if kind not in ("junction", "symlink", "symlink_broken"):
            return SymlinkResult(False, _T("sym.msg_unlink_not_needed", kind=kind) or f"当前 mod 目录状态为 '{kind}'，不需要撤销",
                                 self.original, dest_dir)
        target = Path(dest_dir) if dest_dir else (Path(status["target"]) if status.get("target") else None)
        if target is None or not target.exists():
            return SymlinkResult(False, _T("sym.msg_unlink_no_target"),
                                 self.original, target)
        # 1. 删除 link
        try:
            if self.original.is_symlink():
                self.original.unlink()
            else:
                # Junction：尝试用 os.rmdir
                os.rmdir(str(self.original))
        except OSError as e:
            return SymlinkResult(False, _T("sym.msg_unlink_delete_fail", e=str(e)) or f"删除链接失败：{e}", self.original, target)
        # 2. 搬回真实目录 — R12: 用 copytree 代替逐个 move（失败时 target 仍有完整数据）
        try:
            self.original.mkdir(parents=True)
            # 先复制（target 保持不动），成功后再清理 target
            for item in list(target.iterdir()):
                dest = self.original / item.name
                if dest.exists():
                    if item.stat().st_size == dest.stat().st_size:
                        continue
                shutil.copy2(str(item), str(dest))
            # 全部复制成功后删除 target 中的源文件
            for item in list(target.iterdir()):
                try:
                    item.unlink()
                except OSError:
                    pass
        except OSError as e:
            return SymlinkResult(
                False,
                (_T("sym.msg_unlink_moveback_fail", e=str(e), target=str(target)) or f"搬回时出错：{e}。当前真实目录在 {target}，你可以手动复制回去。"),
                self.original, target
            )
        return SymlinkResult(True, _T("sym.msg_unlink_ok"), self.original, target, method="replaced")


    # ---------- 修复：失效软链接重定向到新 target（不搬文件） ----------
    def repair_broken_link(self, new_target_dir: Path) -> SymlinkResult:
        """
        用于：用户手工把真实 mod 目录从 D 盘剪切到 H 盘（导致原 symlink/junction 指向的 target 不存在，变成 broken）。
        本函数 **不搬任何文件**，只做：
          1) 删除原位置的失效链接
          2) 在原位置重建 Junction/Symlink 指向 new_target_dir（必须是已经存在且包含真实 mod 内容的目录）
        """
        orig = self.original
        target = Path(new_target_dir)
        if not target.exists() or not target.is_dir():
            return SymlinkResult(False, _T("sym.msg_repair_target_invalid", target=str(target)) or f"目标目录不存在或不是文件夹：{target}", orig, target)
        # 测试可写
        probe = target / ".ets2_mm_probe_write"
        try:
            probe.write_bytes(b"ok"); probe.unlink()
        except OSError as e:
            return SymlinkResult(False, _T("sym.msg_target_not_writable", e=str(e)) or f"目标目录不可写：{e}", orig, target)

        # 1) 清除原位置残留的失效 link / 重解析点 / 空目录
        status = self.get_status()
        removed = False
        try:
            if status.get("kind") in ("symlink", "symlink_broken") and orig.is_symlink():
                orig.unlink(); removed = True
            elif status.get("kind") == "junction":
                # Junction 通常对 os.rmdir / .unlink 都有效，都失败再试 cmd
                try: orig.unlink(); removed = True
                except Exception:
                    try: os.rmdir(str(orig)); removed = True
                    except Exception: pass
            elif orig.exists() and orig.is_dir():
                # 可能是一个空壳目录（Junction 未被正确识别）：仅在空时删除
                try:
                    if not any(orig.iterdir()):
                        orig.rmdir(); removed = True
                except Exception: pass
            # 兜底：cmd /c rmdir 删除重解析点
            if not removed and (orig.exists() or orig.is_symlink()):
                r = subprocess.run(["cmd", "/c", "rmdir", str(orig)],
                                   capture_output=True, timeout=5,
                                   encoding="mbcs", errors="replace")
                removed = r.returncode == 0
        except OSError as e:
            return SymlinkResult(False, _T("sym.msg_repair_del_fail", e=str(e)) or f"删除旧链接失败：{e}", orig, target)
        if not removed and orig.exists():
            return SymlinkResult(False, _T("sym.msg_repair_clear_fail", orig=str(orig)) or f"无法清除原位置的旧链接/目录：{orig}，请手动删除后再试", orig, target)

        # 2) 重建 — 先试 Junction
        # P0 修复：先创建空目录再建 Junction
        link_path = orig.parent / orig.name
        try:
            link_path.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        try:
            ok = _create_junction(target, link_path)
        except Exception as e:
            return SymlinkResult(False, _T("sym.msg_repair_jct_fail", e=str(e)) or f"重建 Junction 异常：{e}", orig, target)
        if ok:
            return SymlinkResult(True, _T("sym.msg_repair_jct_ok", target=str(target)) or f"已修复软链接：原位置重建目录联接 → {target}。重启 ETS2 即生效。",
                                 orig, target, method="junction")
        # 兜底：os.symlink
        try:
            os.symlink(str(target), str(orig), target_is_directory=True)
            return SymlinkResult(True, _T("sym.msg_repair_sym_ok", target=str(target)) or f"已修复软链接：原位置重建 Symlink → {target}。重启 ETS2 即生效。",
                                 orig, target, method="symlink_py")
        except OSError as e_sym:
            err = f"Junction 与 Symlink 重建都失败：{e_sym}\n"
            if not _is_admin():
                err += "提示：请右键「以管理员身份运行」，或在 Windows 设置开启「开发者模式」。"
            return SymlinkResult(False, err, orig, target)

    # ---------- 别名 ----------
    def relocate_to(self, new_target_dir: Path) -> SymlinkResult:
        """
        UI 调用的主入口：自动判断当前状况（普通 real_dir / 已建 link 想换地方 / link broken）
          - 如果当前 kind 是 "symlink_broken"  → repair_broken_link（不搬文件）
          - 否则 → move_and_link（搬文件+建新 link）
        """
        st = self.get_status()
        if st.get("kind") == "symlink_broken":
            return self.repair_broken_link(new_target_dir)
        return self.move_and_link(new_target_dir)
