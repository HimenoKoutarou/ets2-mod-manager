from __future__ import annotations

import io
import os
import zipfile
from dataclasses import dataclass
from typing import Optional, List, Tuple
from pathlib import Path
from .sii_parser import parse_sii, SiiUnit
from .models import ModManifest, ModIcon


"""
SCS Archive 读取器
ETS2/ATS 的 .scs 文件其实就是标准 zip（压缩算法可选 store/deflate），
只是扩展名被改了。.zip 扩展名也同样被游戏识别。
另外模组还可以是普通目录（开发模式）。
"""


class ScsArchiveReader:
    """通用的 SCS 包读取：统一处理 .scs / .zip / 目录"""

    @classmethod
    def open(cls, package_path):
        """兼容别名为直接实例化。"""
        return cls(package_path)

    def __init__(self, package_path: str | os.PathLike):
        self.path = Path(package_path)
        self._zf: Optional[zipfile.ZipFile] = None
        self._mode: str = ""
        self._external_cache: Optional[dict] = None  # external 模式预提取的文件缓存
        self._open_mode()

    def _detect_magic(self) -> str:
        """读取前 4 字节判断格式：scs_hashfs / aem / zip_encrypted / zip / unknown"""
        try:
            with open(self.path, "rb") as f:
                head = f.read(4)
        except OSError:
            return "unknown"
        if head == b"SCS#":
            return "scs_hashfs"
        if head == b"AEM!":
            return "aem"
        if head[:2] == b"PK":
            return "zip_encrypted" if self._is_zip_encrypted() else "zip"
        return "unknown"

    def _is_zip_encrypted(self) -> bool:
        try:
            with zipfile.ZipFile(self.path) as z:
                return any(info.flag_bits & 0x1 for info in z.infolist())
        except Exception:
            return False

    def _open_mode(self):
        p = self.path
        if not p.exists():
            raise FileNotFoundError(p)
        if p.is_dir():
            self._mode = "dir"
            return
        # 先按 magic 分流：SCS# (HashFS) / AEM! / 加密 ZIP 走外部解包工具
        magic = self._detect_magic()
        if magic in ("scs_hashfs", "aem", "zip_encrypted"):
            self._mode = "external"
            return
        # 否则按后缀走标准 zipfile
        if p.suffix.lower() in (".scs", ".zip"):
            # 容错：有些 mod 可能是坏包，先试标准 zip
            try:
                self._zf = zipfile.ZipFile(p, "r")
                self._mode = "zip"
            except zipfile.BadZipFile:
                # zipfile 打不开 —— 可能是 SXC/ModGuard 加密、header 损坏等。
                # 交给外部解包工具尝试（sxc64.exe 能处理部分此类包），失败则无 manifest。
                self._mode = "external"
        else:
            self._mode = "unknown"


    # ---------- 别名：兼容旧 API 命名 ----------
    def extract_file_bytes(self, inner_path: str) -> Optional[bytes]:
        """兼容别名为 read_bytes。"""
        return self.read_bytes(inner_path)

    def extract_text(self, inner_path: str) -> Optional[str]:
        return self.read_text(inner_path)

    def close(self):
        if self._zf is not None:
            try:
                self._zf.close()
            except Exception:
                pass
            self._zf = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    # ---------- 底层文件读取 ----------
    def exists(self, inner_path: str) -> bool:
        if self._mode == "dir":
            return (self.path / inner_path.replace("/", os.sep)).exists()
        if self._mode == "zip" and self._zf:
            # Zip 里的路径通常以 "/" 分隔
            try:
                self._zf.getinfo(inner_path)
                return True
            except KeyError:
                # 也尝试反斜杠
                try:
                    self._zf.getinfo(inner_path.replace("/", "\\"))
                    return True
                except KeyError:
                    return False
        return False

    def read_bytes(self, inner_path: str) -> Optional[bytes]:
        try:
            if self._mode == "dir":
                p = self.path / inner_path.replace("/", os.sep)
                if not p.is_file():
                    return None
                return p.read_bytes()
            if self._mode == "zip" and self._zf:
                try:
                    return self._zf.read(inner_path)
                except KeyError:
                    try:
                        return self._zf.read(inner_path.replace("/", "\\"))
                    except KeyError:
                        return None
        except OSError:
            return None
        return None

    def read_text(self, inner_path: str) -> Optional[str]:
        b = self.read_bytes(inner_path)
        if b is None:
            return None
        for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                return b.decode(enc)
            except UnicodeDecodeError:
                continue
        return b.decode("utf-8", errors="replace")

    def list_root_files(self) -> list[str]:
        """列出根目录的文件（仅一级，加速查找）"""
        names: list[str] = []
        if self._mode == "dir":
            try:
                for item in self.path.iterdir():
                    if item.is_file():
                        names.append(item.name)
            except OSError:
                pass
        elif self._mode == "zip" and self._zf:
            for name in self._zf.namelist():
                # zip 条目如果不包含 "/" 就是在根目录；
                # 或者可能形如 "manifest.sii"
                if "/" not in name.rstrip("/") and not name.endswith("/"):
                    names.append(name)
        return names

    # ---------- 高层：解析 manifest ----------
    def parse_manifest(self) -> ModManifest:
        """读取 manifest.sii 并转成 ModManifest；若不存在则返回默认对象"""
        manifest = ModManifest()
        text = self.read_text("manifest.sii")
        if not text and self._mode == "external":
            # 加密包（SCS#/AEM!/加密ZIP）走外部解包工具提取 manifest.sii
            try:
                from services.external_extractor_service import extract_manifest_text
                text = extract_manifest_text(self.path)
            except Exception:
                text = None
        if not text:
            return manifest
        try:
            units = parse_sii(text)
        except Exception:
            return manifest
        # 找第一个 mod_package unit
        pkg: Optional[SiiUnit] = next((u for u in units if u.unit_type == "mod_package"), None)
        if pkg is None:
            # 有些文件的 unit_type 可能大小写有差异 —— 降级为第一个
            pkg = units[0] if units else None
        if pkg is None:
            return manifest
        manifest.package_name = pkg.unit_name or ""
        manifest.package_version = pkg.get("package_version", "") or ""
        manifest.display_name = pkg.get("display_name", "") or ""
        manifest.author = pkg.get("author", "") or ""
        manifest.categories = [c for c in pkg.get_list("category") if c]
        manifest.icon_filename = pkg.get("icon", "") or ""
        manifest.description_filename = pkg.get("description_file", "") or ""
        manifest.compatible_versions = [v for v in pkg.get_list("compatible_versions") if v]
        manifest.dlc_dependencies = [d for d in pkg.get_list("dlc_dependencies") if d]
        # multiplayer_optional 可能缺省
        mo = pkg.get("multiplayer_optional", "")
        if isinstance(mo, str):
            manifest.multiplayer_optional = mo.lower() != "false"
        # external 模式下，预提取 icon/description 候选（1 次 extractor 调用，供 read_icon/read_description 复用）
        if self._mode == "external":
            self._preload_external_bundle(manifest.icon_filename or "",
                                          manifest.description_filename or "")
        return manifest

    def _preload_external_bundle(self, icon_filename: str, desc_filename: str) -> None:
        """external 模式下，一次提取所有 icon/description 候选，缓存到 _external_cache。"""
        if self._external_cache is not None:
            return
        # 构建候选列表（与 read_icon/read_description 的候选一致）
        icon_candidates: List[str] = []
        if icon_filename:
            icon_candidates.append(icon_filename)
            if not icon_filename.lower().endswith((".jpg", ".jpeg", ".png")):
                icon_candidates += [icon_filename + ".jpg", icon_filename + ".jpeg", icon_filename + ".png"]
        icon_candidates += ["mod_icon.jpg", "icon.jpg", "preview.jpg", "thumbnail.jpg",
                            "mod_icon.png", "icon.png"]
        desc_candidates: List[str] = []
        if desc_filename:
            desc_candidates.append(desc_filename)
        desc_candidates += ["mod_description.txt", "description.txt", "readme.txt"]
        # 去重保序
        all_candidates: List[str] = list(dict.fromkeys(icon_candidates + desc_candidates))
        try:
            from services.external_extractor_service import extract_files_batch
            self._external_cache = extract_files_batch(self.path, all_candidates)
        except Exception:
            self._external_cache = {}

    # ---------- 高层：提取描述 ----------
    def read_description(self, filename: str) -> str:
        candidates: List[str] = []
        if filename: candidates.append(filename)
        candidates += ["mod_description.txt", "description.txt", "readme.txt"]
        for c in candidates:
            t = self.read_text(c)
            if t: return t
        # external 模式（加密包）下，从预提取缓存读取描述文件
        if self._mode == "external" and self._external_cache is not None:
            for c in candidates:
                b = self._external_cache.get(c)
                if not b:
                    continue
                for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
                    try:
                        return b.decode(enc)
                    except UnicodeDecodeError:
                        continue
        # 若预提取缓存未命中，按格式补提取
        if self._mode == "external" and self._external_cache is None:
            try:
                magic = self._detect_magic()
                if magic == "scs_hashfs":
                    from services.external_extractor_service import extract_files_batch
                    batch = extract_files_batch(self.path, candidates)
                    for c in candidates:
                        b = batch.get(c)
                        if not b:
                            continue
                        for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
                            try:
                                return b.decode(enc)
                            except UnicodeDecodeError:
                                continue
                else:
                    from services.external_extractor_service import extract_file_bytes
                    for c in candidates:
                        b = extract_file_bytes(self.path, c)
                        if not b:
                            continue
                        for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
                            try:
                                return b.decode(enc)
                            except UnicodeDecodeError:
                                continue
            except Exception:
                pass
        return ""

    # ---------- 高层：提取预览图 ----------
    def read_icon(self, filename: str) -> ModIcon:
        icon = ModIcon()
        # 尝试直接文件名，也尝试 .jpg/.png 作为 fallback
        candidates: List[str] = []
        if filename:
            candidates.append(filename)
            if not filename.lower().endswith((".jpg", ".jpeg", ".png")):
                candidates += [filename + ".jpg", filename + ".jpeg", filename + ".png"]
        # 如果 manifest 没写 icon，但包根目录有 "mod_icon.jpg" / "icon.jpg" 也兜底
        candidates += ["mod_icon.jpg", "icon.jpg", "preview.jpg", "thumbnail.jpg",
                       "mod_icon.png", "icon.png"]
        raw: Optional[bytes] = None
        used = ""
        for c in candidates:
            b = self.read_bytes(c)
            if b is not None and len(b) > 100:
                raw = b
                used = c
                break
        # external 模式（加密包）下，从预提取缓存读取图标
        if raw is None and self._mode == "external" and self._external_cache is not None:
            for c in candidates:
                b = self._external_cache.get(c)
                if b is not None and len(b) > 100:
                    raw = b
                    used = c
                    break
        # 若预提取缓存未命中（parse_manifest 未调用或候选不含目标），按格式补提取
        if raw is None and self._mode == "external" and self._external_cache is None:
            try:
                magic = self._detect_magic()
                if magic == "scs_hashfs":
                    from services.external_extractor_service import extract_files_batch
                    batch = extract_files_batch(self.path, candidates)
                    for c in candidates:
                        b = batch.get(c)
                        if b is not None and len(b) > 100:
                            raw = b
                            used = c
                            break
                else:
                    from services.external_extractor_service import extract_file_bytes
                    for c in candidates:
                        b = extract_file_bytes(self.path, c)
                        if b is not None and len(b) > 100:
                            raw = b
                            used = c
                            break
            except Exception:
                pass
        if raw is None:
            return icon
        icon.raw_bytes = raw
        icon.source_path = used
        # 识别格式和尺寸
        icon.format, icon.width, icon.height = _probe_image_info(raw)
        return icon


