"""城市反查 mod 服务

按优先级扫描已启用 mod 的 def/city*.sii，建立 {city_name: [来源mod...]} 反向索引。
priority_index 升序 = 优先级从高到低（ETS2: active_mods[0] 优先级最高，覆盖后面）。
索引缓存到 assets/cache/city_index.json，启用 mod 集合变化时增量重建。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Iterable, Tuple

from core.scs_archive import ScsArchiveReader
from core.sii_parser import parse_sii
from core.models import Mod


# ------------------------------------------------------------------
# 数据结构
# ------------------------------------------------------------------
@dataclass
class CityHit:
    """单个 mod 对某城市的贡献条目。"""
    city_name: str            # 游戏内 city_name（SII key，未本地化）
    short_name: str = ""       # short_city_name
    country: str = ""          # country unit_name 引用
    mod_id: str = ""           # 来源 mod 的 mod_id
    mod_title: str = ""        # 来源 mod 的展示名
    package_path: str = ""     # 来源 mod 磁盘路径
    priority_index: int = -1   # 在 active_mods 中的位置（越小优先级越高）


@dataclass
class CityIndex:
    """完整索引：city_name -> 按 priority_index 升序排列的来源列表。"""
    cities: Dict[str, List[CityHit]] = field(default_factory=dict)
    profile_signature: str = ""    # 用于失效判断的签名（启用 mod 集合 + 顺序）
    built_at: float = 0.0

    def hits_for(self, city_name: str) -> List[CityHit]:
        """返回某城市的所有来源，已按优先级升序（第一个=生效）。"""
        return self.cities.get(city_name, [])

    def effective_mod(self, city_name: str) -> Optional[CityHit]:
        """返回某城市当前生效的 mod（priority_index 最小者）。"""
        hits = self.cities.get(city_name)
        return hits[0] if hits else None

    def search(self, keyword: str, limit: int = 200) -> List[Tuple[str, List[CityHit]]]:
        """模糊搜索城市名（不区分大小写、子串匹配），返回 [(city_name, hits), ...]。
        结果按城市名字母序，最多 limit 项。"""
        if not keyword:
            # 空关键字返回全部（按名排序）
            items = sorted(self.cities.items())
            return items[:limit]
        kw = keyword.lower()
        out = []
        for name, hits in self.cities.items():
            if kw in name.lower():
                out.append((name, hits))
        out.sort(key=lambda x: x[0].lower())
        return out[:limit]


# ------------------------------------------------------------------
# 服务

def _resolve_app_root(explicit: Optional[Path] = None) -> Path:
    """推导项目根目录（含 assets/cache 的目录）。

    优先级：
    1. 调用方显式传入的 project_root
    2. PyInstaller 打包后 sys.prefix（指向 exe 目录）
    3. sys._MEIPASS（PyInstaller 的 --onefile 模式临时解压路径）
    4. sys.argv[0] 的父目录（命令行运行）
    5. 当前工作目录（兜底）
    最终以存在 assets/cache 或 src/ 子目录作为可信根，否则逐级向上查找（最多 5 级）
    """
    if explicit is not None:
        return Path(explicit)
    import sys as _sys

    # 候选根：按优先级生成列表
    candidates: list[Path] = []
    # 2) PyInstaller onedir 模式
    prefix = getattr(_sys, "prefix", None)
    if prefix:
        candidates.append(Path(prefix))
    # 3) PyInstaller onefile 模式
    meipass = getattr(_sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass))
    # 4) 入口脚本所在目录（开发模式）
    argv0 = getattr(_sys, "argv", None)
    if argv0 and argv0[0]:
        candidates.append(Path(argv0[0]).resolve().parent)
        # 再上一层（src/ 父目录，即 F:\ETS2ModManager）
        candidates.append(Path(argv0[0]).resolve().parent.parent)
    # 5) 兜底 cwd
    candidates.append(Path.cwd())

    # 辅助：判断路径是否是可信的"项目根"（有 assets/ 或 src/ 子目录）
    def _is_likely_root(p: Path) -> bool:
        return (p / "assets").is_dir() or (p / "src").is_dir()

    for c in candidates:
        try:
            c_resolved = c.resolve()
        except (OSError, RuntimeError):
            continue
        # 当前路径本身可能就是根
        if _is_likely_root(c_resolved):
            return c_resolved
        # 向上最多 5 层查找（处理 src/ui/ 这种入口下嵌套多层）
        for _ in range(5):
            parent = c_resolved.parent
            if parent == c_resolved or not parent.exists():
                break
            if _is_likely_root(parent):
                return parent
            c_resolved = parent
    # 实在找不到，回到 cwd 兜底
    return Path.cwd()

# ------------------------------------------------------------------
class CityLookupService:
    """扫描已启用 mod 建立城市反向索引，带磁盘缓存。"""

    CACHE_DIR = Path("assets/cache")
    CACHE_FILE_TEMPLATE = "city_index{}.json"


    def __init__(self, project_root: Optional[Path] = None,
                 profile_id: Optional[str] = None) -> None:
        """
        Args:
            project_root: 显式项目根，不传时由 _resolve_app_root 自动推导
            profile_id: 当前存档 ID（多 profile 场景下缓存按 profile 分桶）。
                不传则用全局缓存（city_index.json），与旧行为兼容。
        """
        self._root = _resolve_app_root(project_root)
        self._profile_id = profile_id
        # profile_id 中的非法字符替换为 _，防止写入非法文件名
        def _safe(name: str) -> str:
            return re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", name).strip(" .")
        if profile_id:
            suffix = "_" + _safe(profile_id)
        else:
            suffix = ""
        self._cache_path = self._root / self.CACHE_DIR / (self.CACHE_FILE_TEMPLATE.format(suffix))
        self._index: Optional[CityIndex] = None

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------
    def get_index(self) -> CityIndex:
        """返回当前索引（未加载则加载磁盘缓存）。"""
        if self._index is None:
            self._load_cache()
        if self._index is None:
            self._index = CityIndex()
        return self._index

    def rebuild(self, enabled_mods: List[Mod], progress_cb=None) -> CityIndex:
        """重新扫描已启用 mod 列表建立索引。

        Args:
            enabled_mods: 已启用 mod 列表（按 priority_index 升序，priority_index 越小优先级越高）
            progress_cb: 可选回调 (current, total, mod_title) 用于 UI 进度显示

        性能：对每个 mod 只打开一次 ScsArchiveReader，复用 _find_city_files + parse_sii。
        """
        index = CityIndex()
        index.profile_signature = self._signature(enabled_mods)
        import time
        index.built_at = time.time()

        total = len(enabled_mods)
        for i, mod in enumerate(enabled_mods):
            if progress_cb:
                try:
                    progress_cb(i, total, mod.display_title)
                except Exception:
                    pass
            try:
                self._scan_mod_into(index, mod)
            except Exception:
                # 单个 mod 失败不影响整体
                continue

        # 对每个城市的 hits 按 priority_index 升序排序（确保第一个是生效 mod）
        for hits in index.cities.values():
            hits.sort(key=lambda h: h.priority_index if h.priority_index >= 0 else 1 << 30)

        self._index = index
        self._save_cache()
        return index

    def ensure_fresh(self, enabled_mods: List[Mod], progress_cb=None) -> CityIndex:
        """若缓存签名匹配则直接用缓存，否则 rebuild。"""
        sig = self._signature(enabled_mods)
        if self._index is None:
            self._load_cache()
        if self._index is not None and self._index.profile_signature == sig:
            return self._index
        return self.rebuild(enabled_mods, progress_cb)

    # ------------------------------------------------------------------
    # 扫描实现
    # ------------------------------------------------------------------
    def _scan_mod_into(self, index: CityIndex, mod: Mod) -> None:
        """扫描单个 mod 包，把其贡献的城市追加到 index。"""
        pkg_path = Path(mod.package_path)
        if not pkg_path.exists():
            return

        try:
            with ScsArchiveReader(pkg_path) as reader:
                city_files = self._find_city_files(reader)
                if not city_files:
                    return

                mod_title = mod.display_title
                p_idx = mod.priority_index

                for cf in city_files:
                    text = reader.read_text(cf)
                    if not text:
                        continue
                    for city in self._extract_cities(text):
                        hit = CityHit(
                            city_name=city["city_name"],
                            short_name=city.get("short_name", ""),
                            country=city.get("country", ""),
                            mod_id=mod.mod_id,
                            mod_title=mod_title,
                            package_path=str(pkg_path),
                            priority_index=p_idx,
                        )
                        index.cities.setdefault(hit.city_name, []).append(hit)
        except Exception:
            return

    @staticmethod
    def _find_city_files(reader: ScsArchiveReader) -> List[str]:
        """查找 def/city*.sii（含 infix 多文件: def/city.foo.sii）。

        复用 game_data._find_def_files 的匹配规则但只针对 city。
        """
        found: List[str] = []
        candidates: List[str] = []
        # 匹配 def/city.sii 或 def/city.<infix>.sii（不匹配子目录如 def/city/sub.sii）
        pattern = re.compile(r"^def/city(\.[^/]+)?\.sii$", re.IGNORECASE)

        if reader._mode == "zip" and reader._zf:
            for name in reader._zf.namelist():
                if pattern.match(name.lower()):
                    candidates.append(name)
        elif reader._mode == "dir":
            def_dir = reader.path / "def"
            if def_dir.exists():
                try:
                    for p in def_dir.iterdir():
                        if p.is_file() and pattern.match(f"def/{p.name.lower()}"):
                            candidates.append(f"def/{p.name}")
                except OSError:
                    pass

        # base 文件优先排前
        base = "def/city.sii"
        if base in candidates:
            found.append(base)
            candidates.remove(base)
        found.extend(candidates)
        return found

    @staticmethod
    def _extract_cities(text: str) -> List[Dict[str, str]]:
        """从 SII 文本提取 city_data unit 的 city_name/short_city_name/country。"""
        out: List[Dict[str, str]] = []
        try:
            units = parse_sii(text)
        except Exception:
            return out
        for u in units:
            if u.unit_type != "city_data":
                continue
            name = (u.get("city_name", "") or "").strip()
            if not name:
                continue
            out.append({
                "city_name": name,
                "short_name": (u.get("short_city_name", "") or "").strip(),
                "country": (u.get("country", "") or "").strip(),
            })
        return out

    # ------------------------------------------------------------------
    # 缓存读写
    # ------------------------------------------------------------------
    @staticmethod
    def _signature(enabled_mods: Iterable[Mod]) -> str:
        """签名 = 启用 mod 的 (mod_id, priority_index) 序列。
        mod 集合或顺序变化都触发重建。"""
        parts = [f"{m.mod_id}@{m.priority_index}" for m in enabled_mods]
        return "|".join(parts)

    def _load_cache(self) -> None:
        if not self._cache_path.exists():
            return
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            cities: Dict[str, List[CityHit]] = {}
            for name, hits_raw in data.get("cities", {}).items():
                hits = [CityHit(**h) for h in hits_raw]
                cities[name] = hits
            self._index = CityIndex(
                cities=cities,
                profile_signature=data.get("profile_signature", ""),
                built_at=float(data.get("built_at", 0)),
            )
        except Exception:
            self._index = None

    def _save_cache(self) -> None:
        if self._index is None:
            return
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "profile_signature": self._index.profile_signature,
                "built_at": self._index.built_at,
                "cities": {name: [asdict(h) for h in hits]
                           for name, hits in self._index.cities.items()},
            }
            self._cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
