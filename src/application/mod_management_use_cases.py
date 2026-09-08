"""Application boundary for the core Mod-management workflow.

The UI owns selection, dialogs, and rendering.  Pure worklist transformations
live in :mod:`domain.mod_priority_rules`; this module only orchestrates those
rules with the scanned-Mod resolver supplied by the infrastructure adapter.
"""
from __future__ import annotations

from typing import Callable, List, Protocol, Sequence, Set

from domain import mod_priority_rules as priority_rules


class ModPriorityPort(Protocol):
    """Mod metadata/resolution port used by Domain worklist rules."""

    known_mods: Sequence[object]

    def resolve_mod(self, package_name: str) -> object | None: ...


class ModManagementUseCases:
    """Coordinate Mod state changes without depending on Qt or persistence.

    Every method returns a new worklist when the underlying priority adapter
    does so.  Callers remain responsible for assigning the result and for
    rendering/marking the in-memory state dirty.
    """

    def __init__(self, priority: ModPriorityPort):
        self._priority = priority

    @property
    def priority(self) -> ModPriorityPort:
        """Expose the adapter identity so UI caches can refresh after rescans."""
        return self._priority

    def _resolve_mod(self) -> Callable[[str], object | None]:
        resolver = getattr(self._priority, "resolve_mod", None)
        if callable(resolver):
            return resolver
        # Compatibility with the current adapter while its public port is
        # being introduced.
        resolver = getattr(self._priority, "_resolve_mod", None)
        if callable(resolver):
            return resolver
        return lambda _package_name: None

    def build_worklist(self, active_mods: List[str], all_package_names: List[str]) -> List[dict]:
        return priority_rules.build_worklist(
            list(active_mods),
            list(all_package_names),
            resolve_mod=self._resolve_mod(),
        )

    def batch_toggle(self, worklist: List[dict], indices: Sequence[int], action: str = "toggle") -> List[dict]:
        return priority_rules.batch_toggle(worklist, list(indices), action)

    def move(self, worklist: List[dict], indices: Sequence[int], kind: str, *, steps: int = 1) -> List[dict]:
        """Move selected enabled entries using the requested command."""
        operation = {
            "up": priority_rules.move_up,
            "down": priority_rules.move_down,
            "top": priority_rules.move_top,
            "bottom": priority_rules.move_bottom,
        }.get(kind)
        if operation is None:
            raise ValueError(f"未知 Mod 排序操作: {kind}")
        if kind in {"up", "down"}:
            return operation(worklist, list(indices), steps=steps)
        return operation(worklist, list(indices))

    def move_category(
        self,
        worklist: List[dict],
        package_names: Set[str],
        kind: str,
        *,
        steps: int = 1,
    ) -> List[dict]:
        """Move a category/package block while preserving its internal order."""
        operation = {
            "up": priority_rules.move_up_by_package_set,
            "down": priority_rules.move_down_by_package_set,
            "top": priority_rules.move_top_by_package_set,
            "bottom": priority_rules.move_bottom_by_package_set,
        }.get(kind)
        if operation is None:
            raise ValueError(f"未知 Mod 分类排序操作: {kind}")
        if kind in {"up", "down"}:
            return operation(worklist, set(package_names), steps=steps)
        return operation(worklist, set(package_names))

    def move_delta(self, worklist: List[dict], indices: Sequence[int], delta: int) -> List[dict]:
        """Move selected entries by a signed number of enabled positions."""
        if delta < 0:
            return priority_rules.move_up(worklist, list(indices), steps=abs(delta))
        if delta > 0:
            return priority_rules.move_down(worklist, list(indices), steps=delta)
        # PriorityService's movement methods also normalize order fields for a
        # no-op.  Preserve that behavior through the smallest available path.
        return priority_rules.move_up(worklist, list(indices), steps=0)

    def move_category_delta(self, worklist: List[dict], package_names: Set[str], delta: int) -> List[dict]:
        """Move a category block by a signed number of enabled positions."""
        if delta < 0:
            return priority_rules.move_up_by_package_set(worklist, set(package_names), steps=abs(delta))
        if delta > 0:
            return priority_rules.move_down_by_package_set(worklist, set(package_names), steps=delta)
        return worklist

    def apply_preset(self, worklist: List[dict]) -> List[dict]:
        return priority_rules.apply_preset(worklist)

    def rebuild_from_active(self, current_worklist: List[dict], new_active_entries) -> List[dict]:
        """Rebuild the in-memory projection from Profile ``active_mods`` order."""
        return priority_rules.rebuild_from_active(
            current_worklist,
            list(new_active_entries),
            known_mods=getattr(self._priority, "known_mods", ()),
            resolve_mod=self._resolve_mod(),
        )

    def worklist_to_active(self, worklist: List[dict]) -> List[str]:
        return priority_rules.worklist_to_active(worklist)

    def worklist_to_profile_active(self, worklist: List[dict]) -> List[str]:
        return priority_rules.worklist_to_profile_active(worklist)
