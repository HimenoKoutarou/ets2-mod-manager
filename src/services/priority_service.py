from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

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
        self.known_mods: List[Mod] = list(known_mods)
        self.by_name: Dict[str, Mod] = {}
        # 性能优化：worklist 反向索引缓存，避免 indices_for_category 每次线性扫描
        # 结构：{ id(worklist_tuple): { frozenset(pkg_set): List[int] } }
        # worklist 是 list[dict]，不可哈希，用元组化签名做 key
        self._worklist_sig = None
        self._pkg_index: Optional[Dict[str, List[int]]] = None
        import re as _re_pn
        for m in self.known_mods:
            # manifest.package_name（unit_name 主索引）与 mod_id（文件名索引）同级 setdefault
            # 先 manifest.package_name 左段
            pkg_name = getattr(getattr(m, "manifest", None), "package_name", None) or ""
            if pkg_name:
                left = pkg_name.split("|",1)[0].strip()
                if left:
                    self.by_name.setdefault(left, m)
                self.by_name.setdefault(pkg_name.strip(), m)
            # 再 mod_id（同级权重）
            if m.mod_id:
                self.by_name.setdefault(m.mod_id, m)
            # 再 workshop_id 剥后缀纯数字
            stripped = _re_pn.sub(r"_(workshop|copy\d*|local)$", "", m.mod_id)
            if stripped and stripped != m.mod_id and stripped.isdigit():
                self.by_name.setdefault(stripped, m)

    @staticmethod
    def _canonical_key(value: object) -> str:
        """Normalize a profile/package key for duplicate detection only."""
        import re
        text = str(value or "").split("|", 1)[0].strip()
        return re.sub(
            r"_(workshop|copy\d*|local)$", "", text, flags=re.IGNORECASE
        ).casefold()

    @staticmethod
    def _canonical_package_for_mod(mod: Optional[Mod]) -> str:
        if mod is None:
            return ""
        mf = getattr(mod, "manifest", None)
        package = str(getattr(mf, "package_name", "") or "").strip() if mf else ""
        mod_id = str(getattr(mod, "mod_id", "") or "").strip()
        if getattr(mod, "package_type", "") == "workshop" and mod_id:
            return mod_id
        return package or mod_id

    def _resolve_mod(self, package_name: str) -> Optional[Mod]:
        """Resolve profile/package aliases to one scanned Mod object."""
        if not package_name:
            return None
        import re
        pn = str(package_name).strip()
        left = pn.split("|", 1)[0].strip()
        suffix_left = re.sub(r"_(workshop|copy\d*|local)$", "", left, flags=re.IGNORECASE)
        suffix_full = re.sub(r"_(workshop|copy\d*|local)$", "", pn, flags=re.IGNORECASE)
        for key in (pn, left, suffix_left, suffix_full):
            if key and key in self.by_name:
                return self.by_name[key]
        if left.isdigit() or suffix_left.isdigit() or pn.isdigit():
            target = left if left.isdigit() else (suffix_left if suffix_left.isdigit() else pn)
            for mod in self.known_mods:
                mid = re.sub(
                    r"_(workshop|copy\d*|local)$", "",
                    str(getattr(mod, "mod_id", "") or ""),
                    flags=re.IGNORECASE,
                )
                mf_pkg = str(
                    getattr(getattr(mod, "manifest", None), "package_name", "") or ""
                ).split("|", 1)[0].strip()
                if mid == target or mf_pkg == target:
                    return mod
        target = self._canonical_key(pn)
        if target:
            for mod in self.known_mods:
                mf = getattr(mod, "manifest", None)
                keys = (
                    getattr(mod, "mod_id", ""),
                    getattr(mf, "package_name", "") if mf else "",
                    getattr(mf, "display_name", "") if mf else "",
                    getattr(mod, "display_title", ""),
                )
                if any(self._canonical_key(key) == target for key in keys if key):
                    return mod
        return None

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
        # Rebuild worklist rows after an in-memory active-mods reorder.
        # Mirrors build_worklist() row shape: enabled, priority_index, package_name,
        # mod + display_title metadata carried over from current_worklist where present.
        # Disabled rows get priority_index = None.
        if current_worklist is None:
            current_worklist = []
        active_keys = []
        for e in new_active_entries:
            if isinstance(e, dict):
                key = (e.get("package_name") or e.get("mod_id") or "").strip()
            else:
                key = str(e).strip()
            if key:
                active_keys.append(key)
        active_rank = {k: i for i, k in enumerate(active_keys)}
        new_wl = []
        for row in current_worklist:
            r2 = dict(row)
            pkg = str(r2.get("package_name") or "").strip()
            if pkg and pkg in active_rank:
                r2["enabled"] = True
                r2["order"] = active_rank[pkg]
                r2["priority_index"] = active_rank[pkg]
            else:
                r2["enabled"] = False
                r2["order"] = -1
                r2["priority_index"] = None
            new_wl.append(r2)
        seen = {str(r.get("package_name") or "").strip() for r in new_wl}
        mod_index = {}
        mods_src = (
            getattr(current_svc, "known_mods", None)
            or getattr(current_svc, "mods", None)
            or []
        )
        for m in mods_src:
            for key in (getattr(m, "package_name", None), getattr(m, "mod_id", None)):
                if key:
                    mod_index[str(key).strip()] = m
        for k in active_keys:
            if k and k not in seen:
                m = mod_index.get(k)
                new_wl.append({
                    "mod": m,
                    "package_name": k,
                    "display_title": (getattr(m, "display_title", None) or k) if m else k,
                    "enabled": True,
                    "order": active_rank[k],
                    "priority_index": active_rank[k],
                    "source": "",
                    "size_mb": None,
                    "compatible_versions": "",
                })
                seen.add(k)
        def _key(r):
            pkg = str(r.get("package_name") or "").strip()
            if pkg in active_rank:
                return (0, active_rank[pkg], pkg)
            return (1, 0, pkg)
        new_wl.sort(key=_key)
        return new_wl

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
        represented_mods: set[int] = set()
        represented_keys: set[str] = set()
        enabled_count = 0

        def add_entry(package_name: str, enabled: bool, mod: Optional[Mod]) -> bool:
            nonlocal enabled_count
            pn = str(package_name or "").strip()
            if not pn:
                return False
            key = self._canonical_key(pn)
            identity = id(mod) if mod is not None else None
            # A single scanned Mod can have several aliases (manifest name,
            # filename, Workshop id).  Keep one worklist row per real Mod,
            # while preserving the exact active profile spelling when enabled.
            if identity is not None and identity in represented_mods:
                return False
            if key and key in represented_keys:
                return False
            if identity is not None:
                represented_mods.add(identity)
            if key:
                represented_keys.add(key)
            result.append({
                "package_name": pn,
                "enabled": bool(enabled),
                "order": enabled_count if enabled else -1,
                "priority_index": enabled_count if enabled else None,
                "mod": mod,
            })
            if enabled:
                enabled_count += 1
            return True

        # First retain the profile's exact active_mods order and spelling.
        for pn in active_mods:
            add_entry(pn, True, self._resolve_mod(pn))

        # Then add each inactive scanned Mod once.  ``all_package_names`` is
        # an alias index in the UI, so resolve and deduplicate by object identity.
        for pn in sorted({str(p).strip() for p in all_package_names if str(p).strip()}):
            mod = self._resolve_mod(pn)
            canonical = self._canonical_package_for_mod(mod) if mod is not None else pn
            add_entry(canonical or pn, False, mod)
        return result

    # ---- 导出新的 active_mods 列表：按工作列表中"所有 enabled 条目"的当前顺序 ----
    @staticmethod
    def worklist_to_active(worklist: List[dict]) -> List[str]:
        active = []
        for x in worklist:
            if not x.get("enabled"):
                continue
            package_name = str(x.get("package_name") or "").strip()
            if not package_name:
                continue
            # ETS2 对 Workshop 条目使用 `package_name|display_name`，仅写
            # 数字 Workshop ID 时游戏启动后会将该条目视为无效并丢弃。
            mod = x.get("mod")
            if mod is not None and getattr(mod, "package_type", "") == "workshop" and "|" not in package_name:
                title = str(getattr(mod, "display_title", "") or "").strip()
                if title and not title.isdigit():
                    package_name = f"{package_name}|{title}"
            active.append(package_name)
        return active

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
            x["priority_index"] = i
        for x in disabled_part:
            x["order"] = -1
            x["priority_index"] = None
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
                    x["order"] = o
                    x["priority_index"] = o
                    o += 1
                else:
                    x["order"] = -1
                    x["priority_index"] = None
            return new
        # 非 enabled-only 模式：直接把 merged 替换掉对应位置
        # 把 disabled 保留并放最后
        disabled = [x for x in new if not x["enabled"]]
        for x in merged:
            x["order"] = new.index(x) if x.get("enabled") else -1
        # 重新编号 enabled
        result = []
        for i, x in enumerate(merged):
            y = dict(x); y["order"] = i; y["priority_index"] = i; result.append(y)
        for x in disabled:
            y = dict(x); y["order"] = -1; y["priority_index"] = None; result.append(y)
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
                x["order"] = o
                x["priority_index"] = o
                o += 1
            else:
                x["order"] = -1
                x["priority_index"] = None
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
            x["priority_index"] = i
        for x in disabled:
            x["order"] = -1
            x["priority_index"] = None
        return new_enabled + disabled


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
