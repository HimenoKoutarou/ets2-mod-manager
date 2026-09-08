from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from core.models import Mod
from domain.mod_identity import (
    canonical_key,
    canonical_package_for_mod,
    mod_aliases,
    profile_entry_aliases,
)
from domain.priority_rules import profile_to_ui_order, ui_to_profile_order
from domain import mod_priority_rules as priority_rules


# ETS2 profile.sii 的 active_mods[] 是实际加载顺序：
#   active_mods[0] = 最低优先级（先加载）；
#   active_mods[N-1] = 最高优先级（后加载并覆盖前面的同名文件）。
# UI 工作列表使用用户更直观的相反方向：顶部 / order 0 = 最高优先级。

PRESET_CATEGORY_MAP = {
    # 预设名：tuple(该预设需要命中的关键词 / 分类)
    "map_bottom": (
        "map", "map_addon", "map_mod", "addon_map",
        # 关键词
        ("map", "name"), ("promods", "name"), ("rusmap", "name"), ("project", "name"),
        ("amur", "name"), ("baikal", "name"), ("sibir", "name"), ("siberia", "name"),
        ("turkey", "name"), ("africa", "name"), ("kazakhstan", "name"), ("carpathian", "name"),
        ("extended", "name"), ("open", "name"), ("rebuild", "name"), ("rebuild", "name"),
        ("poland", "name"), ("slovak", "name"), ("ukrain", "name"), ("russia", "name"),
        ("connector", "name"), ("road connection", "name"), ("rc", "name"),
    ),
    "assets_middle": (
        "assets", "model", "models", "media", "texture", "dlc", "support",
        ("asset", "name"), ("model", "name"), ("models", "name"), ("media", "name"),
        ("fix", "name"), ("patch", "name"), ("mod_", "name"),
    ),
    # 剩下的默认放 "function_top" 层 —— 所以该层几乎不写关键词
    "function_top": (
        "ai", "traffic", "trailer", "truck", "cargo", "weather", "sound", "interior",
        "economy", "physics", "camera", "ui", "hud", "tuning", "paint_job",
        ("traffic", "name"), ("trailer", "name"), ("truck", "name"), ("cargo", "name"),
        ("weather", "name"), ("graphics", "name"), ("ai", "name"), ("sound", "name"),
        ("real", "name"), ("event", "name"), ("tuning", "name"), ("skin", "name"),
        ("paint", "name"), ("logo", "name"), ("loading", "name"), ("screen", "name"),
        ("random event", "name"),
    ),
}


def _mod_match(mod: Mod, rules: Sequence) -> bool:
    """给定某一层的 rules，判断 mod 是否命中。"""
    name = ((mod.manifest.display_name if getattr(mod, "manifest", None) else "") or getattr(mod, "display_title", "") or mod.mod_id or "").lower()
    cat = (",".join(mod.manifest.categories) if getattr(mod, "manifest", None) else "").lower()
    for r in rules:
        if isinstance(r, tuple) and len(r) == 2:
            kw, target = r  # 形如 ("map", "name")
            if target == "name" and kw in name:
                return True
            if target == "category" and kw in cat:
                return True
        elif isinstance(r, str):
            # 字符串：命中 category 或 name 关键词
            if r in cat or r in name:
                return True
    return False


