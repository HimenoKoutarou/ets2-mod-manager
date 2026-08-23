from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional, List


# 官方支持的 18 种分类
VALID_CATEGORIES = {
    "truck", "trailer", "interior", "tuning_parts", "ai_traffic",
    "sound", "paint_job", "cargo_pack", "map", "ui",
    "weather_setup", "physics", "graphics", "models",
    "movers", "walkers", "prefabs", "other",
}

CATEGORY_LABELS = {
    "truck": "卡车",
    "trailer": "挂车",
    "interior": "内饰",
    "tuning_parts": "改装件",
    "ai_traffic": "AI 交通",
    "sound": "声音",
    "paint_job": "涂装",
    "cargo_pack": "货物包",
    "map": "地图",
    "ui": "界面",
    "weather_setup": "天气",
    "physics": "物理",
    "graphics": "图形",
    "models": "模型",
    "movers": "动态物体",
    "walkers": "行人",
    "prefabs": "预制件",
    "other": "其他",
}


@dataclass
class ModManifest:
    """从 manifest.sii 中解析出的模组元数据"""
    package_name: str = ""         # mod_package 的 unit_name（游戏在 mods_info.sii 缓存的 key）
    package_version: str = ""
    display_name: str = ""
    author: str = ""
    categories: List[str] = field(default_factory=list)
    icon_filename: str = ""           # mod_icon.jpg
    description_filename: str = ""     # mod_description.txt
    compatible_versions: List[str] = field(default_factory=list)
    dlc_dependencies: List[str] = field(default_factory=list)
    multiplayer_optional: bool = True

    @property
    def category_labels(self) -> List[str]:
        try:
            from services.i18n_service import _ as _tr
            out = []
            for c in self.categories:
                i18n_key = "cat." + c
                t = _tr(i18n_key)
                # 若未翻译则 fallback 到 CATEGORY_LABELS
                if t == i18n_key:
                    t = CATEGORY_LABELS.get(c, c)
                out.append(t)
            return out
        except Exception:
            return [CATEGORY_LABELS.get(c, c) for c in self.categories]


@dataclass
class ModIcon:
    """预览图包装"""
    raw_bytes: Optional[bytes] = None
    format: str = "jpg"             # jpg / png
    width: int = 0
    height: int = 0
    source_path: str = ""           # 调试用

    @property
    def is_available(self) -> bool:
        return self.raw_bytes is not None and len(self.raw_bytes) > 0


