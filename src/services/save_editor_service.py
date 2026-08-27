"""
ETS2 存档编辑器服务
====================
功能：
  3. 重命名 profile（profile_name / company_name）
  4. 复制 profile 设置（active_mods / controls 等）
  5. 修改金钱 / 经验 / 等级
  6. 解锁地图 / 车库 / 经销商
  7. 卡车维修 / 加油

技术要点：
  - profile.sii：ScsC/XXTEA 加密 → 解密为文本 SII → 文本编辑 → 重新加密
  - game.sii：ScsC 加密 → 解密为 BSII 二进制 → 字段搜索/修改 → 重新加密
  - ScsC 格式：AES-256-CBC + zlib（key 为社区公开的 32 字节常量）
  - BSII 格式：[u32 name_len][name][u8 type_byte][3 padding bytes 0x00 0x00 0x00]...
"""
from __future__ import annotations

import struct
import zlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Tuple, Dict

# 尝试导入 pycryptodome（ScsC 解密必需）
try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad, unpad
    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False

# 复用 profile_service 的文本编辑能力
from services.profile_service import (
    ProfileService, ProfileInfo, BackupService,
    decrypt_profile_bytes, encrypt_profile_bytes,
    _looks_encrypted, _decode_text, _escape_profile_str_for_sii,
    _unescape_profile_str, _run_sii_decrypt,
)


# =========================================================================
#  ScsC 加解密（game.sii 用的 AES-256-CBC + zlib 格式）
# =========================================================================

# 社区公开的 ScsC AES-256 key（由 SCS 二进制中提取）
_SCS_AES_KEY = bytes([
    0x2a, 0x5f, 0xcb, 0x17, 0x91, 0xd2, 0x2f, 0xb6,
    0x02, 0x45, 0xb3, 0xd8, 0x36, 0x9e, 0xd0, 0xb2,
    0xc2, 0x73, 0x71, 0x56, 0x3f, 0xbf, 0x1f, 0x3c,
    0x9e, 0xdf, 0x6b, 0x11, 0x82, 0x5a, 0x5d, 0x0a,
])

_MAGIC_SCSC = b"ScsC"


def decrypt_scsc(data: bytes) -> bytes:
    """解密 ScsC 格式：AES-256-CBC 解密 → zlib 解压。返回明文 BSII 二进制。"""
    if not data.startswith(_MAGIC_SCSC):
        return data  # 已经是明文
    if not _HAS_CRYPTO:
        raise RuntimeError("ScsC 解密需要 pycryptodome，请先 pip install pycryptodome")
    # 文件头结构：ScsC(4) + hash(32) + iv(16) + expected_size(4) + encrypted(...)
    iv = data[36:52]
    encrypted = data[56:]
    cipher = AES.new(_SCS_AES_KEY, AES.MODE_CBC, iv)
    decrypted = cipher.decrypt(encrypted)
    try:
        decrypted = unpad(decrypted, AES.block_size)
    except ValueError as e:
        # unpad 失败 = 解密后 padding 非法（密钥/IV 错误或文件损坏），
        # 继续 decompress 会抛 zlib.error 或返回垃圾数据，必须中止
        raise ValueError(f"ScsC AES unpad failed (corrupted data or wrong key): {e}") from e
    plaintext = zlib.decompress(decrypted)
    return plaintext


def encrypt_scsc(plaintext: bytes) -> bytes:
    """加密为 ScsC 格式：zlib 压缩 → AES-256-CBC 加密。"""
    if not _HAS_CRYPTO:
        raise RuntimeError("ScsC 加密需要 pycryptodome，请先 pip install pycryptodome")
    import os
    compressed = zlib.compress(plaintext, 9)
    iv = os.urandom(16)
    padded = pad(compressed, AES.block_size)
    cipher = AES.new(_SCS_AES_KEY, AES.MODE_CBC, iv)
    encrypted = cipher.encrypt(padded)
    # 文件头：ScsC(4) + zeros(32) + iv(16) + expected_size(4) + encrypted
    header = _MAGIC_SCSC + b"\x00" * 32 + iv + struct.pack("<I", len(plaintext)) + encrypted
    return header


# =========================================================================
#  BSII 二进制字段查找器
# =========================================================================