def _probe_image_info(data: bytes) -> Tuple[str, int, int]:
    """从字节头探测 JPG/PNG 的 (format, w, h)，失败返回 ('',0,0)"""
    if not data:
        return "", 0, 0
    # PNG
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        # IHDR chunk: length(4) + "IHDR"(4) + width(4) + height(4)
        import struct
        try:
            w, h = struct.unpack(">II", data[16:24])
            return "png", int(w), int(h)
        except struct.error:
            return "png", 0, 0
    # JPG
    if data[:2] == b"\xff\xd8":
        try:
            # 扫描 SOF0 / SOF2 marker: 0xFF 0xC0 或 0xFF 0xC2
            i = 2
            n = len(data)
            while i < n - 9:
                while i < n and data[i] != 0xFF:
                    i += 1
                while i < n and data[i] == 0xFF:
                    i += 1
                marker = data[i] if i < n else 0
                i += 1
                if marker in (0x01,) or (0xD0 <= marker <= 0xD9):
                    # Standalone markers (no length)
                    continue
                if marker == 0xDA:  # SOS — 之后是压缩数据，不再有尺寸信息
                    break
                if i + 1 >= n:
                    break
                seg_len = int.from_bytes(data[i:i+2], "big")
                if seg_len < 2:
                    break
                # SOF0/1/2/3/5/6/7/9/10/11/13/14/15 = 帧头，包含宽高
                if (0xC0 <= marker <= 0xC3) or (0xC5 <= marker <= 0xC7) or \
                   (0xC9 <= marker <= 0xCB) or (0xCD <= marker <= 0xCF):
                    # precision(1) + height(2) + width(2)
                    if i + 7 < n:
                        import struct
                        h, w = struct.unpack(">HH", data[i+3:i+7])
                        return "jpg", int(w), int(h)
                i += seg_len
        except Exception:
            pass
        return "jpg", 0, 0
    return "", 0, 0
