"""Application boundary for the core Mod-management workflow.

The UI owns selection, dialogs, and rendering.  This module owns the
technology-neutral orchestration of a worklist and delegates the existing
priority algorithms through a small port.  The port keeps the migration
incremental: ``PriorityService`` remains the compatibility adapter until the
priority implementation is moved below the application boundary.
"""
from __future__ import annotations

from typing import List, Protocol, Sequence, Set


class ModPriorityPort(Protocol):
    """Operations required by the Mod-management application use case."""

    def build_worklist(self, active_mods: List[str], all_package_names: List[str]) -> List[dict]: ...

    def batch_toggle(self, worklist: List[dict], indices: Sequence[int], action: str = "toggle") -> List[dict]: ...

    def move_up(self, worklist: List[dict], indices: Sequence[int], steps: int = 1) -> List[dict]: ...

    def move_down(self, worklist: List[dict], indices: Sequence[int], steps: int = 1) -> List[dict]: ...

    def move_top(self, worklist: List[dict], indices: Sequence[int]) -> List[dict]: ...

    def move_bottom(self, worklist: List[dict], indices: Sequence[int]) -> List[dict]: ...

    def move_up_by_package_set(self, worklist: List[dict], pkg_set: Set[str], steps: int = 1) -> List[dict]: ...

    def move_down_by_package_set(self, worklist: List[dict], pkg_set: Set[str], steps: int = 1) -> List[dict]: ...

    def move_top_by_package_set(self, worklist: List[dict], pkg_set: Set[str]) -> List[dict]: ...

    def move_bottom_by_package_set(self, worklist: List[dict], pkg_set: Set[str]) -> List[dict]: ...

    def apply_preset(self, worklist: List[dict]) -> List[dict]: ...

    def rebuild_from_active(self, current_svc, current_worklist: List[dict], new_active_entries) -> List[dict]: ...

    def worklist_to_active(self, worklist: List[dict]) -> List[str]: ...

    def worklist_to_profile_active(self, worklist: List[dict]) -> List[str]: ...


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

    def build_worklist(self, active_mods: List[str], all_package_names: List[str]) -> List[dict]:
        return self._priority.build_worklist(list(active_mods), list(all_package_names))

    def batch_toggle(self, worklist: List[dict], indices: Sequence[int], action: str = "toggle") -> List[dict]:
        return self._priority.batch_toggle(worklist, list(indices), action)

    def move(self, worklist: List[dict], indices: Sequence[int], kind: str, *, steps: int = 1) -> List[dict]:
        """Move selected enabled entries using the requested command."""
        operation = {
            "up": self._priority.move_up,
            "down": self._priority.move_down,
            "top": self._priority.move_top,
            "bottom": self._priority.move_bottom,
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
            "up": self._priority.move_up_by_package_set,
            "down": self._priority.move_down_by_package_set,
            "top": self._priority.move_top_by_package_set,
            "bottom": self._priority.move_bottom_by_package_set,
        }.get(kind)
        if operation is None:
            raise ValueError(f"未知 Mod 分类排序操作: {kind}")
        if kind in {"up", "down"}:
            return operation(worklist, set(package_names), steps=steps)
        return operation(worklist, set(package_names))

    def move_delta(self, worklist: List[dict], indices: Sequence[int], delta: int) -> List[dict]:
        """Move selected entries by a signed number of enabled positions."""
        if delta < 0:
            return self._priority.move_up(worklist, list(indices), steps=abs(delta))
        if delta > 0:
            return self._priority.move_down(worklist, list(indices), steps=delta)
        # PriorityService's movement methods also normalize order fields for a
        # no-op.  Preserve that behavior through the smallest available path.
        return self._priority.move_up(worklist, list(indices), steps=0)

    def move_category_delta(self, worklist: List[dict], package_names: Set[str], delta: int) -> List[dict]:
        """Move a category block by a signed number of enabled positions."""
        if delta < 0:
            return self._priority.move_up_by_package_set(worklist, set(package_names), steps=abs(delta))
        if delta > 0:
            return self._priority.move_down_by_package_set(worklist, set(package_names), steps=delta)
        return worklist

    def apply_preset(self, worklist: List[dict]) -> List[dict]:
        return self._priority.apply_preset(worklist)

    def rebuild_from_active(self, current_worklist: List[dict], new_active_entries) -> List[dict]:
        """Rebuild the in-memory projection from Profile ``active_mods`` order."""
        # ``rebuild_from_active`` is a classmethod on the legacy adapter and
        # therefore still accepts the adapter as its first argument.
        return self._priority.rebuild_from_active(self._priority, current_worklist, list(new_active_entries))

    def worklist_to_active(self, worklist: List[dict]) -> List[str]:
        return self._priority.worklist_to_active(worklist)

    def worklist_to_profile_active(self, worklist: List[dict]) -> List[str]:
        return self._priority.worklist_to_profile_active(worklist)