def _find_field_positions(data: bytes, field_name: str) -> List[int]:
    """在 BSII 二进制中查找所有 [u32 len][field_name] 模式的起始位置。"""
    name_bytes = field_name.encode("utf-8")
    target = struct.pack("<I", len(name_bytes)) + name_bytes
    positions: List[int] = []
    start = 0
    while True:
        pos = data.find(target, start)
        if pos == -1:
            break
        positions.append(pos)
        start = pos + 1
    return positions


def _read_field_type(data: bytes, field_pos: int, field_name: str) -> Optional[Tuple[int, int]]:
    """读取字段名后的 type byte。返回 (type_byte, type_byte_offset)。"""
    name_bytes = field_name.encode("utf-8")
    type_offset = field_pos + 4 + len(name_bytes)
    if type_offset >= len(data):
        return None
    return (data[type_offset], type_offset)


def _read_float32_at(data: bytes, offset: int) -> Optional[float]:
    if offset + 4 > len(data):
        return None
    return struct.unpack_from("<f", data, offset)[0]


def _write_float32_at(data: bytearray, offset: int, value: float) -> None:
    struct.pack_into("<f", data, offset, value)


def _read_u32_at(data: bytes, offset: int) -> Optional[int]:
    if offset + 4 > len(data):
        return None
    return struct.unpack_from("<I", data, offset)[0]


