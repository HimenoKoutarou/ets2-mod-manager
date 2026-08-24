from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Optional, Sequence

from core.models import Mod


# 模组优先级：ETS2 的 active_mods[] 顺序 = 游戏内 Mod Manager 列表顺序（从上到下）。
#   active_mods[0] = 列表第一个 = 最高优先级（覆盖下面的同名文件）；
#   active_mods[N-1] = 列表最后一个 = 最低优先级（被上面覆盖）。
# 我们把语义和 SCS 一致：
#   ORDER = active_mods 中的下标，0 = 最高优先级，越大优先级越低。

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
    负责：
      - 从 Mod 列表 + 当前 active_mods 合成「完整带状态的工作列表」
      - 批量启用 / 禁用 / 开关反转
      - 拖拽重排 / 置顶 / 置底 / 批量上移下移
      - 一键"套用推荐优先级预设"（地图地图在底 / 素材模型居中 / 功能AI在上）
    """

    def __init__(self, known_mods: Iterable[Mod]):
        # by package_name 做快速查找
        self.by_name: Dict[str, Mod] = {}
        for m in known_mods:
            self.by_name[m.mod_id] = m

    # ---- 当前 active_mods → 工作模型（附带 enabled 状态） ----
    def build_worklist(self, active_mods: List[str], all_package_names: List[str]) -> List[dict]:
        """
        产出 [{
            "package_name": str,
            "enabled": bool,          # 是否在 active_mods 中
            "order": int,             # 优先级序号（0=最高优先级 / 列表第一个），不在列表中 = -1
            "mod": Optional[Mod],
        }, ...]。
        排列规则：先 active_mods 原顺序，然后是"未启用的已知模组"（按 package_name 排）。
        """
        result: List[dict] = []
        active_set = set(active_mods)
        for i, pn in enumerate(active_mods):
            result.append({
                "package_name": pn,
                "enabled": True,
                "order": i,
                "mod": self.by_name.get(pn),
            })
        # 未启用的（不重复）
        known_pns = [p for p in all_package_names if p not in active_set]
        for pn in sorted(known_pns):
            result.append({
                "package_name": pn,
                "enabled": False,
                "order": -1,
                "mod": self.by_name.get(pn),
            })
        return result

    # ---- 导出新的 active_mods 列表：按工作列表中"所有 enabled 条目"的当前顺序 ----
    @staticmethod
    def worklist_to_active(worklist: List[dict]) -> List[str]:
        return [x["package_name"] for x in worklist if x.get("enabled")]

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
        new = [dict(x) for x in worklist]
        # 1) 处理 enabled 状态
        for i in indices:
            if i < 0 or i >= len(new):
                continue
            it = new[i]
            if action == "enable":
                it["enabled"] = True
            elif action == "disable":
                it["enabled"] = False
            else:
                it["enabled"] = not it["enabled"]
        # 2) 维持"先启用、后禁用"顺序，保持启用条目之间的相对顺序不变
        enabled_part = [x for x in new if x["enabled"]]
        disabled_part = [x for x in new if not x["enabled"]]
        # 重算 order
        for i, x in enumerate(enabled_part):
            x["order"] = i
        for x in disabled_part:
            x["order"] = -1
        return enabled_part + disabled_part

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
        new = [dict(x) for x in worklist]
        if scope_enabled_only:
            enabled = [(i, x) for i, x in enumerate(new) if x["enabled"]]
        else:
            enabled = list(enumerate(new))
        # 把 indices 限制在 enabled 所在的条目子集里找
        to_move: List[tuple] = []
        target_sub_i = None
        # 建立 "全局 idx → 子列表 i" 映射
        sub_to_global = [g for g, _ in enabled]
        global_to_sub = {g: i for i, g in enumerate(sub_to_global)}
        for gi in indices:
            if gi in global_to_sub:
                to_move.append(global_to_sub[gi])
        if target_before_index in global_to_sub:
            target_sub_i = global_to_sub[target_before_index]
        if not to_move:
            return new
        # 先按升序排列 to_move，保证相对顺序
        to_move.sort()
        sub_entries = [x for _, x in enabled]
        moved_entries = [sub_entries[i] for i in to_move]
        remain_entries = [sub_entries[i] for i in range(len(sub_entries)) if i not in set(to_move)]
        # 插回到 target_sub_i 之前（注意 target_sub_i 应该是 remain 里的位置）
        if target_sub_i is None:
            # 没有 target → 末尾
            merged = remain_entries + moved_entries
        else:
            # target_sub_i 是原 sub_entries 中的 index → 计算在 remain 中应该插入的位置
            # 思路：原 target 在 moved 之后 → 插入到 remain 中 target_sub_i - (moved<target 的数量)
            n_smaller = sum(1 for m in to_move if m < target_sub_i)
            remain_pos = target_sub_i - n_smaller
            merged = remain_entries[:remain_pos] + moved_entries + remain_entries[remain_pos:]
        if scope_enabled_only:
            # 把 merged 按顺序塞回 new 中已启用条目所在的位置
            for new_val, (g_pos, _) in zip(merged, enabled):
                new[g_pos] = new_val
            # 重算 order
            o = 0
            for x in new:
                if x["enabled"]:
                    x["order"] = o; o += 1
                else:
                    x["order"] = -1
            return new
        # 非 enabled-only 模式：直接把 merged 替换掉对应位置
        # 把 disabled 保留并放最后
        disabled = [x for x in new if not x["enabled"]]
        for x in merged:
            x["order"] = new.index(x) if x.get("enabled") else -1
        # 重新编号 enabled
        result = []
        for i, x in enumerate(merged):
            y = dict(x); y["order"] = i; result.append(y)
        for x in disabled:
            y = dict(x); y["order"] = -1; result.append(y)
        return result

    # ---- 批量上移 / 下移 / 置顶 / 置底 ----
    def move_up(self, worklist: List[dict], indices: Sequence[int], steps: int = 1) -> List[dict]:
        """整体上移 steps 位（保持 indices 指定条目之间的相对顺序）。"""
        if not indices or steps <= 0:
            return self._renumber(worklist)
        # 映射：全局下标 → 在 enabled 段中的 sub index
        enabled = [(i, x) for i, x in enumerate(worklist) if x["enabled"]]
        global_to_sub = {g: s for s, (g, _) in enumerate(enabled)}
        sub_ids = sorted(global_to_sub[g] for g in indices if g in global_to_sub)
        if not sub_ids:
            return self._renumber(worklist)
        first_sub = sub_ids[0]
        target_sub = max(0, first_sub - steps)
        if target_sub == first_sub:
            return self._renumber(worklist)
        # reorder_before 需要"目标全局下标（在其之前插入）"
        target_global = enabled[target_sub][0]
        return self.reorder_before(worklist, list(indices), target_global)

    def move_down(self, worklist: List[dict], indices: Sequence[int], steps: int = 1) -> List[dict]:
        """整体下移 steps 位（保持 indices 指定条目之间的相对顺序）。"""
        if not indices or steps <= 0:
            return self._renumber(worklist)
        enabled = [(i, x) for i, x in enumerate(worklist) if x["enabled"]]
        global_to_sub = {g: s for s, (g, _) in enumerate(enabled)}
        sub_ids = sorted(global_to_sub[g] for g in indices if g in global_to_sub)
        if not sub_ids:
            return self._renumber(worklist)
        last_sub = sub_ids[-1]
        # 下移 steps 后：块尾应该落在 last_sub + steps，插入点 = last_sub + steps + 1（插在 target 条目前）
        target_sub = last_sub + 1 + steps
        if target_sub >= len(enabled):
            # 超出末尾 → 直接置底
            return self.move_bottom(worklist, list(indices))
        if target_sub == last_sub + 1:
            return self._renumber(worklist)
        target_global = enabled[target_sub][0]
        return self.reorder_before(worklist, list(indices), target_global)

    def move_top(self, worklist: List[dict], indices: Sequence[int]) -> List[dict]:
        first_enabled = next((i for i, x in enumerate(worklist) if x["enabled"]), None)
        if first_enabled is None:
            return self._renumber(worklist)
        return self.reorder_before(worklist, indices, first_enabled)

    def move_bottom(self, worklist: List[dict], indices: Sequence[int]) -> List[dict]:
        # 置底：相当于"移到尾后" → target 取 None
        # reorder_before 不支持 None，这里直接取 last_enabled 之后
        enabled = [i for i, x in enumerate(worklist) if x["enabled"]]
        if not enabled:
            return self._renumber(worklist)
        idx_set = set(indices)
        moved = [worklist[i] for i in enabled if i in idx_set]
        rest = [worklist[i] for i in enabled if i not in idx_set]
        new_enabled = rest + moved
        # 塞回
        result = list(worklist)
        ptr = 0
        for gi in enabled:
            result[gi] = new_enabled[ptr]; ptr += 1
        return self._renumber(result)

    @staticmethod
    def _renumber(worklist: List[dict]) -> List[dict]:
        out = [dict(x) for x in worklist]
        o = 0
        for x in out:
            if x["enabled"]:
                x["order"] = o; o += 1
            else:
                x["order"] = -1
        return out

    # ---- 预设优先级（地图底 / 素材中 / 功能上） ----
    def apply_preset(self, worklist: List[dict]) -> List[dict]:
        """
        将所有已启用条目分成三层：
            底层（最后加载 = 优先级最高？不！ET2 数组是"先出现先加载"，后面覆盖前面。
            为了让"地图资产包"不被修改，反而应该先加载它。所以：
                · map_bottom    → 排在 active_mods 前面（先加载）
                · assets_middle → 中间
                · function_top  → 排在最后（最高优先级，覆盖前面的 asset/map 皮肤）
        """
        enabled = [dict(x) for x in worklist if x["enabled"]]
        disabled = [dict(x) for x in worklist if not x["enabled"]]

        bottom, middle, top = [], [], []
        for x in enabled:
            mod = x.get("mod")
            # 没 mod 信息时做伪 Mod：手动拿名称判断
            if mod is None:
                pseudo = Mod(mod_id=x["package_name"], package_path="", package_type="local",
                             source_type="local", source_path="", files=[], size=0, enabled=False,
                             load_order=-1, timestamp=0)
                _hit_btm = _mod_match(pseudo, PRESET_CATEGORY_MAP["map_bottom"])
                _hit_top = _mod_match(pseudo, PRESET_CATEGORY_MAP["function_top"])
            else:
                _hit_btm = _mod_match(mod, PRESET_CATEGORY_MAP["map_bottom"])
                _hit_top = _mod_match(mod, PRESET_CATEGORY_MAP["function_top"])
            if _hit_btm:
                bottom.append(x)
            elif _hit_top:
                top.append(x)
            else:
                middle.append(x)
        new_enabled = bottom + middle + top
        for i, x in enumerate(new_enabled):
            x["order"] = i
        for x in disabled:
            x["order"] = -1
        return new_enabled + disabled


    # —— 分类整体块移动（基于 package_name 集合，保持块内相对顺序）——
    def indices_for_category(self, worklist, pkg_set):
        """
        返回 worklist 中同时满足以下条件的条目的下标（按 worklist 原顺序升序，天然保持块内相对顺序）：
          1. entry["enabled"] is True （只对已启用的 mod 做排序）
          2. entry["package_name"] in pkg_set
        """
        if not pkg_set:
            return []
        idx = []
        for i, e in enumerate(worklist):
            if e.get("enabled") and e.get("package_name") in pkg_set:
                idx.append(i)
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
