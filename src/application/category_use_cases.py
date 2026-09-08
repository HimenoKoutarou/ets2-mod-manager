"""Application facade for Mod categories and known-Mod tracking."""
from __future__ import annotations

from typing import Dict, Iterable, Optional, Protocol, Set

from .contracts import CategoryMutationResult, CategoryState


class CategoryPort(Protocol):
    def all_folders(self): ...
    def stats(self): ...
    def get_category(self, mod_id: str): ...
    def mods_in_category(self, category_key: str): ...
    def create_folder(self, name: str): ...
    def rename_folder(self, old: str, new: str): ...
    def delete_folder(self, name: str): ...
    def set_category(self, mod_id: str, category: str): ...
    def set_categories_bulk(self, mapping: Dict[str, str]): ...
    def touch_and_detect_new(self, scanned_ids: Iterable[str], name_hints=None): ...
    def save(self, force: bool = False): ...


class CategoryUseCases:
    def __init__(self, repository: CategoryPort):
        self._repository = repository

    @property
    def repository(self) -> CategoryPort:
        return self._repository

    def snapshot(self) -> CategoryState:
        folders = tuple(str(x) for x in (self._repository.all_folders() or []) if x)
        stats = dict(self._repository.stats() or {})
        return CategoryState(folders=folders, stats=stats)

    def all_folders(self):
        return list(self.snapshot().folders)

    def get_category(self, mod_id: str) -> str:
        return str(self._repository.get_category(mod_id) or "")

    def mods_in_category(self, category_key: str) -> Set[str]:
        return set(self._repository.mods_in_category(category_key) or set())

    def create_folder(self, name: str) -> CategoryMutationResult:
        ok = bool(self._repository.create_folder(name))
        return CategoryMutationResult("create_folder", ok, error=None if ok else "folder_exists_or_empty")

    def rename_folder(self, old: str, new: str) -> CategoryMutationResult:
        affected = int(self._repository.rename_folder(old, new) or 0)
        conflict = affected == -1
        ok = not conflict and (affected > 0 or old == new or old not in self.all_folders())
        return CategoryMutationResult(
            "rename_folder", ok, affected_mods=max(affected, 0), conflict=conflict,
            error="folder_exists" if conflict else None,
        )

    def delete_folder(self, name: str) -> CategoryMutationResult:
        affected = int(self._repository.delete_folder(name) or 0)
        return CategoryMutationResult("delete_folder", affected >= 0, affected_mods=max(affected, 0))

    def set_category(self, mod_id: str, category: str) -> CategoryMutationResult:
        self._repository.set_category(mod_id, category)
        return CategoryMutationResult("set_category", True, affected_mods=1 if mod_id else 0)

    def set_categories_bulk(self, mapping: Dict[str, str]) -> CategoryMutationResult:
        clean = {str(k): str(v or "") for k, v in (mapping or {}).items() if k}
        self._repository.set_categories_bulk(clean)
        return CategoryMutationResult("set_categories_bulk", True, affected_mods=len(clean))

    def touch_and_detect_new(self, scanned_ids: Iterable[str], name_hints: Optional[Dict[str, str]] = None):
        return self._repository.touch_and_detect_new(scanned_ids, name_hints=name_hints)

    def save(self, force: bool = False) -> None:
        self._repository.save(force=force)