def _write_u32_at(data: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<I", data, offset, value & 0xFFFFFFFF)


# =========================================================================
#  SaveEditorService 主类
# =========================================================================

@dataclass
class SaveSlotInfo:
    """一个 save 槽位信息。

    性能缓存字段：
      _cached_bsii:  解密后的 BSII 明文（写操作后置 None 失效）
      _cached_magic: 原始文件前 4 字节 magic（避免 encrypt 时重复读盘）
      _field_index:  BSII 字段名索引 {name: [(field_pos, type_byte, type_offset)]}（首次扫描后缓存）
    """
    profile: ProfileInfo
    slot_name: str        # "1", "autosave", "autosave_drive", etc.
    slot_path: Path
    game_sii: Path
    info_sii: Path
    file_time: int = 0
    # 性能缓存（不参与 dataclass 默认 repr）
    _cached_bsii: Optional[bytes] = None
    _cached_magic: Optional[bytes] = None
    _field_index: Optional[Dict[str, List[Tuple[int, int, int]]]] = None

    def invalidate_cache(self) -> None:
        """写操作后调用，使解密缓存和字段索引失效。"""
        self._cached_bsii = None
        self._field_index = None
        # magic 不失效：文件格式不会因修改而改变


class SaveEditorService:
    """存档编辑器服务：解密、字段查找、修改、重新加密。"""

    T_BOOL = 0x02
    T_F32 = 0x05
    T_F64 = 0x06
    T_STRING = 0x27
    T_ARRAY = 0x28
    T_STRUCT = 0x39

    WEAR_FIELDS = [
        "engine_wear", "transmission_wear", "cabin_wear", "chassis_wear", "wheels_wear",
        "engine_wear_unfixable", "transmission_wear_unfixable",
        "cabin_wear_unfixable", "chassis_wear_unfixable", "wheels_wear_unfixable",
    ]

    FUEL_FIELDS = ["fuel", "current_fuel", "fuel_level", "total_fuel_litres"]

    def __init__(self, profile_service: ProfileService):
        self.ps = profile_service

    # ---------- 列出存档槽位 ----------

    def list_save_slots(self, prof: ProfileInfo) -> List[SaveSlotInfo]:
        """列出存档槽位。

        性能优化：直接用 game.sii 的文件 mtime 作为时间戳，
        跳过对每个 info.sii 的解密+正则解析（原实现是 O(n × decrypt)）。
        info_sii 路径仍保留，供需要详细信息的调用方按需使用。
        """
        slots: List[SaveSlotInfo] = []
        save_dir = prof.folder / "save"
        if not save_dir.exists():
            return slots
        for d in sorted(save_dir.iterdir()):
            if not d.is_dir():
                continue
            game_sii = d / "game.sii"
            info_sii = d / "info.sii"
            if not game_sii.exists():
                continue
            slot = SaveSlotInfo(
                profile=prof, slot_name=d.name, slot_path=d,
                game_sii=game_sii, info_sii=info_sii,
            )
            # 用文件 mtime 代替 info.sii 解析（避免每个槽位都解密一次）
            try:
                slot.file_time = int(game_sii.stat().st_mtime)
            except Exception:
                pass
            slots.append(slot)
        return slots

    # ---------- game.sii 解密 / 加密（带缓存）----------

    def decrypt_game_sii(self, slot_or_path) -> bytes:
        """解密 game.sii。

        性能优化：
          1. 当传入 SaveSlotInfo 时，解密结果缓存到 slot._cached_bsii，
             后续读取/修改复用，避免重复 AES 解密（原 read_current_money/xp/level
             会触发 3-4 次解密）。
          2. 同时缓存原始文件 magic（前 4 字节），供 encrypt_game_sii 复用。
          3. 兼容旧 API：直接传 Path 时走无缓存路径。
        """
        # 兼容：直接传 Path
        if isinstance(slot_or_path, Path):
            data = Path(slot_or_path).read_bytes()
            if data.startswith(_MAGIC_SCSC):
                return decrypt_scsc(data)
            if data.startswith(b"BSII"):
                return data
            if self.ps.sii_decrypt_exe and self.ps.sii_decrypt_exe.exists():
                out = _run_sii_decrypt(self.ps.sii_decrypt_exe, slot_or_path)
                if out is not None:
                    return out
            return data

        # 新路径：传 SaveSlotInfo，带缓存
        slot: SaveSlotInfo = slot_or_path
        if slot._cached_bsii is not None:
            return slot._cached_bsii

        data = Path(slot.game_sii).read_bytes()
        slot._cached_magic = data[:4]  # 缓存 magic 供 encrypt 复用

        if data.startswith(_MAGIC_SCSC):
            plaintext = decrypt_scsc(data)
        elif data.startswith(b"BSII"):
            plaintext = data
        elif self.ps.sii_decrypt_exe and self.ps.sii_decrypt_exe.exists():
            out = _run_sii_decrypt(self.ps.sii_decrypt_exe, slot.game_sii)
            plaintext = out if out is not None else data
        else:
            plaintext = data

        slot._cached_bsii = plaintext
        return plaintext

    def encrypt_game_sii(self, plaintext: bytes, slot_or_path) -> bytes:
        """加密 BSII 明文回 game.sii 格式。

        性能优化：复用 decrypt 阶段缓存的 magic，避免再次读盘判断格式。
        """
        # 兼容：直接传 Path
        if isinstance(slot_or_path, Path):
            orig = Path(slot_or_path).read_bytes()
            if orig.startswith(_MAGIC_SCSC):
                return encrypt_scsc(plaintext)
            return plaintext

        # 新路径：传 SaveSlotInfo，复用 magic
        slot: SaveSlotInfo = slot_or_path
        magic = slot._cached_magic
        if magic is None:
            # 兜底：缓存未命中时读前 4 字节
            try:
                magic = Path(slot.game_sii).read_bytes()[:4]
            except Exception:
                magic = b""
        if magic == _MAGIC_SCSC:
            return encrypt_scsc(plaintext)
        return plaintext

    # ---------- 通用字段查找/修改 ----------

    def find_float_field_value(self, bsii: bytes, field_name: str) -> List[Tuple[int, float]]:
        """查找指定名称字段后的 float32 值。"""
        results: List[Tuple[int, float]] = []
        for pos in _find_field_positions(bsii, field_name):
            type_info = _read_field_type(bsii, pos, field_name)
            if type_info is None:
                continue
            type_byte, type_offset = type_info
            # BSII 中 0x05 后跟 3 个 0x00 padding，尝试多个偏移
            for delta in (1, 4, 5, 8):
                val = _read_float32_at(bsii, type_offset + delta)
                if val is not None:
                    results.append((type_offset + delta, val))
        return results

    def find_u32_field_value(self, bsii: bytes, field_name: str) -> List[Tuple[int, int]]:
        results: List[Tuple[int, int]] = []
        for pos in _find_field_positions(bsii, field_name):
            type_info = _read_field_type(bsii, pos, field_name)
            if type_info is None:
                continue
            type_byte, type_offset = type_info
            for delta in (1, 4, 5, 8):
                val = _read_u32_at(bsii, type_offset + delta)
                if val is not None:
                    results.append((type_offset + delta, val))
        return results

    def replace_float_value(self, bsii: bytearray, value_offset: int, new_value: float) -> None:
        _write_float32_at(bsii, value_offset, new_value)

    def replace_u32_value(self, bsii: bytearray, value_offset: int, new_value: int) -> None:
        _write_u32_at(bsii, value_offset, new_value)

    # ---------- 功能 3：重命名 profile ----------

    def rename_profile(self, prof: ProfileInfo, new_profile_name: str = "",
                       new_company_name: str = "") -> Path:
        original_bytes = prof.profile_sii.read_bytes()
        was_encrypted = _looks_encrypted(original_bytes)
        plain = self.ps._get_plain_text(prof.profile_sii)

        if new_profile_name:
            escaped = _escape_profile_str_for_sii(new_profile_name)
            plain = self._replace_text_field(plain, "profile_name", escaped)

        if new_company_name:
            escaped = _escape_profile_str_for_sii(new_company_name)
            plain = self._replace_text_field(plain, "company_name", escaped)

        out_bytes = plain.encode("utf-8-sig")
        if was_encrypted:
            try:
                out_bytes = encrypt_profile_bytes(plain.encode("utf-8-sig"))
            except Exception as e:
                # 加密失败 = 数据写回后游戏无法识别，不能降级写明文
                # 让上层捕获提示用户，避免静默损坏存档
                raise RuntimeError(f"profile.sii 重新加密失败，已取消写入以防存档损坏: {e}") from e

        self.ps.backup.backup(prof.profile_sii, tag="pre-rename")
        prof.profile_sii.write_bytes(out_bytes)
        return prof.profile_sii

    @staticmethod
    def _replace_text_field(text: str, key: str, new_val: str) -> str:
        pat = re.compile(
            r'^(?P<indent>\s*)' + re.escape(key) + r'\s*:\s*"[^"]*"\s*$',
            re.MULTILINE
        )
        return pat.sub(lambda m: f'{m.group("indent")}{key}: "{new_val}"', text)

    # ---------- 功能 4：复制 profile 设置 ----------

    def copy_profile_settings(self, src: ProfileInfo, dst: ProfileInfo,
                               copy_active_mods: bool = True,
                               copy_controls: bool = False) -> None:
        if copy_active_mods:
            mods = self.ps.get_active_mods(src)
            self.ps.set_active_mods(dst, mods)

        if copy_controls:
            src_controls = src.folder / "controls.sii"
            dst_controls = dst.folder / "controls.sii"
            if src_controls.exists():
                if dst_controls.exists():
                    self.ps.backup.backup(dst_controls, tag="pre-copy-controls")
                shutil.copy2(src_controls, dst_controls)

    # ---------- 功能 5：修改金钱 / 经验 / 等级 ----------

    def set_player_money(self, slot: SaveSlotInfo, new_money: float,
                          current_money_hint: Optional[float] = None) -> bool:
        # 解密结果走 slot._cached_bsii 缓存（写时由 _save_game_sii 失效）
        bsii = bytearray(self.decrypt_game_sii(slot))
        modified = False

        # 策略1：用字段索引查找（一次扫描建立，多次复用）
        for fname in ("money", "money_account", "player_money", "bank_money", "account_balance"):
            hits = self.find_float_field_value(bsii, fname)
            for offset, val in hits:
                if val != val or abs(val) > 1e15:
                    continue
                if current_money_hint is not None:
                    if abs(val - current_money_hint) > max(1.0, abs(current_money_hint) * 0.01):
                        continue
                self.replace_float_value(bsii, offset, float(new_money))
                modified = True

        # 策略2：全文件搜索 float32(当前值) 并替换
        if not modified and current_money_hint is not None:
            target_bytes = struct.pack("<f", float(current_money_hint))
            start = 0
            while True:
                pos = bsii.find(target_bytes, start)
                if pos == -1:
                    break
                self.replace_float_value(bsii, pos, float(new_money))
                modified = True
                start = pos + 4

        if modified:
            self._save_game_sii(slot, bytes(bsii))
        return modified

    def set_player_experience(self, slot: SaveSlotInfo, new_xp: float,
                               current_xp_hint: Optional[float] = None) -> bool:
        bsii = bytearray(self.decrypt_game_sii(slot))
        modified = False

        hits = self.find_float_field_value(bsii, "experience_points")
        for offset, val in hits:
            if val != val or abs(val) > 1e12:
                continue
            if current_xp_hint is not None:
                if abs(val - current_xp_hint) > max(1.0, abs(current_xp_hint) * 0.01):
                    continue
            self.replace_float_value(bsii, offset, float(new_xp))
            modified = True

        if not modified and current_xp_hint is not None:
            target_bytes = struct.pack("<f", float(current_xp_hint))
            start = 0
            count = 0
            while count < 10:
                pos = bsii.find(target_bytes, start)
                if pos == -1:
                    break
                self.replace_float_value(bsii, pos, float(new_xp))
                modified = True
                start = pos + 4
                count += 1

        if modified:
            self._save_game_sii(slot, bytes(bsii))
        return modified

    def set_player_level(self, slot: SaveSlotInfo, new_level: int,
                          current_level_hint: Optional[int] = None) -> bool:
        bsii = bytearray(self.decrypt_game_sii(slot))
        modified = False

        hits = self.find_u32_field_value(bsii, "level")
        for offset, val in hits:
            if val > 1000:
                continue
            if current_level_hint is not None and val != current_level_hint:
                continue
            self.replace_u32_value(bsii, offset, int(new_level))
            modified = True

        if not modified and current_level_hint is not None:
            target_bytes = struct.pack("<I", int(current_level_hint))
            start = 0
            count = 0
            while count < 5:
                pos = bsii.find(target_bytes, start)
                if pos == -1:
                    break
                existing = _read_u32_at(bsii, pos)
                if existing is not None and existing < 1000:
                    self.replace_u32_value(bsii, pos, int(new_level))
                    modified = True
                start = pos + 4
                count += 1

        if modified:
            self._save_game_sii(slot, bytes(bsii))
        return modified

    # ---------- 功能 6：解锁地图 / 车库 / 经销商 ----------

    def unlock_all_dealers(self, slot: SaveSlotInfo) -> bool:
        bsii = bytearray(self.decrypt_game_sii(slot))
        modified = False

        for fname in ("unlocked_dealers", "unlocked_recruitments"):
            for pos in _find_field_positions(bsii, fname):
                type_info = _read_field_type(bsii, pos, fname)
                if type_info is None:
                    continue
                type_byte, type_offset = type_info
                for delta in (1, 4, 5):
                    if type_offset + delta + 4 <= len(bsii):
                        old = _read_u32_at(bsii, type_offset + delta)
                        if old is not None and old < 0x10000:
                            self.replace_u32_value(bsii, type_offset + delta, 1)
                            modified = True

        if modified:
            self._save_game_sii(slot, bytes(bsii))
        return modified

    def unlock_all_garages(self, slot: SaveSlotInfo) -> bool:
        bsii = bytearray(self.decrypt_game_sii(slot))
        modified = False

        for pos in _find_field_positions(bsii, "garages"):
            type_info = _read_field_type(bsii, pos, "garages")
            if type_info is None:
                continue
            type_byte, type_offset = type_info
            if type_byte == self.T_BOOL and type_offset + 1 < len(bsii):
                bsii[type_offset + 1] = 0x01
                modified = True

        if modified:
            self._save_game_sii(slot, bytes(bsii))
        return modified

    # ---------- 功能 7：卡车维修 / 加油 ----------

    def repair_truck(self, slot: SaveSlotInfo) -> int:
        """维修卡车：将所有磨损字段归零。

        性能优化：用 _build_field_index 一次扫描建立字段索引，
        避免对 10 个 wear 字段各做一次 O(n) 全文件扫描（10×O(n) → 1×O(n)）。
        """
        bsii = bytearray(self.decrypt_game_sii(slot))
        index = self._build_field_index(slot, bsii)
        count = 0

        for fname in self.WEAR_FIELDS:
            for field_pos, type_byte, type_offset in index.get(fname, []):
                # 保守策略：将 type_byte 后 8 字节全部清零
                for delta in range(1, 9):
                    if type_offset + delta < len(bsii):
                        bsii[type_offset + delta] = 0x00
                count += 1

        if count > 0:
            self._save_game_sii(slot, bytes(bsii))
        return count

    def refuel_truck(self, slot: SaveSlotInfo, fuel_amount: float = 100.0) -> int:
        """加油：将燃油字段设置为指定值。"""
        bsii = bytearray(self.decrypt_game_sii(slot))
        index = self._build_field_index(slot, bsii)
        count = 0

        for fname in self.FUEL_FIELDS:
            for field_pos, type_byte, type_offset in index.get(fname, []):
                for delta in (1, 4, 5):
                    if type_offset + delta + 4 <= len(bsii):
                        self.replace_float_value(bsii, type_offset + delta, fuel_amount)
                        count += 1

        if count > 0:
            self._save_game_sii(slot, bytes(bsii))
        return count

    # ---------- 内部：保存 game.sii ----------

    def _save_game_sii(self, slot: SaveSlotInfo, new_bsii: bytes) -> None:
        """加密写回 game.sii 并失效缓存。"""
        game_sii = slot.game_sii
        self.ps.backup.backup(game_sii, tag="pre-save-edit")
        encrypted = self.encrypt_game_sii(new_bsii, slot)
        game_sii.write_bytes(encrypted)
        # 写操作后使缓存失效，下次读取会重新解密
        slot.invalidate_cache()

    # ---------- BSII 字段索引（性能优化）----------

    def _build_field_index(self, slot: SaveSlotInfo, bsii: bytes) -> Dict[str, List[Tuple[int, int, int]]]:
        """一次扫描 BSII 建立字段名 → [(field_pos, type_byte, type_offset)] 索引。

        性能：原实现对每个目标字段各做一次 _find_field_positions（O(n)），
        repair_truck 调 10 个字段 → 10×O(n)。本方法一次扫描所有 [u32 len][name] 模式，
        建立全字段索引并缓存到 slot._field_index，后续复用，10×O(n) → 1×O(n)。

        仅索引目标字段名（WEAR_FIELDS、FUEL_FIELDS 等），避免索引全文件。
        """
        if slot._field_index is not None:
            return slot._field_index

        # 合并所有需要索引的目标字段名
        targets = set(self.WEAR_FIELDS) | set(self.FUEL_FIELDS) | {
            "money", "money_account", "player_money", "bank_money", "account_balance",
            "experience_points", "level",
            "unlocked_dealers", "unlocked_recruitments", "garages",
        }
        index: Dict[str, List[Tuple[int, int, int]]] = {}
        i = 0
        n = len(bsii)
        while i < n - 8:
            # 读 u32 名字长度
            name_len = struct.unpack_from("<I", bsii, i)[0]
            if 1 <= name_len <= 64 and i + 4 + name_len < n:
                try:
                    name = bsii[i + 4:i + 4 + name_len].decode("utf-8")
                except UnicodeDecodeError:
                    i += 1
                    continue
                if name in targets:
                    after = i + 4 + name_len
                    if after < n:
                        type_byte = bsii[after]
                        index.setdefault(name, []).append((i, type_byte, after))
            i += 1

        slot._field_index = index
        return index

    # ---------- 读取当前值（用于 UI 显示）----------

    def read_current_money(self, slot: SaveSlotInfo) -> Optional[float]:
        try:
            bsii = self.decrypt_game_sii(slot)
            for fname in ("money", "money_account", "player_money"):
                hits = self.find_float_field_value(bsii, fname)
                for offset, val in hits:
                    if val == val and 0 < abs(val) < 1e15:
                        return val
            return None
        except Exception:
            return None

    def read_current_xp(self, slot: SaveSlotInfo) -> Optional[float]:
        try:
            bsii = self.decrypt_game_sii(slot)
            hits = self.find_float_field_value(bsii, "experience_points")
            for offset, val in hits:
                if val == val and 0 <= val < 1e12:
                    return val
            return None
        except Exception:
            return None

    def read_current_level(self, slot: SaveSlotInfo) -> Optional[int]:
        """从经验值反推等级。ETS2 等级公式：每级所需 XP 递增。"""
        try:
            xp = self.read_current_xp(slot)
            if xp is None:
                return None
            # ETS2 等级公式（近似）：
            # Level 1: 0 XP, Level 2: ~1000 XP, Level 3: ~3000 XP, ...
            # 每级所需 = level * 1000 (累计)
            # xp_needed(N) = N * (N-1) * 500
            level = 1
            while level < 200:
                needed = level * (level - 1) * 500
                next_needed = (level + 1) * level * 500
                if needed <= xp < next_needed:
                    return level
                level += 1
            return level
        except Exception:
            return None

    @staticmethod
    def xp_for_level(level: int) -> float:
        """计算到达指定等级所需的总 XP。"""
        if level <= 1:
            return 0.0
        # xp_needed(N) = N * (N-1) * 500
        return float(level * (level - 1) * 500)