class PriorityService:
    """
    Compatibility adapter for scanned Mod metadata and legacy callers.

    The actual worklist transformations now live in
    ``domain.mod_priority_rules``.  The methods retained below are thin
    wrappers so older integrations can migrate without a flag day.
    """

    def __init__(self, known_mods: Iterable[Mod]):
        self.known_mods: List[Mod] = list(known_mods)
        self.by_name: Dict[str, Mod] = {}
        self.by_canonical: Dict[str, Mod] = {}
        self.by_display_title: Dict[str, Mod] = {}
        # 性能优化：worklist 反向索引缓存，避免 indices_for_category 每次线性扫描
        # 结构：{ id(worklist_tuple): { frozenset(pkg_set): List[int] } }
        # worklist 是 list[dict]，不可哈希，用元组化签名做 key
        self._worklist_sig = None
        self._pkg_index: Optional[Dict[str, List[int]]] = None
        for m in self.known_mods:
            # Keep one normalized alias index for package names, file names,
            # saved display titles, suffix variants, and legacy Workshop IDs.
            for alias in mod_aliases(m):
                self.by_name.setdefault(alias, m)
                normalized = canonical_key(alias)
                if normalized:
                    self.by_canonical.setdefault(normalized, m)
                if alias and not alias.isdigit():
                    self.by_display_title.setdefault(alias, m)

    @staticmethod
    def _canonical_key(value: object) -> str:
        """Normalize a profile/package key for duplicate detection only."""
        return canonical_key(value)

    @staticmethod
    def _canonical_package_for_mod(mod: Optional[Mod]) -> str:
        return canonical_package_for_mod(mod)

    def _resolve_mod(self, package_name: str) -> Optional[Mod]:
        """Resolve profile/package aliases to one scanned Mod object."""
        if not package_name:
            return None
        pn = str(package_name).strip()
        for alias in profile_entry_aliases(pn):
            resolved = self.by_name.get(alias)
            if resolved is not None:
                return resolved
            resolved = self.by_canonical.get(canonical_key(alias))
            if resolved is not None:
                return resolved
            resolved = self.by_display_title.get(alias)
            if resolved is not None:
                return resolved
        return None

    def resolve_mod(self, package_name: str) -> Optional[Mod]:
        """Public metadata port for Domain worklist rules."""
        return self._resolve_mod(package_name)

    # ---- 分类反向索引（性能优化） ----
    def _build_pkg_index(self, worklist: List[dict]) -> Dict[str, List[int]]:
        """构建 { package_name: [idx,...] } 反向索引，缓存到 self._pkg_index。

        性能：indices_for_category 原实现对每次批量操作都线性扫描 worklist O(n)。
        当连续调用 move_up/down/top/bottom 时会重复扫描。本方法按 worklist 内容签名
        缓存，签名变化时重建（worklist 是新对象或内容变了）。
        """
        # Category enable/disable mutates the list in place.  Caching by
        # ``id(worklist)`` would keep stale enabled indexes after that change.
        sig = tuple(
            (str(e.get("package_name") or ""), bool(e.get("enabled")))
            for e in worklist
        )
        if self._worklist_sig == sig and self._pkg_index is not None:
            return self._pkg_index
        idx_map: Dict[str, List[int]] = {}
        for i, e in enumerate(worklist):
            if not e.get("enabled"):
                continue
            pn = e.get("package_name")
            if pn:
                idx_map.setdefault(pn, []).append(i)
        self._pkg_index = idx_map
        self._worklist_sig = sig
        return idx_map

    # ---- 当前 active_mods → 工作模型（附带 enabled 状态） ----

    @classmethod
    def rebuild_from_active(cls, current_svc, current_worklist, new_active_entries):
        resolver = getattr(current_svc, "resolve_mod", None) or getattr(current_svc, "_resolve_mod", None)
        return priority_rules.rebuild_from_active(
            current_worklist,
            new_active_entries,
            known_mods=getattr(current_svc, "known_mods", ()),
            resolve_mod=resolver,
        )

    def build_worklist(self, active_mods: List[str], all_package_names: List[str]) -> List[dict]:
        """
        产出 [{
            "package_name": str,
            "enabled": bool,          # 是否在 active_mods 中
            "order": int,             # UI 优先级序号（0=最高优先级 / 列表顶部），不在列表中 = -1
            "mod": Optional[Mod],
        }, ...]。
        输入 active_mods 使用 profile.sii 的低到高顺序；输出先放已启用模组，
        并转换为 UI 的高到低顺序，然后追加未启用模组。
        """
        return priority_rules.build_worklist(
            active_mods,
            all_package_names,
            resolve_mod=self.resolve_mod,
        )

    # ---- 导出 UI 优先级列表：所有 enabled 条目，最高优先级在前 ----
    @staticmethod
    def worklist_to_active(worklist: List[dict]) -> List[str]:
        return priority_rules.worklist_to_active(worklist)

    @staticmethod
    def worklist_to_profile_active(worklist: List[dict]) -> List[str]:
        """Convert the UI high-to-low worklist to profile.sii low-to-high order."""
        return priority_rules.worklist_to_profile_active(worklist)

    # ---- 批量：启用 / 禁用 / 反转 ----
    @staticmethod
    def batch_toggle(worklist: List[dict],
                     indices: Sequence[int],
                     action: str = "toggle") -> List[dict]:
        """
        action: "enable" / "disable" / "toggle"
        返回新的 worklist（不修改原引用）。
        启用时，若条目原先未启用，则 append 到已启用列表末尾。
        """
        return priority_rules.batch_toggle(worklist, indices, action)

    # ---- 拖拽重排（把若干 index 移到某个目标位置之前） ----
    @staticmethod
    def reorder_before(worklist: List[dict],
                       indices: Sequence[int],
                       target_before_index: int,
                       scope_enabled_only: bool = True) -> List[dict]:
        """
        把 indices 指定的条目，整体移到 target_before_index 所指条目之前。
        scope_enabled_only=True：只调整"已启用"条目（通常用户只关心启用的加载顺序）。
        """
        return priority_rules.reorder_before(
            worklist, indices, target_before_index, scope_enabled_only
        )

    # ---- 批量上移 / 下移 / 置顶 / 置底 ----
    def move_up(self, worklist: List[dict], indices: Sequence[int], steps: int = 1) -> List[dict]:
        """整体上移 steps 位（保持 indices 指定条目之间的相对顺序）。"""
        return priority_rules.move_up(worklist, indices, steps)

    def move_down(self, worklist: List[dict], indices: Sequence[int], steps: int = 1) -> List[dict]:
        """整体下移 steps 位（保持 indices 指定条目之间的相对顺序）。"""
        return priority_rules.move_down(worklist, indices, steps)

    def move_top(self, worklist: List[dict], indices: Sequence[int]) -> List[dict]:
        return priority_rules.move_top(worklist, indices)

    def move_bottom(self, worklist: List[dict], indices: Sequence[int]) -> List[dict]:
        return priority_rules.move_bottom(worklist, indices)

    @staticmethod
    def _renumber(worklist: List[dict]) -> List[dict]:
        return priority_rules._renumber(worklist)

    # ---- 预设优先级（地图底 / 素材中 / 功能上） ----
    def apply_preset(self, worklist: List[dict]) -> List[dict]:
        """
        将所有已启用条目按 UI 的高到低顺序分成三层：
            · function_top  → UI 顶部（最高优先级）
            · assets_middle → 中间
            · map_bottom    → UI 底部（最低优先级）

        保存时 worklist_to_profile_active() 会反转为游戏的实际加载顺序。
        """
        return priority_rules.apply_preset(worklist)


    # —— 分类整体块移动（基于 package_name 集合，保持块内相对顺序）——
    def indices_for_category(self, worklist, pkg_set):
        """
        返回 worklist 中同时满足以下条件的条目的下标（按 worklist 原顺序升序，天然保持块内相对顺序）：
          1. entry["enabled"] is True （只对已启用的 mod 做排序）
          2. entry["package_name"] in pkg_set

        性能优化：用 _build_pkg_index 的反向索引 O(1) 查询每个 pkg，
        避免每次都线性扫描整个 worklist O(n)。
        """
        if not pkg_set:
            return []
        # 用反向索引加速：O(|pkg_set|) 而非 O(|worklist|)
        idx_map = self._build_pkg_index(worklist)
        idx = []
        for pn in pkg_set:
            if pn in idx_map:
                idx.extend(idx_map[pn])
        # 排序保持 worklist 原顺序
        idx.sort()
        return idx

    def move_up_by_package_set(self, worklist, pkg_set, steps=1):
        """对属于 pkg_set 的已启用 entry 整体上移 steps（保持相对顺序）。"""
        indices = self.indices_for_category(worklist, pkg_set)
        if not indices or steps <= 0:
            return worklist
        return self.move_up(worklist, indices, steps=steps)

    def move_down_by_package_set(self, worklist, pkg_set, steps=1):
        """对属于 pkg_set 的已启用 entry 整体下移 steps（保持相对顺序）。"""
        indices = self.indices_for_category(worklist, pkg_set)
        if not indices or steps <= 0:
            return worklist
        return self.move_down(worklist, indices, steps=steps)

    def move_top_by_package_set(self, worklist, pkg_set):
        """把属于 pkg_set 的已启用 entry 整体移到 active 段最前方（保持相对顺序）。"""
        indices = self.indices_for_category(worklist, pkg_set)
        if not indices:
            return worklist
        return self.move_top(worklist, indices)

    def move_bottom_by_package_set(self, worklist, pkg_set):
        """把属于 pkg_set 的已启用 entry 整体移到 active 段最后方（保持相对顺序）。"""
        indices = self.indices_for_category(worklist, pkg_set)
        if not indices:
            return worklist
        return self.move_bottom(worklist, indices)