@dataclass
class Mod:
    """一个模组（.scs / .zip / 文件夹 / Steam Workshop 包）的完整信息"""
    mod_id: str                     # 唯一 ID：文件名不带扩展名 / workshop 目录名
    package_path: str               # 真实磁盘路径
    package_type: str               # "scs" | "zip" | "directory" | "workshop"
    file_size: int = 0              # 字节
    last_modified: float = 0.0      # 时间戳

    manifest: ModManifest = field(default_factory=ModManifest)
    icon: ModIcon = field(default_factory=ModIcon)
    description: str = ""           # 已读取的描述文本（含颜色标签）

    # 运行时状态
    is_enabled: bool = False        # 是否在当前 profile 中启用
    priority_index: int = -1        # 在 active_mods 中的位置（-1=未启用）
    mods_info_timestamp: int = 0    # 来自 mods_info.sii 的时间戳

    # 虚拟分类标签：UI "文件夹" 分类（空串 = 未分类），持久化到 category_service
    _category_tag: str = ""

    @property
    def category_tag(self) -> str:
        """优先读缓存服务，保证跨进程/跨次启动一致；_category_tag 只做内存临时覆盖。"""
        try:
            from services import category_service as _cs
            cached = _cs.get_category(self.mod_id)
            if cached:
                return cached
        except Exception:
            pass
        return self._category_tag or ""

    @category_tag.setter
    def category_tag(self, value: str) -> None:
        self._category_tag = value or ""
        try:
            from services import category_service as _cs
            _cs.set_category(self.mod_id, value or "")
            _cs.save()
        except Exception:
            pass

    @property
    def display_title(self) -> str:
        """多层兜底：display_name → Steam Workshop API（按ID） → 子包名 → 归档名 → Workshop #ID"""
        title = self.manifest.display_name.strip()
        if title and not title.isdigit():
            return title
        # 兜底 0：package_name（manifest 的 unit_name；游戏在 mods_info.sii 缓存的就是它，本地、快、可靠）
        # 过滤垃圾值：SCS 的 unit_name 常以 "." 或 "_nameless" 开头（引用名，非可读包名）
        _PKG_BLOCK = {"manifest", "package_name", "mod_package", "mods_info", "nameless"}
        pkg = (self.manifest.package_name or "").strip()
        if (pkg and not pkg.isdigit()
                and not pkg.startswith(".") and not pkg.startswith("_")
                and pkg.lower() not in _PKG_BLOCK and len(pkg) >= 2):
            return pkg
        # 兜底 1：Steam Workshop API（按 mod_id 查缓存，不发网络请求）
        if self.mod_id and self.mod_id.isdigit() and self.package_type == "workshop":
            try:
                from services.steam_workshop_service import get_cached_title
                cached = get_cached_title(self.mod_id)
                if cached:
                    try:
                        if not self.manifest.display_name or self.manifest.display_name.strip().isdigit():
                            self.manifest.display_name = cached
                    except Exception:
                        pass
                    return cached
            except Exception:
                pass
        # 兜底 2：从 package_path 或其子文件推导出友好名
        from pathlib import Path as _Path
        pp = _Path(self.package_path)
        # 3a) 如果是 workshop 目录，看第一层子包
        if pp.is_dir():
            try:
                sub = [s for s in pp.iterdir() if s.is_file() and s.suffix.lower() in (".scs", ".zip")]
            except OSError:
                sub = []
            BLACKLIST2 = {"universal", "content", "data", "base", "main", "mod", "package"}
            for sf in sorted(sub):
                stem = sf.stem
                import re as _re3
                m3 = _re3.match(r"^(\d+)[_\-](.+)$", stem)
                c = m3.group(2) if m3 else stem
                c2 = _re3.sub(r"[_\-]?content$", "", c, flags=_re3.IGNORECASE).strip("_-")
                if c2: c = c2
                if c.lower() in BLACKLIST2: continue
                if c.isdigit(): continue
                nice = c.replace("_", " ").replace("-", " ").strip()
                if nice:
                    nice = " ".join(w[:1].upper() + w[1:] if w else w for w in nice.split())
                    if 2 <= len(nice) <= 80:
                        return nice
        # 3b) 用自身归档包名（本地 mod：.scs/.zip 文件名）
        stem_self = pp.stem if not pp.is_dir() else pp.name
        if stem_self and not stem_self.isdigit():
            nice = stem_self.replace("_", " ").replace("-", " ").strip()
            if nice:
                nice = " ".join(w[:1].upper() + w[1:] if w else w for w in nice.split())
                if 2 <= len(nice) <= 80:
                    return nice
        # 兜底 3：如果 mod_id 是纯数字（Workshop ID），格式化为友好显示
        if self.mod_id and self.mod_id.isdigit():
            return f"Workshop #{self.mod_id}"
        return self.mod_id

    @property
    def display_version(self) -> str:
        if self.manifest.package_version:
            return self.manifest.package_version
        try:
            from services.i18n_service import _ as _tr
            return _tr("detail.ver_notag")
        except Exception:
            return _tr("detail.ver_notag")

    @property
    def size_mb(self) -> float:
        return round(self.file_size / (1024 * 1024), 2)


@dataclass
class Profile:
    """游戏存档 Profile"""
    profile_id: str                 # 目录哈希名
    profile_path: str               # 完整路径
    display_name: str = ""          # 存档显示名（如果能读到）
    active_mods: List[str] = field(default_factory=list)  # active_mods[] 顺序
    is_encrypted: bool = True
    is_steam_cloud: bool = False


@dataclass
class ModPreset:
    """批量启用方案"""
    name: str
    enabled_mods: List[str] = field(default_factory=list)   # 顺序 = 优先级
    description: str = ""
