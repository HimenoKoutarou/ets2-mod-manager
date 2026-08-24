from __future__ import annotations

import os
import re
import shutil
import subprocess
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# 相对路径 import（项目根被加到 sys.path 后可用）
from core.sii_parser import parse_sii, parse_sii_file, SiiUnit
from services.backup_service import BackupService


# =========================================================================
#  SCS Sii 加解密（兼容 1.48+ 的 profile.sii 加密）
#  算法：32-bit XXTEA（Delta=0x9e3779b9，取 k[4] 作为 key 魔改后参与）
#  文件头："Sii\x00"  或  "#S*" 系列 —— 具体实现见下
#  这里兼容：公开社区常用的 "把加密文件作为 SII_Decrypt 输入" 的行为，
#  若 assets/bin/SII_Decrypt.exe 存在则优先调用（最稳）。
# =========================================================================

# 32-bit 循环左移
def _rol32(x: int, n: int) -> int:
    return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF


def _xxtea_encrypt(v: List[int], k: List[int]) -> List[int]:
    n = len(v)
    if n < 2:
        return v[:]
    out = list(v)
    delta = 0x9E3779B9
    q = 6 + 52 // n
    s = 0
    z = out[-1]
    for _ in range(q):
        s = (s + delta) & 0xFFFFFFFF
        e = (s >> 2) & 3
        for p in range(n):
            y = out[(p + 1) % n]
            mx = (((z >> 5) ^ (y << 2)) + ((y >> 3) ^ (z << 4))) ^ ((s ^ y) + (k[(p & 3) ^ e] ^ z))
            out[p] = (out[p] + mx) & 0xFFFFFFFF
            z = out[p]
    return out


def _xxtea_decrypt(v: List[int], k: List[int]) -> List[int]:
    n = len(v)
    if n < 2:
        return v[:]
    out = list(v)
    delta = 0x9E3779B9
    q = 6 + 52 // n
    s = (q * delta) & 0xFFFFFFFF
    y = out[0]
    for _ in range(q):
        e = (s >> 2) & 3
        for p in range(n - 1, -1, -1):
            z = out[(p - 1) % n]
            mx = (((z >> 5) ^ (y << 2)) + ((y >> 3) ^ (z << 4))) ^ ((s ^ y) + (k[(p & 3) ^ e] ^ z))
            out[p] = (out[p] - mx) & 0xFFFFFFFF
            y = out[p]
        s = (s - delta) & 0xFFFFFFFF
    return out


def _derive_scs_key(text_key: str) -> List[int]:
    """把 SCS 文本 key 折成 4 个 uint32。"""
    # 社区流传的 profile.sii key: "ScsCryptionIsForSissies!!!!!" 衍生
    raw = (text_key + "\x00" * 16)[:16].encode("latin-1", errors="replace")
    return list(struct.unpack("<IIII", raw))


_PROFILE_DEFAULT_KEY = "ScsCryptionIsForSissies!!!!!"

# SCS 文件头 3 种常见签名
_HEAD_4S = b"Sii\x00"   # Sii NUL (旧版)
_HEAD_2H = b"#S"        # Sii# / AEM! (社区)
_HEAD_SCSC = b"ScsC"    # 新版 1.50+ ScsCryption (需外部 SII_Decrypt.exe)



def _looks_encrypted(data: bytes) -> bool:
    if data.startswith(_HEAD_4S):
        return True
    if data.startswith(_HEAD_SCSC):
        return True
    if data.startswith(_HEAD_2H) and len(data) > 3 and data[2:3] != b"\n" and data[2:3] != b"i":
        return True
    # 看有没有 SiiNunit / profile : 之类的明文标记
    head = data[:2000]
    if b"SiiNunit" in head or b"profile :" in head or b"mod_manager" in head:
        return False
    return True


