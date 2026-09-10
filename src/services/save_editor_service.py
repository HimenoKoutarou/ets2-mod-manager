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
  - profile.sii：ScsC/XXTEA 加密 → 解密为文本 SII → 文本编辑 → 写回 ETS2 可读 SII
  - game.sii：ScsC 加密 → 解密为 BSII 二进制 → 字段搜索/修改 → 重新加密
  - ScsC 格式：AES-256-CBC + zlib（key 为社区公开的 32 字节常量）
  - BSII 格式：[u32 name_len][name][u8 type_byte][3 padding bytes 0x00 0x00 0x00]...
"""
from __future__ import annotations

import struct
import zlib
import re
import shutil
import os
import tempfile
import time
import uuid
import math
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
    profile_plaintext_bytes,
)
from application.profile_use_cases import require_game_closed
from infrastructure.process.game_state import WindowsGameState
from domain.bsii import BSIIFile, BSIIFieldValue, BSIIParseError, parse_bsii


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


def _combine_unlock_results(dealers_ok: bool, garages_ok: bool) -> tuple[bool, str]:
    """Return a truthful result for the combined dealer/garage operation."""
    return bool(dealers_ok and garages_ok), ""


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


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Atomically replace *path* with *data* using a sibling temporary file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _looks_like_schema_continuation(data: bytes, offset: int) -> bool:
    """Return True when *offset* starts the next BSII field declaration."""
    if offset + 5 > len(data):
        return False
    name_len = struct.unpack_from("<I", data, offset)[0]
    if not 1 <= name_len <= 96 or offset + 4 + name_len > len(data):
        return False
    raw_name = data[offset + 4:offset + 4 + name_len]
    try:
        name = raw_name.decode("ascii")
    except UnicodeDecodeError:
        return False
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_\.]*", name))


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
    display_name: str = ""
    # 性能缓存（不参与 dataclass 默认 repr）
    _cached_bsii: Optional[bytes] = None
    _cached_magic: Optional[bytes] = None
    _field_index: Optional[Dict[str, List[Tuple[int, int, int]]]] = None
    _cached_document: Optional[BSIIFile] = None

    def invalidate_cache(self) -> None:
        """写操作后调用，使解密缓存和字段索引失效。"""
        self._cached_bsii = None
        self._field_index = None
        self._cached_document = None
        # magic 不失效：文件格式不会因修改而改变


class SaveEditorService:
    """存档编辑器服务：解密、字段查找、修改、重新加密。"""

    T_BOOL = 0x02
    T_F32 = 0x05
    T_F64 = 0x06
    T_U32 = 0x27
    T_STRING = T_U32  # compatibility alias retained for older callers
    T_ARRAY = 0x28
    T_I64 = 0x31
    T_STRUCT = 0x39

    WEAR_FIELDS = [
        "engine_wear", "transmission_wear", "cabin_wear", "chassis_wear", "wheels_wear",
        "engine_wear_unfixable", "transmission_wear_unfixable",
        "cabin_wear_unfixable", "chassis_wear_unfixable", "wheels_wear_unfixable",
    ]

    FUEL_FIELDS = ["fuel", "current_fuel", "fuel_level", "total_fuel_litres"]

    def __init__(self, profile_service: ProfileService, game_state=None):
        self.ps = profile_service
        self.game_state = game_state or getattr(
            profile_service, "game_state", None
        ) or WindowsGameState()

    def is_game_running(self) -> bool:
        """Compatibility wrapper around the injectable game-state adapter."""
        return bool(self.game_state.is_running())

    def ensure_game_closed(self, action: str = "修改存档") -> None:
        require_game_closed(self.game_state, action)

    # ---------- 列出存档槽位 ----------

    def list_save_slots(self, prof: ProfileInfo) -> List[SaveSlotInfo]:
        """列出存档槽位。

        性能优化：直接用 game.sii 的文件 mtime 作为时间戳，
        只对 info.sii 读取存档显示名。Steam/Cloud Profile 不参与存档
        列表，避免把远程副本混进本地存档编辑器。
        """
        slots: List[SaveSlotInfo] = []
        if getattr(prof, "location", "") != "local":
            return slots
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
            slot.display_name = self._read_save_display_name(info_sii, d.name)
            slots.append(slot)
        def autosave_rank(slot: SaveSlotInfo) -> int:
            name = slot.slot_name.casefold()
            if name == "autosave":
                return 1
            if name.startswith("autosave"):
                return 2
            return 0
        return sorted(
            slots,
            key=lambda slot: (
                autosave_rank(slot),
                (slot.display_name or slot.slot_name).casefold(),
                slot.slot_name.casefold(),
            ),
        )

    @staticmethod
    def _read_save_display_name(info_sii: Path, fallback: str) -> str:
        """Read the user-facing save name from info.sii without mutating it."""
        if not info_sii.is_file():
            return fallback
        try:
            text = _decode_text(decrypt_scsc(info_sii.read_bytes()))
            match = re.search(
                r'(?m)^\s*name\s*:\s*"(?P<value>(?:\\.|[^"\\])*)"\s*$',
                text,
            )
            if match:
                value = _unescape_profile_str(match.group("value")).strip()
                if value:
                    return value
        except Exception:
            pass
        return fallback

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
        """查找可安全确认的内联 float32 值；结构定义区不做猜测。"""
        results: List[Tuple[int, float]] = []
        for pos in _find_field_positions(bsii, field_name):
            type_info = _read_field_type(bsii, pos, field_name)
            if type_info is None:
                continue
            type_byte, type_offset = type_info
            if type_byte != self.T_F32 or bsii[type_offset + 1:type_offset + 4] != b"\x00\x00\x00":
                continue
            value_offset = type_offset + 4
            if _looks_like_schema_continuation(bsii, value_offset):
                continue
            val = _read_float32_at(bsii, value_offset)
            if val is not None:
                results.append((value_offset, val))
        return results

    def find_u32_field_value(self, bsii: bytes, field_name: str) -> List[Tuple[int, int]]:
        """Find UInt32 values through the BSII schema; reject non-BSII input."""
        try:
            document = parse_bsii(bytes(bsii))
        except BSIIParseError:
            return []
        return [
            (value.offset, int(value.value))
            for _, value in document.find_fields(field_name)
            if value.type_id == self.T_U32 and value.size == 4 and isinstance(value.value, int)
        ]

    def replace_float_value(self, bsii: bytearray, value_offset: int, new_value: float) -> None:
        _write_float32_at(bsii, value_offset, new_value)

    def replace_u32_value(self, bsii: bytearray, value_offset: int, new_value: int) -> None:
        _write_u32_at(bsii, value_offset, new_value)

    @staticmethod
    def _write_i64_value(bsii: bytearray, value_offset: int, new_value: int) -> None:
        if value_offset < 0 or value_offset + 8 > len(bsii):
            raise ValueError("BSII Int64 字段越界")
        struct.pack_into("<q", bsii, value_offset, int(new_value))

    def _parse_bsii_document(self, slot: SaveSlotInfo, bsii: Optional[bytes] = None) -> BSIIFile:
        """Parse a slot's BSII once and reuse the immutable document cache."""
        data = bytes(bsii) if bsii is not None else self.decrypt_game_sii(slot)
        if slot._cached_document is not None and slot._cached_bsii == data:
            return slot._cached_document
        document = parse_bsii(data)
        if slot._cached_bsii == data:
            slot._cached_document = document
        return document

    @staticmethod
    def _single_typed_field(
        document: BSIIFile,
        field_name: str,
        structure_name: str,
        type_id: int,
    ) -> Optional[BSIIFieldValue]:
        matches = document.find_fields(field_name, structure_names=[structure_name])
        if len(matches) != 1:
            return None
        value = matches[0][1]
        return value if value.type_id == type_id else None

    # ---------- 功能 3：重命名 profile ----------

    def rename_profile(self, prof: ProfileInfo, new_profile_name: str = "",
                       new_company_name: str = "") -> Path:
        self.ps.ensure_local_profile(prof)
        self.ensure_game_closed("重命名 Profile")
        plain = self.ps._get_plain_text(prof.profile_sii)

        if new_profile_name:
            escaped = _escape_profile_str_for_sii(new_profile_name)
            plain = self._replace_text_field(plain, "profile_name", escaped)

        if new_company_name:
            escaped = _escape_profile_str_for_sii(new_company_name)
            plain = self._replace_text_field(plain, "company_name", escaped)

        out_bytes = profile_plaintext_bytes(plain)

        self.ps.backup.backup(prof.profile_sii, tag="pre-rename")
        _atomic_write_bytes(prof.profile_sii, out_bytes)
        return prof.profile_sii

    @staticmethod
    def _replace_text_field(text: str, key: str, new_val: str) -> str:
        pat = re.compile(
            r'^(?P<indent>[ \t]*)' + re.escape(key)
            + r'[ \t]*:[ \t]*"(?:\\.|[^"\\])*"[ \t]*$',
            re.MULTILINE
        )
        return pat.sub(lambda m: f'{m.group("indent")}{key}: "{new_val}"', text)

    # ---------- 功能 4：复制 profile 设置 ----------

    def copy_profile_settings(self, src: ProfileInfo, dst: ProfileInfo,
                               copy_active_mods: bool = True,
                               copy_controls: bool = False) -> None:
        # Reading a Cloud source is allowed; the destination must always be a
        # local profile because this operation writes active_mods/controls.
        self.ps.ensure_local_profile(dst)
        self.ensure_game_closed("复制 Profile 设置")
        source_mods = self.ps.get_active_mods(src) if copy_active_mods else None
        previous_mods = self.ps.get_active_mods(dst) if copy_active_mods else None
        src_controls = src.folder / "controls.sii"
        dst_controls = dst.folder / "controls.sii"
        copy_controls_now = bool(copy_controls and src_controls.is_file())
        previous_controls_exists = dst_controls.is_file()
        previous_controls = dst_controls.read_bytes() if previous_controls_exists else None

        try:
            # Apply controls first so a Profile write failure can still restore
            # the original controls file before the exception leaves the UI.
            if copy_controls_now:
                if previous_controls_exists:
                    self.ps.backup.backup(dst_controls, tag="pre-copy-controls")
                _atomic_write_bytes(dst_controls, src_controls.read_bytes())
            if copy_active_mods:
                self.ps.set_active_mods(dst, source_mods, verify=True)
        except Exception as error:
            rollback_errors = []
            if copy_active_mods and previous_mods is not None:
                try:
                    self.ps.set_active_mods(dst, previous_mods, verify=True)
                except Exception as rollback_error:
                    rollback_errors.append(f"active_mods rollback: {rollback_error}")
            if copy_controls_now:
                try:
                    if previous_controls_exists and previous_controls is not None:
                        _atomic_write_bytes(dst_controls, previous_controls)
                    else:
                        dst_controls.unlink(missing_ok=True)
                except Exception as rollback_error:
                    rollback_errors.append(f"controls rollback: {rollback_error}")
            if rollback_errors:
                raise RuntimeError(
                    f"复制 Profile 设置失败：{error}；回滚失败：{'; '.join(rollback_errors)}"
                ) from error
            raise

    def copy_save_slot(self, slot: SaveSlotInfo, new_display_name: str) -> SaveSlotInfo:
        """Copy one local game save into a new numbered slot.

        The source is never changed. The copy is prepared in a sibling staging
        directory and moved into place only after its ``info.sii`` has been
        decoded and updated successfully.
        """
        self.ps.ensure_local_profile(slot.profile)
        self.ensure_game_closed("复制存档")

        display_name = str(new_display_name or "").strip()
        if not display_name:
            raise ValueError("新存档名称不能为空。")

        save_dir = (slot.profile.folder / "save").resolve()
        source_dir = Path(slot.slot_path).resolve()
        if source_dir.parent != save_dir:
            raise ValueError("源存档槽位不在当前本地 Profile 的 save 目录中。")
        if not source_dir.is_dir():
            raise FileNotFoundError(f"源存档目录不存在：{source_dir}")

        source_game = source_dir / "game.sii"
        source_info = source_dir / "info.sii"
        if not source_game.is_file() or not source_info.is_file():
            raise FileNotFoundError("源存档缺少 game.sii 或 info.sii，无法复制。")

        numeric_slots = [
            int(path.name)
            for path in save_dir.iterdir()
            if path.is_dir() and path.name.isdigit()
        ]
        next_number = max(numeric_slots, default=0) + 1
        target_dir = save_dir / str(next_number)
        while target_dir.exists():
            next_number += 1
            target_dir = save_dir / str(next_number)

        staging_dir = save_dir / f".{next_number}_copy_{uuid.uuid4().hex}"
        try:
            shutil.copytree(source_dir, staging_dir, copy_function=shutil.copy2)
            copied_info = staging_dir / "info.sii"
            raw_info = copied_info.read_bytes()
            plain_info = decrypt_scsc(raw_info)
            if b"SiiNunit" not in plain_info[:256] or b"save_container" not in plain_info:
                raise ValueError("无法解析源存档 info.sii，已取消创建新存档。")

            info_text = _decode_text(plain_info)
            container_pos = info_text.find("save_container")
            info_prefix = info_text[:container_pos]
            container_text = info_text[container_pos:]
            escaped_name = _escape_profile_str_for_sii(display_name)
            container_text, name_count = re.subn(
                r'^(?P<indent>\s*)name\s*:\s*"(?:\\.|[^"\\])*"\s*$',
                lambda match: f'{match.group("indent")}name: "{escaped_name}"',
                container_text,
                count=1,
                flags=re.MULTILINE,
            )
            if name_count != 1:
                raise ValueError("info.sii 中没有可修改的存档名称字段。")

            now = int(time.time())
            container_text, file_time_count = re.subn(
                r'^(?P<indent>\s*)file_time\s*:\s*-?\d+\s*$',
                lambda match: f'{match.group("indent")}file_time: {now}',
                container_text,
                count=1,
                flags=re.MULTILINE,
            )
            if file_time_count != 1:
                raise ValueError("info.sii 中没有可修改的 file_time 字段。")

            info_bytes = (info_prefix + container_text).encode("utf-8")
            if info_bytes.startswith(b"\xef\xbb\xbf"):
                info_bytes = info_bytes[3:]
            # Preserve the source container format. Real ETS2 saves use ScsC
            # for info.sii; writing decrypted text here makes the copied slot
            # unreadable even though the metadata itself is valid.
            output_info = (
                encrypt_scsc(info_bytes)
                if raw_info.startswith(_MAGIC_SCSC)
                else info_bytes
            )
            _atomic_write_bytes(copied_info, output_info)
            os.replace(staging_dir, target_dir)
        except Exception:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise

        copied_game = target_dir / "game.sii"
        copied_info = target_dir / "info.sii"
        return SaveSlotInfo(
            profile=slot.profile,
            slot_name=target_dir.name,
            slot_path=target_dir,
            game_sii=copied_game,
            info_sii=copied_info,
            file_time=now,
            display_name=display_name,
        )

    # ---------- 功能 5：修改金钱 / 经验 / 等级 ----------

    def set_player_money(self, slot: SaveSlotInfo, new_money: float,
                          current_money_hint: Optional[float] = None) -> bool:
        """Set the canonical bank balance stored as a signed Int64."""
        try:
            requested = float(new_money)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(requested):
            return False
        target = int(round(requested))
        if not -(1 << 63) <= target <= (1 << 63) - 1:
            return False

        bsii = bytearray(self.decrypt_game_sii(slot))
        try:
            document = self._parse_bsii_document(slot, bsii)
        except BSIIParseError:
            return False
        field = self._single_typed_field(document, "money_account", "bank", self.T_I64)
        if field is None or field.size != 8 or not isinstance(field.value, int):
            return False
        current = int(field.value)
        if current_money_hint is not None:
            try:
                hint = float(current_money_hint)
            except (TypeError, ValueError):
                return False
            if not math.isfinite(hint):
                return False
            if abs(current - hint) > max(1.0, abs(hint) * 0.01):
                return False
        if current == target:
            return False
        self._write_i64_value(bsii, field.offset, target)
        self._save_game_sii(slot, bytes(bsii))
        return True

    def set_player_experience(self, slot: SaveSlotInfo, new_xp: float,
                               current_xp_hint: Optional[float] = None) -> bool:
        """Set the canonical economy experience value stored as UInt32."""
        try:
            requested = float(new_xp)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(requested):
            return False
        target = int(round(requested))
        if not 0 <= target <= (1 << 32) - 1:
            return False

        bsii = bytearray(self.decrypt_game_sii(slot))
        try:
            document = self._parse_bsii_document(slot, bsii)
        except BSIIParseError:
            return False
        field = self._single_typed_field(document, "experience_points", "economy", self.T_U32)
        if field is None or field.size != 4 or not isinstance(field.value, int):
            return False
        current = int(field.value)
        if current_xp_hint is not None:
            try:
                hint = float(current_xp_hint)
            except (TypeError, ValueError):
                return False
            if not math.isfinite(hint):
                return False
            if abs(current - hint) > max(1.0, abs(hint) * 0.01):
                return False
        if current == target:
            return False
        self.replace_u32_value(bsii, field.offset, target)
        self._save_game_sii(slot, bytes(bsii))
        return True

    def set_player_level(self, slot: SaveSlotInfo, new_level: int,
                          current_level_hint: Optional[int] = None) -> bool:
        """Update the derived level by writing its canonical XP value.

        Current BSII saves do not persist a standalone ``level`` field.  The
        game derives it from ``economy.experience_points``.
        """
        try:
            target_level = int(new_level)
        except (TypeError, ValueError):
            return False
        if not 1 <= target_level <= 200:
            return False

        bsii = bytearray(self.decrypt_game_sii(slot))
        try:
            document = self._parse_bsii_document(slot, bsii)
        except BSIIParseError:
            return False
        field = self._single_typed_field(document, "experience_points", "economy", self.T_U32)
        if field is None or field.size != 4 or not isinstance(field.value, int):
            return False
        current_level = self._level_from_xp(int(field.value))
        if current_level_hint is not None:
            try:
                if current_level != int(current_level_hint):
                    return False
            except (TypeError, ValueError):
                return False
        target_xp = int(round(self.xp_for_level(target_level)))
        if int(field.value) == target_xp:
            return False
        self.replace_u32_value(bsii, field.offset, target_xp)
        self._save_game_sii(slot, bytes(bsii))
        return True

    # ---------- 功能 6：解锁地图 / 车库 / 经销商 ----------

    def unlock_all_dealers(self, slot: SaveSlotInfo) -> bool:
        # These are container fields in current BSII saves. Editing bytes beside
        # their schema names corrupts the field table, so keep this disabled until
        # a real BSII object/value parser is available.
        return False

    def unlock_all_garages(self, slot: SaveSlotInfo) -> bool:
        bsii = bytearray(self.decrypt_game_sii(slot))
        modified = False

        for pos in _find_field_positions(bsii, "garages"):
            type_info = _read_field_type(bsii, pos, "garages")
            if type_info is None:
                continue
            type_byte, type_offset = type_info
            value_offset = type_offset + 4
            if (type_byte == self.T_BOOL
                    and bsii[type_offset + 1:type_offset + 4] == b"\x00\x00\x00"
                    and value_offset < len(bsii)
                    and not _looks_like_schema_continuation(bsii, value_offset)):
                bsii[value_offset] = 0x01
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
                if type_byte != self.T_F32 or bsii[type_offset + 1:type_offset + 4] != b"\x00\x00\x00":
                    continue
                value_offset = type_offset + 4
                if _looks_like_schema_continuation(bsii, value_offset):
                    continue
                self.replace_float_value(bsii, value_offset, 0.0)
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
                if type_byte != self.T_F32 or bsii[type_offset + 1:type_offset + 4] != b"\x00\x00\x00":
                    continue
                value_offset = type_offset + 4
                if _looks_like_schema_continuation(bsii, value_offset):
                    continue
                self.replace_float_value(bsii, value_offset, fuel_amount)
                count += 1

        if count > 0:
            self._save_game_sii(slot, bytes(bsii))
        return count

    # ---------- 内部：保存 game.sii ----------

    def _save_game_sii(self, slot: SaveSlotInfo, new_bsii: bytes) -> None:
        """加密写回 game.sii 并失效缓存。"""
        self.ps.ensure_local_profile(slot.profile)
        self.ensure_game_closed("修改存档")
        game_sii = slot.game_sii
        self.ps.backup.backup(game_sii, tag="pre-save-edit")
        encrypted = self.encrypt_game_sii(new_bsii, slot)
        _atomic_write_bytes(game_sii, encrypted)
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
            document = self._parse_bsii_document(slot, bsii)
            field = self._single_typed_field(document, "money_account", "bank", self.T_I64)
            if field is None or field.size != 8:
                return None
            return float(field.value)
        except Exception:
            return None

    def read_current_xp(self, slot: SaveSlotInfo) -> Optional[float]:
        try:
            bsii = self.decrypt_game_sii(slot)
            document = self._parse_bsii_document(slot, bsii)
            field = self._single_typed_field(document, "experience_points", "economy", self.T_U32)
            if field is None or field.size != 4:
                return None
            return float(field.value)
        except Exception:
            return None

    def read_current_level(self, slot: SaveSlotInfo) -> Optional[int]:
        """从经验值反推等级。ETS2 等级公式：每级所需 XP 递增。"""
        try:
            xp = self.read_current_xp(slot)
            if xp is None:
                return None
            return self._level_from_xp(int(xp))
        except Exception:
            return None

    @staticmethod
    def _level_from_xp(xp: int) -> int:
        xp = max(0, int(xp))
        level = 1
        while level < 200:
            needed = level * (level - 1) * 500
            next_needed = (level + 1) * level * 500
            if needed <= xp < next_needed:
                return level
            level += 1
        return level

    @staticmethod
    def xp_for_level(level: int) -> float:
        """计算到达指定等级所需的总 XP。"""
        if level <= 1:
            return 0.0
        # xp_needed(N) = N * (N-1) * 500
        return float(level * (level - 1) * 500)
