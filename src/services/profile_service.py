from __future__ import annotations

import datetime as _dt
import os
import re
import shutil
import subprocess
import struct
import zipfile
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

def _escape_profile_str_for_sii(s: str) -> str:
    """将普通字符串编码为 SII 中可保存的格式（对非 ASCII 使用 \\xNN 编码）。"""
    if not s:
        return ""
    out = []
    for ch in s:
        code = ord(ch)
        if code < 128 and ch not in ('"', '\\'):
            out.append(ch)
        elif ch == '"':
            out.append('\\"')
        elif ch == '\\':
            out.append('\\\\')
        else:
            utf8_bytes = ch.encode('utf-8')
            for b in utf8_bytes:
                out.append(f"\\x{b:02X}")
    return ''.join(out)

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
    def list_profiles(self, quick: bool = False) -> List[ProfileInfo]:
        """列出所有 profile。

        性能优化：
          - quick=True：跳过解密+SII 解析，仅返回 ProfileInfo 骨架
            （display_name/company_name 留空，由调用方按需调 enrich_profile 异步填充）。
            原实现对每个 profile 都同步解密+解析，Profile 数量多时启动明显卡顿（O(n×decrypt)）。
          - quick=False（默认）：保持原行为，立即 _enrich。
        """
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
                if not quick:
                    self._enrich(info)
                out.append(info)
        return out

    def enrich_profile(self, info: ProfileInfo) -> bool:
        """异步填充单个 profile 的 display_name/company_name 等字段。

        供 list_profiles(quick=True) 后的异步任务调用，返回是否成功填充。
        也可在外部任何时候调用以补全字段（重复调用幂等）。
        """
        try:
            self._enrich(info)
            return True
        except Exception:
            return False

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
    def set_active_mods(self, prof: ProfileInfo, new_mods: List[str],
                         verify: bool = False) -> Path:
        """
        1. 读取 profile.sii 原始字节并解密成文本
        2. 文本级原位重写 active_mods 条目
        3. 若原文件为加密 → 重新加密写回；若明文 → 明文写回
        4. 写前备份
        返回实际写入的文件路径

        性能优化：verify=False（默认）跳过写入后的二次解密+解析校验，
        避免双倍开销（原实现每次写入都 get_active_mods 再读一次）。
        保留 verify=True 用于调试或关键路径。
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
        # 仅在显式请求时进行二次校验（避免双倍解密开销）
        if verify:
            check = self.get_active_mods(prof)
            if check != list(new_mods):
                raise RuntimeError("写回后校验失败：active_mods 与预期不一致，可能被游戏重新加密或加密算法不匹配")
        return prof.profile_sii

    # ---------- 复制存档 ----------
    def copy_profile(self, prof: ProfileInfo, new_display_name: str = "", new_company_name: str = "") -> ProfileInfo:
        """复制存档到同一位置（local/steam/cloud）。"""
        # 1. 根据 prof.location 定位目标父目录
        if prof.location == "local":
            parent = self.paths.profiles_dir
        elif prof.location == "steam":
            parent = self.paths.steam_profiles_dir
        elif prof.location == "cloud":
            parent = self.paths.steam_cloud_dir
        else:
            raise ValueError(f"未知 location: {prof.location}")

        if parent is None or not parent.exists():
            raise RuntimeError(f"目标父目录不存在: {parent}")

        # 2. 生成新的 profile_id：原id后加 "_copy1"，若已存在则 "_copy2"……直到不冲突
        original_id = prof.profile_id
        base_id = original_id[:32] if len(original_id) > 32 else original_id
        counter = 1
        while True:
            suffix = f"_copy{counter}"
            avail = 32 - len(suffix)
            if avail < 1:
                avail = 1
            new_id = base_id[:avail] + suffix
            if not (parent / new_id).exists():
                break
            counter += 1

        # 复制之前先备份原 profile 文件夹（tag="pre-copy"）
        try:
            if prof.folder.exists():
                ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
                backups_dir = prof.folder.parent / ".profile_backups"
                backups_dir.mkdir(parents=True, exist_ok=True)
                zip_path = backups_dir / f"{prof.profile_id}_{ts}_pre-copy.zip"
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    for f in prof.folder.rglob("*"):
                        if f.is_file():
                            try:
                                zf.write(f, str(f.relative_to(prof.folder.parent)))
                            except Exception:
                                pass
        except Exception:
            pass

        # 3. 整个 prof.folder 目录用 shutil.copytree 复制到父目录下的新id文件夹
        new_folder = parent / new_id
        if new_folder.exists():
            shutil.rmtree(new_folder)
        shutil.copytree(prof.folder, new_folder)

        # 4. 读取新目录下的 profile.sii 文本（解密后），在 unit 中更新
        new_sii_in_new_folder = new_folder / "profile.sii"
        new_sii_target = new_sii_in_new_folder

        # 如果是 steam 指针目录，真实 profile.sii 在 cloud 位置
        if not new_sii_in_new_folder.exists():
            if prof.location == "steam" and self.paths.steam_cloud_dir is not None:
                orig_cloud_dir = prof.profile_sii.parent
                try:
                    same_id = orig_cloud_dir.name == prof.profile_id
                except Exception:
                    same_id = str(orig_cloud_dir).endswith(str(prof.profile_id))
                if same_id:
                    new_cloud_dir = self.paths.steam_cloud_dir / new_id
                    if new_cloud_dir.exists():
                        shutil.rmtree(new_cloud_dir)
                    shutil.copytree(orig_cloud_dir, new_cloud_dir)
                    new_sii_target = new_cloud_dir / "profile.sii"
                else:
                    candidate = self.paths.steam_cloud_dir / new_id / "profile.sii"
                    if candidate.exists():
                        new_sii_target = candidate

        if not new_sii_target.exists():
            raise RuntimeError(f"复制后找不到 profile.sii: {new_sii_target}")

        original_bytes = new_sii_target.read_bytes()
        was_encrypted = _looks_encrypted(original_bytes)
        plain = self._get_plain_text(new_sii_target)

        # 文本级替换 profile_name 和 company_name
        new_profile_name = new_display_name if new_display_name else ((prof.display_name or prof.profile_id) + " 副本")
        new_company_name_val = new_company_name if new_company_name else ((prof.company_name or new_profile_name) + " 副本")

        escaped_pn = _escape_profile_str_for_sii(new_profile_name)
        escaped_cn = _escape_profile_str_for_sii(new_company_name_val)

        import re as _re_local

        def _replace_kv(text, key, new_val):
            pat = _re_local.compile(
                r'^(?P<indent>\s*)' + _re_local.escape(key) + r'\s*:\s*"(?P<val>.*)"\s*$',
                _re_local.MULTILINE
            )
            def repl(m):
                return f'{m.group("indent")}{key}: "{new_val}"'
            return pat.sub(repl, text)

        new_text = _replace_kv(plain, "profile_name", escaped_pn)
        new_text = _replace_kv(new_text, "company_name", escaped_cn)

        # 保存时注意原文件加密状态
        out_bytes = new_text.encode("utf-8-sig")
        if was_encrypted:
            try:
                out_bytes = encrypt_profile_bytes(new_text.encode("utf-8-sig"))
            except Exception:
                out_bytes = new_text.encode("utf-8-sig")
        new_sii_target.write_bytes(out_bytes)

        # 5. 返回新的 ProfileInfo（自动调用 _enrich 补齐）
        new_info = ProfileInfo(
            profile_id=new_id,
            location=prof.location,
            folder=new_folder,
            profile_sii=new_sii_target,
        )
        self._enrich(new_info)
        return new_info

    # ---------- 删除存档 ----------
    def delete_profile(self, prof: ProfileInfo, backup_first: bool = True) -> None:
        """删除存档（含备份）"""
        # 1. 若 backup_first=True：先把整个 prof.folder 目录打 zip 备份到 BackupService 的备份目录
        if backup_first:
            try:
                ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
                backups_dir = prof.folder.parent / ".profile_backups"
                backups_dir.mkdir(parents=True, exist_ok=True)
                zip_name = f"{prof.profile_id}_{ts}_deleted.zip"
                zip_path = backups_dir / zip_name
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    if prof.folder.exists():
                        for f in prof.folder.rglob("*"):
                            if f.is_file():
                                try:
                                    zf.write(f, str(f.relative_to(prof.folder.parent)))
                                except Exception:
                                    pass
                    # 同时把真实 profile.sii 所在目录也打包进 zip（如果在其他位置）
                    sii_parent = prof.profile_sii.parent
                    if sii_parent != prof.folder and sii_parent.exists():
                        for f in sii_parent.rglob("*"):
                            if f.is_file():
                                try:
                                    arc = f"_cloud_sii_{sii_parent.name}/" + str(f.relative_to(sii_parent))
                                    zf.write(f, arc)
                                except Exception:
                                    pass
            except Exception:
                pass

        # 2. shutil.rmtree(prof.folder) 删除目录
        if prof.folder.exists():
            shutil.rmtree(prof.folder)

        # 3. 如果 prof.profile_sii 不在 prof.folder 下，也删除 profile_sii 所在的那个目录（仅当该目录的父级是 steam_cloud_dir 时）
        sii_parent = prof.profile_sii.parent
        try:
            sii_in_folder = prof.folder in prof.profile_sii.parents
        except Exception:
            sii_in_folder = str(prof.folder) in str(prof.profile_sii)
        if not sii_in_folder and sii_parent.exists():
            if self.paths.steam_cloud_dir is not None:
                try:
                    is_cloud_child = sii_parent.parent == self.paths.steam_cloud_dir
                except Exception:
                    is_cloud_child = str(sii_parent.parent) == str(self.paths.steam_cloud_dir)
                if is_cloud_child:
                    shutil.rmtree(sii_parent)

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
        result = subprocess.run([str(exe), str(tmpin), str(tmpout)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=30, cwd=td, shell=False)
        # 必须同时满足：returncode=0、输出文件存在、文件非空
        if result.returncode != 0:
            return None
        if not tmpout.exists():
            return None
        try:
            data = tmpout.read_bytes()
        except OSError:
            return None
        if not data:
            return None
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