def decrypt_profile_bytes(data: bytes, key_text: str = _PROFILE_DEFAULT_KEY) -> bytes:
    """尝试解密 profile.sii 的原始字节；若判断为明文直接原封返回。"""
    if not _looks_encrypted(data):
        return data
    # 新版 ScsC 头：内置 XXTEA 解不开，直接返回原样让上层强制走 SII_Decrypt.exe
    if data.startswith(_HEAD_SCSC):
        return data
    k = _derive_scs_key(key_text)
    # 多种头处理：
    #   A. Sii\x00 + uint32 body_len + uint32 reserved + <cipher>
    #   B. 其他签名：直接按 XXTEA 块尝试
    if data.startswith(_HEAD_4S) and len(data) >= 12:
        body_len = struct.unpack("<I", data[4:8])[0]
        body = data[12: 12 + body_len]
    else:
        # 跳过前 8 字节尝试
        body = data[8:] if len(data) % 4 == 0 and len(data) > 8 else data
    n32 = len(body) // 4
    if n32 < 2:
        # 回退：整文件解密看看
        if len(data) % 4 == 0 and len(data) // 4 >= 2:
            v = list(struct.unpack("<" + "I" * (len(data) // 4), data))
            dec = _xxtea_decrypt(v, k)
            return struct.pack("<" + "I" * len(dec), *dec)
        return data
    v = list(struct.unpack("<" + "I" * n32, body))
    dec = _xxtea_decrypt(v, k)
    out = struct.pack("<" + "I" * n32, *dec)
    # 如果没成功（还是没 SiiNunit 头），尝试换 key "SCSsucksDonkeyBalls!!!"
    if b"SiiNunit" not in out and b"profile :" not in out:
        alt = _derive_scs_key("SCSsucksDonkeyBalls!!!")
        dec = _xxtea_decrypt(v, alt)
        out = struct.pack("<" + "I" * n32, *dec)
    # 仍然失败则返回原始数据（让上层走 SII_Decrypt.exe 外部）
    return out


def encrypt_profile_bytes(plaintext: bytes, key_text: str = _PROFILE_DEFAULT_KEY) -> bytes:
    """加密成 Sii\x00 格式。"""
    k = _derive_scs_key(key_text)
    # pad 到 4 字节边界
    pad = (-len(plaintext)) % 4
    body = plaintext + (b"\x00" * pad)
    n32 = len(body) // 4
    v = list(struct.unpack("<" + "I" * n32, body)) if n32 else []
    if n32 >= 2:
        enc = _xxtea_encrypt(v, k)
        cipher = struct.pack("<" + "I" * n32, *enc)
    else:
        cipher = body
    header = _HEAD_4S + struct.pack("<II", len(cipher), 0)
    return header + cipher


# =========================================================================
#  Profile 序列化：把 active_mods 数组写回 profile.sii 文本（不重写全部）
# =========================================================================

# 匹配 "active_mods[N]:" 或 "active_mods[]:" 等带引号值（真实模组条目）
_KEY_VALUE_LINE = re.compile(r"^(?P<indent>\s*)active_mods\s*(?P<idx>\[\d*\])\s*:\s*\"(?P<val>.*)\"\s*$")
# 匹配标量 "active_mods: N" 或 "active_mods : N"（纯数字长度头）
_LEN_LINE = re.compile(r"^(?P<indent>\s*)active_mods\s*:\s*(?P<val>\d+)\s*(?P<comment>#.*)?$")


def rewrite_active_mods_in_text(plain_text: str, new_mods: List[str]) -> str:
    """
    在明文 profile.sii 文本里 "原位" 替换所有 active_mods[...] 条目，并更新 active_mods: N 长度头。
    - 旧条目更多 → 多余行清空；
    - 旧条目更少 → 在最后一个 active_mods 行之后追加；
    - 找不到 length 头和条目行时，在第一个 "}" 前构造新块。
    """
    lines = plain_text.split("\n")
    entry_positions: List[int] = []   # 有下标的条目
    len_position: Optional[int] = None
    template_line: Optional[str] = None
    for i, ln in enumerate(lines):
        m = _KEY_VALUE_LINE.match(ln)
        if m:
            entry_positions.append(i)
            template_line = ln
            continue
        if _LEN_LINE.match(ln):
            len_position = i

    # 决定 template / prefix indent
    if template_line is None:
        template = '    active_mods[{}]: "{}"'
        prefix_len = '    active_mods: {}'
    else:
        indent_match = re.match(r"^(\s*)", template_line)
        indent = indent_match.group(1) if indent_match else "    "
        template = f'{indent}active_mods[{{}}]: "{{}}"'
        prefix_len = f'{indent}active_mods: {{}}'

    result = lines[:]
    # 1) 先更新 length 头（若没有 → 在第一个 entry 之前插入一行）
    if len_position is not None:
        result[len_position] = prefix_len.format(len(new_mods))
    elif entry_positions:
        result.insert(entry_positions[0], prefix_len.format(len(new_mods)))
        # 插入后所有 entry 位置整体 +1
        entry_positions = [p + 1 for p in entry_positions]

    # 2) 覆盖 / 插入 / 清空条目
    n_old, n_new = len(entry_positions), len(new_mods)
    # 先处理 0..max-1 范围
    for j in range(max(n_old, n_new)):
        if j < n_old and j < n_new:
            result[entry_positions[j]] = template.format(j, _escape_quote(new_mods[j]))
        elif j < n_old:
            result[entry_positions[j]] = ""
        else:
            # 新条目不够：追加（entry_positions[-1] 可能在前面 insert 后变化）
            insert_at = entry_positions[-1] + 1 + (j - n_old)
            result.insert(insert_at, template.format(j, _escape_quote(new_mods[j])))
    return "\n".join(result)


def _escape_quote(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _unescape_profile_str(s: str) -> str:
    if not s:
        return s
    import re as _re
    def _repl_literal(m):
        try:
            hex_bytes = bytes([int(x, 16) for x in _re.findall(r'\\x([0-9a-fA-F]{2})', m.group(0))])
            return hex_bytes.decode('utf-8', errors='replace')
        except Exception:
            return m.group(0)
    pattern = _re.compile(r'(?:\\x[0-9a-fA-F]{2}){2,}')
    s = pattern.sub(_repl_literal, s)

    result = []
    i = 0
    n = len(s)
    while i < n:
        if ord(s[i]) >= 128:
            j = i
            while j < n and ord(s[j]) >= 128:
                j += 1
            seq_len = j - i
            if seq_len >= 2:
                try:
                    raw_bytes = s[i:j].encode('latin-1')
                    decoded = raw_bytes.decode('utf-8', errors='replace')
                    result.append(decoded)
                except Exception:
                    result.append(s[i:j])
            else:
                result.append(s[i:j])
            i = j
        else:
            result.append(s[i])
            i += 1
    return ''.join(result)
# 从 SiiUnit 读取 active_mods 模组列表（过滤 length 纯数字头）
def _extract_active_mods(u):
    """
    优先走 get_indexed("active_mods")，再回退到 get_list 并过滤纯数字开头的 length 标量。
    """
    result = u.get_indexed("active_mods")
    if result:
        return [x for x in result if not (isinstance(x, str) and x.strip().isdigit())]
    lst = u.get_list("active_mods")
    out = []
    for item in lst:
        if (not out) and isinstance(item, str) and item.strip().isdigit():
            continue
        out.append(item)
    return out


# =========================================================================
#  Profile 描述 / ProfileService
# =========================================================================

@dataclass
class ProfileInfo:
    profile_id: str                    # 文件夹名（hash，可能是中文 hex）
    location: str                      # "local" | "steam" | "cloud"
    folder: Path
    profile_sii: Path                  # 真实 profile.sii 路径
    is_encrypted: bool = False
    display_name: str = ""             # profile_name
    save_name: str = ""                # 存档显示名
    company_name: str = ""             # 公司/角色中文名
    mod_count: int = 0

    def __str__(self) -> str:
        label = self.company_name or self.display_name or self.save_name or self.profile_id
        return f"{label} [{self.location}] mods={self.mod_count}"


class ProfileService:
    """
    列出 profile、读取 active_mods、写回 active_mods、加解密、备份。
    """

    def __init__(self, paths, backup: Optional[BackupService] = None,
                 sii_decrypt_exe: Optional[Path] = None):
        self.paths = paths
        self.backup = backup or BackupService()
        self.sii_decrypt_exe = sii_decrypt_exe or self._auto_sii_decrypt()

    def _auto_sii_decrypt(self) -> Optional[Path]:
        bin_dir = Path(__file__).resolve().parents[2] / "assets" / "bin"
        for name in ("SII_Decrypt.exe", "sii_core.exe"):
            cand = bin_dir / name
            if cand.exists():
                return cand
        return None

    # ---------- 列出所有 profile ----------
    def list_profiles(self) -> List[ProfileInfo]:
        out: List[ProfileInfo] = []
        locations: List[Tuple[str, Optional[Path]]] = [
            ("local", self.paths.profiles_dir),
            ("steam", self.paths.steam_profiles_dir),
            ("cloud", self.paths.steam_cloud_dir),
        ]
        seen: set = set()
        for loc, folder in locations:
            if folder is None or not folder.exists():
                continue
            for d in sorted(folder.iterdir()):
                if not d.is_dir():
                    continue
                psii = d / "profile.sii"
                # 如果 loc=steam 且 profile.sii 不存在，可能是个"指针目录"，尝试 cloud 找
                if not psii.exists() and loc == "steam":
                    cloud_path = self.paths.steam_cloud_dir / d.name / "profile.sii" if self.paths.steam_cloud_dir else None
                    if cloud_path and cloud_path.exists():
                        psii = cloud_path
                if not psii.exists():
                    continue
                key = (loc, d.name)
                if key in seen:
                    continue
                seen.add(key)
                info = ProfileInfo(profile_id=d.name, location=loc, folder=d, profile_sii=psii)
                self._enrich(info)
                out.append(info)
        return out

    def _enrich(self, info: ProfileInfo) -> None:
        try:
            plain = self._get_plain_text(info.profile_sii)
            units = parse_sii(plain)
            if not units:
                return
            u = units[0]
            info.display_name = _unescape_profile_str(u.get("profile_name", "") or "")
            info.save_name = _unescape_profile_str(u.get("save_name", "") or "")
            info.company_name = _unescape_profile_str(u.get("company_name", "") or "")
            info.mod_count = len(_extract_active_mods(u))
        except Exception:
            return

    # ---------- 读写文件辅助 ----------
    def _get_plain_text(self, sii_path: Path) -> str:
        data = Path(sii_path).read_bytes()
        # 优先用内置解密（纯 Python，无需外部 exe）
        try:
            dec = decrypt_profile_bytes(data)
            if b"SiiNunit" in dec or b"profile :" in dec:
                return _decode_text(dec)
        except Exception:
            pass
        # 失败则尝试外部 SII_Decrypt.exe
        if self.sii_decrypt_exe and self.sii_decrypt_exe.exists():
            try:
                out = _run_sii_decrypt(self.sii_decrypt_exe, sii_path)
                if out is not None:
                    return _decode_text(out)
            except Exception:
                pass
        # 最后：按明文返回（也许已经是明文）
        return _decode_text(data)

    def _read_units(self, sii_path: Path) -> List[SiiUnit]:
        text = self._get_plain_text(sii_path)
        return parse_sii(text)

    # ---------- 读取 active_mods ----------
    def get_active_mods(self, prof: ProfileInfo) -> List[str]:
        units = self._read_units(prof.profile_sii)
        if not units:
            return []
        return _extract_active_mods(units[0])

    # ---------- 写回 active_mods ----------
    def set_active_mods(self, prof: ProfileInfo, new_mods: List[str]) -> Path:
        """
        1. 读取 profile.sii 原始字节并解密成文本
        2. 文本级原位重写 active_mods 条目
        3. 若原文件为加密 → 重新加密写回；若明文 → 明文写回
        4. 写前备份
        返回实际写入的文件路径
        """
        original_bytes = prof.profile_sii.read_bytes()
        was_encrypted = _looks_encrypted(original_bytes)
        # 解密文本
        plain = self._get_plain_text(prof.profile_sii)
        new_text = rewrite_active_mods_in_text(plain, list(new_mods))
        # 写前备份
        self.backup.backup(prof.profile_sii, tag="pre-write")
        # 输出字节
        out_bytes = new_text.encode("utf-8-sig")
        if was_encrypted:
            # 尝试重新加密
            try:
                out_bytes = encrypt_profile_bytes(new_text.encode("utf-8-sig"))
            except Exception:
                out_bytes = new_text.encode("utf-8-sig")
        prof.profile_sii.write_bytes(out_bytes)
        # 写入后读一次验证
        verify = self.get_active_mods(prof)
        if verify != list(new_mods):
            raise RuntimeError("写回后校验失败：active_mods 与预期不一致，可能被游戏重新加密或加密算法不匹配")
        return prof.profile_sii


def _decode_text(b: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")


def _run_sii_decrypt(exe: Path, in_path: Path) -> Optional[bytes]:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmpin = Path(td) / in_path.name
        shutil.copy2(in_path, tmpin)
        tmpout = tmpin.parent / (tmpin.stem + ".dec")
        subprocess.run([str(exe), str(tmpin), str(tmpout)],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=30, cwd=td, shell=False)
        if tmpout.exists():
            data = tmpout.read_bytes()
            if b"SiiNunit" in data:
                return data
        for cand in tmpin.parent.glob("*"):
            if cand.name == tmpin.name:
                continue
            data = cand.read_bytes()
            if b"SiiNunit" in data:
                return data
        d2 = tmpin.read_bytes()
        if b"SiiNunit" in d2:
            return d2
    return None
