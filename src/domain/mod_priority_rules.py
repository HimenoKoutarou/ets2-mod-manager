"""Pure Mod worklist and priority operations.

This module is deliberately independent from Qt, the filesystem, and service
classes.  A resolver callback is injected for operations that need scanned Mod
metadata; every ordering or enable-state transformation works on plain Python
values and returns a detached worklist.
"""
from __future__ import annotations

from typing import Callable, Iterable, List, Sequence, Set

from domain.mod_identity import canonical_key, canonical_package_for_mod
from domain.priority_rules import profile_to_ui_order, ui_to_profile_order


PRESET_CATEGORY_MAP = {
    "map_bottom": (
        "map", "map_addon", "map_mod", "addon_map",
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


def _mod_match(mod: object, rules: Sequence) -> bool:
    manifest = getattr(mod, "manifest", None)
    name = (
        (getattr(manifest, "display_name", "") if manifest is not None else "")
        or getattr(mod, "display_title", "")
        or getattr(mod, "mod_id", "")
        or (str(mod) if isinstance(mod, str) else "")
    ).lower()
    categories = getattr(manifest, "categories", ()) if manifest is not None else ()
    category_text = ",".join(categories or ()).lower()
    for rule in rules:
        if isinstance(rule, tuple) and len(rule) == 2:
            keyword, target = rule
            if target == "name" and keyword in name:
                return True
            if target == "category" and keyword in category_text:
                return True
        elif isinstance(rule, str) and (rule in category_text or rule in name):
            return True
    return False


def build_worklist(
    active_mods: Iterable[str] | None,
    all_package_names: Iterable[str] | None,
    *,
    resolve_mod: Callable[[str], object | None],
) -> List[dict]:
    """Build the complete UI worklist from Profile low-to-high entries."""
    result: List[dict] = []
    represented_mods: set[int] = set()
    represented_keys: set[str] = set()
    enabled_count = 0

    def add_entry(package_name: str, enabled: bool, mod: object | None) -> None:
        nonlocal enabled_count
        package_name = str(package_name or "").strip()
        if not package_name:
            return
        key = canonical_key(package_name)
        identity = id(mod) if mod is not None else None
        if identity is not None and identity in represented_mods:
            return
        if key and key in represented_keys:
            return
        if identity is not None:
            represented_mods.add(identity)
        if key:
            represented_keys.add(key)
        result.append({
            "package_name": package_name,
            "enabled": bool(enabled),
            "order": enabled_count if enabled else -1,
            "priority_index": enabled_count if enabled else None,
            "mod": mod,
        })
        if enabled:
            enabled_count += 1

    for package_name in profile_to_ui_order(active_mods):
        add_entry(package_name, True, resolve_mod(package_name))

    normalized_packages = sorted({str(p).strip() for p in (all_package_names or []) if str(p).strip()})
    for package_name in normalized_packages:
        mod = resolve_mod(package_name)
        canonical = canonical_package_for_mod(mod) if mod is not None else package_name
        add_entry(canonical or package_name, False, mod)
    return result


def rebuild_from_active(
    current_worklist: List[dict] | None,
    new_active_entries: Iterable[object] | None,
    *,
    known_mods: Iterable[object] = (),
    resolve_mod: Callable[[str], object | None] | None = None,
) -> List[dict]:
    """Rebuild a UI worklist from Profile low-to-high active entries."""
    current_worklist = current_worklist or []
    active_keys: List[str] = []
    for entry in new_active_entries or []:
        if isinstance(entry, dict):
            key = (entry.get("package_name") or entry.get("mod_id") or "").strip()
        else:
            key = str(entry).strip()
        if key:
            active_keys.append(key)

    deduped: List[str] = []
    seen_keys: set[str] = set()
    for key in active_keys:
        normalized = canonical_key(key)
        if not normalized or normalized in seen_keys:
            continue
        seen_keys.add(normalized)
        deduped.append(key)
    active_keys = profile_to_ui_order(deduped)
    active_rank = {
        canonical_key(key): index
        for index, key in enumerate(active_keys)
        if canonical_key(key)
    }

    new_worklist: List[dict] = []
    for row in current_worklist:
        copied = dict(row)
        package_name = str(copied.get("package_name") or "").strip()
        package_key = canonical_key(package_name)
        if package_key and package_key in active_rank:
            copied["enabled"] = True
            copied["order"] = active_rank[package_key]
            copied["priority_index"] = active_rank[package_key]
        else:
            copied["enabled"] = False
            copied["order"] = -1
            copied["priority_index"] = None
        new_worklist.append(copied)

    represented = {
        canonical_key(str(row.get("package_name") or "").strip())
        for row in new_worklist
        if canonical_key(str(row.get("package_name") or "").strip())
    }
    known_index = {}
    for mod in known_mods or ():
        for key in (getattr(mod, "package_name", None), getattr(mod, "mod_id", None)):
            if key:
                known_index[str(key).strip()] = mod

    resolver = resolve_mod or (lambda key: known_index.get(key))
    for key in active_keys:
        normalized = canonical_key(key)
        if not normalized or normalized in represented:
            continue
        mod = resolver(key)
        new_worklist.append({
            "mod": mod,
            "package_name": key,
            "display_title": (getattr(mod, "display_title", None) or key) if mod else key,
            "enabled": True,
            "order": active_rank[normalized],
            "priority_index": active_rank[normalized],
            "source": "",
            "size_mb": None,
            "compatible_versions": "",
        })
        represented.add(normalized)

    def row_key(row: dict):
        package_name = str(row.get("package_name") or "").strip()
        normalized = canonical_key(package_name)
        if normalized in active_rank:
            return (0, active_rank[normalized], package_name.casefold())
        return (1, 0, package_name)

    new_worklist.sort(key=row_key)
    return new_worklist


def worklist_to_active(worklist: List[dict]) -> List[str]:
    active: List[str] = []
    for row in worklist:
        if not row.get("enabled"):
            continue
        package_name = str(row.get("package_name") or "").strip()
        if not package_name:
            continue
        mod = row.get("mod")
        if mod is not None and getattr(mod, "package_type", "") == "workshop" and "|" not in package_name:
            title = str(getattr(mod, "display_title", "") or "").strip()
            if title and not title.isdigit():
                package_name = f"{package_name}|{title}"
        active.append(package_name)
    return active


def worklist_to_profile_active(worklist: List[dict]) -> List[str]:
    return ui_to_profile_order(worklist_to_active(worklist))


def batch_toggle(worklist: List[dict], indices: Sequence[int], action: str = "toggle") -> List[dict]:
    new = [dict(row) for row in worklist]
    for index in indices:
        if index < 0 or index >= len(new):
            continue
        row = new[index]
        if action == "enable":
            row["enabled"] = True
        elif action == "disable":
            row["enabled"] = False
        else:
            row["enabled"] = not row["enabled"]
    enabled = [row for row in new if row["enabled"]]
    disabled = [row for row in new if not row["enabled"]]
    for index, row in enumerate(enabled):
        row["order"] = index
        row["priority_index"] = index
    for row in disabled:
        row["order"] = -1
        row["priority_index"] = None
    return enabled + disabled


def _renumber(worklist: List[dict]) -> List[dict]:
    out = [dict(row) for row in worklist]
    order = 0
    for row in out:
        if row["enabled"]:
            row["order"] = order
            row["priority_index"] = order
            order += 1
        else:
            row["order"] = -1
            row["priority_index"] = None
    return out


def reorder_before(
    worklist: List[dict],
    indices: Sequence[int],
    target_before_index: int,
    scope_enabled_only: bool = True,
) -> List[dict]:
    new = [dict(row) for row in worklist]
    enabled = (
        [(index, row) for index, row in enumerate(new) if row["enabled"]]
        if scope_enabled_only else list(enumerate(new))
    )
    sub_to_global = [global_index for global_index, _ in enabled]
    global_to_sub = {global_index: sub_index for sub_index, global_index in enumerate(sub_to_global)}
    to_move = sorted(global_to_sub[index] for index in indices if index in global_to_sub)
    target_sub = global_to_sub.get(target_before_index)
    if not to_move:
        return new
    sub_entries = [row for _, row in enabled]
    moving = [sub_entries[index] for index in to_move]
    moved_set = set(to_move)
    remain = [row for index, row in enumerate(sub_entries) if index not in moved_set]
    if target_sub is None:
        merged = remain + moving
    else:
        remain_pos = target_sub - sum(1 for index in to_move if index < target_sub)
        merged = remain[:remain_pos] + moving + remain[remain_pos:]
    if scope_enabled_only:
        for row, (global_index, _) in zip(merged, enabled):
            new[global_index] = row
        return _renumber(new)
    disabled = [row for row in new if not row["enabled"]]
    result = []
    for index, row in enumerate(merged):
        copied = dict(row)
        copied["order"] = index
        copied["priority_index"] = index
        result.append(copied)
    for row in disabled:
        copied = dict(row)
        copied["order"] = -1
        copied["priority_index"] = None
        result.append(copied)
    return result


def move_up(worklist: List[dict], indices: Sequence[int], steps: int = 1) -> List[dict]:
    if not indices or steps <= 0:
        return _renumber(worklist)
    enabled = [(index, row) for index, row in enumerate(worklist) if row["enabled"]]
    global_to_sub = {global_index: sub_index for sub_index, (global_index, _) in enumerate(enabled)}
    sub_ids = sorted(global_to_sub[index] for index in indices if index in global_to_sub)
    if not sub_ids:
        return _renumber(worklist)
    first_sub = sub_ids[0]
    target_sub = max(0, first_sub - steps)
    if target_sub == first_sub:
        return _renumber(worklist)
    return reorder_before(worklist, indices, enabled[target_sub][0])


def move_down(worklist: List[dict], indices: Sequence[int], steps: int = 1) -> List[dict]:
    if not indices or steps <= 0:
        return _renumber(worklist)
    enabled = [(index, row) for index, row in enumerate(worklist) if row["enabled"]]
    global_to_sub = {global_index: sub_index for sub_index, (global_index, _) in enumerate(enabled)}
    sub_ids = sorted(global_to_sub[index] for index in indices if index in global_to_sub)
    if not sub_ids:
        return _renumber(worklist)
    last_sub = sub_ids[-1]
    target_sub = last_sub + 1 + steps
    if target_sub >= len(enabled):
        return move_bottom(worklist, list(indices))
    if target_sub == last_sub + 1:
        return _renumber(worklist)
    return reorder_before(worklist, indices, enabled[target_sub][0])


def move_top(worklist: List[dict], indices: Sequence[int]) -> List[dict]:
    first_enabled = next((index for index, row in enumerate(worklist) if row["enabled"]), None)
    if first_enabled is None:
        return _renumber(worklist)
    return reorder_before(worklist, indices, first_enabled)


def move_bottom(worklist: List[dict], indices: Sequence[int]) -> List[dict]:
    enabled = [index for index, row in enumerate(worklist) if row["enabled"]]
    if not enabled:
        return _renumber(worklist)
    index_set = set(indices)
    moving = [worklist[index] for index in enabled if index in index_set]
    remain = [worklist[index] for index in enabled if index not in index_set]
    new_enabled = remain + moving
    result = list(worklist)
    for global_index, row in zip(enabled, new_enabled):
        result[global_index] = row
    return _renumber(result)


def apply_preset(worklist: List[dict]) -> List[dict]:
    enabled = [dict(row) for row in worklist if row["enabled"]]
    disabled = [dict(row) for row in worklist if not row["enabled"]]
    bottom: List[dict] = []
    middle: List[dict] = []
    top: List[dict] = []
    for row in enabled:
        mod = row.get("mod")
        target = mod if mod is not None else str(row.get("package_name") or "")
        if _mod_match(target, PRESET_CATEGORY_MAP["map_bottom"]):
            bottom.append(row)
        elif _mod_match(target, PRESET_CATEGORY_MAP["function_top"]):
            top.append(row)
        else:
            middle.append(row)
    ordered = top + middle + bottom
    for index, row in enumerate(ordered):
        row["order"] = index
        row["priority_index"] = index
    for row in disabled:
        row["order"] = -1
        row["priority_index"] = None
    return ordered + disabled


def indices_for_package_set(worklist: List[dict], package_names: Set[str]) -> List[int]:
    if not package_names:
        return []
    index = {}
    for row_index, row in enumerate(worklist):
        if row.get("enabled"):
            package_name = row.get("package_name")
            if package_name:
                index.setdefault(package_name, []).append(row_index)
    result: List[int] = []
    for package_name in package_names:
        result.extend(index.get(package_name, ()))
    return sorted(result)


def move_up_by_package_set(worklist: List[dict], package_names: Set[str], steps: int = 1) -> List[dict]:
    return move_up(worklist, indices_for_package_set(worklist, package_names), steps=steps) if package_names else worklist


def move_down_by_package_set(worklist: List[dict], package_names: Set[str], steps: int = 1) -> List[dict]:
    return move_down(worklist, indices_for_package_set(worklist, package_names), steps=steps) if package_names else worklist


def move_top_by_package_set(worklist: List[dict], package_names: Set[str]) -> List[dict]:
    return move_top(worklist, indices_for_package_set(worklist, package_names)) if package_names else worklist


def move_bottom_by_package_set(worklist: List[dict], package_names: Set[str]) -> List[dict]:
    return move_bottom(worklist, indices_for_package_set(worklist, package_names)) if package_names else worklist
