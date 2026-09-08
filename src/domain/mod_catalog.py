"""Pure Mod catalog and duplicate-resolution rules.

The catalog owns identity-level decisions only.  Filesystem discovery,
manifest parsing and UI rendering remain outside this module.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from .mod_identity import canonical_key, mod_identity_aliases, profile_entry_aliases


class ModCatalog:
    """Stable ordered collection of discovered Mod objects.

    Discovery order is meaningful: the scanner adds local packages first and
    Workshop packages second, so an exact local/Workshop duplicate keeps the
    local record while retaining the Workshop path as auxiliary metadata.
    """

    def __init__(self, mods: Iterable[object] = ()):
        self._mods: List[object] = []
        self._identity_index: Dict[str, object] = {}
        for mod in mods:
            self.add(mod)

    @property
    def mods(self) -> List[object]:
        return list(self._mods)

    def add(self, mod: object) -> bool:
        """Add *mod* if new, otherwise merge duplicate source metadata.

        Returns ``True`` when a new catalog entry was added and ``False`` for
        a duplicate.  A duplicate never replaces the first discovered Mod.
        """
        keys = mod_identity_aliases(mod)
        existing = self._find_by_keys(keys)
        if existing is None:
            self._mods.append(mod)
            for key in keys:
                self._identity_index.setdefault(key, mod)
            return True
        self._merge_duplicate(existing, mod)
        return False

    def resolve(self, value: object) -> Optional[object]:
        """Resolve a profile/package alias to a catalog Mod object."""
        for alias in profile_entry_aliases(value):
            resolved = self._identity_index.get(canonical_key(alias))
            if resolved is not None:
                return resolved
        return None

    def index(self) -> Dict[str, object]:
        """Return a copy of the strong identity index."""
        return dict(self._identity_index)

    def _find_by_keys(self, keys: Iterable[str]) -> Optional[object]:
        for key in keys:
            existing = self._identity_index.get(key)
            if existing is not None:
                return existing
        return None

    @staticmethod
    def _merge_duplicate(existing: object, incoming: object) -> None:
        """Preserve the first record and attach duplicate source metadata."""
        existing_type = str(getattr(existing, "package_type", "") or "")
        incoming_type = str(getattr(incoming, "package_type", "") or "")
        existing_path = str(getattr(existing, "package_path", "") or "")
        incoming_path = str(getattr(incoming, "package_path", "") or "")

        # Keep local discovery as the primary record, matching historical UI
        # behavior.  Retain the Workshop path for localization and diagnostics.
        if incoming_type == "workshop" and existing_type != "workshop":
            setattr(existing, "_workshop_path", incoming_path)
            setattr(existing, "_has_workshop_dup", True)
        elif existing_type == "workshop" and incoming_type != "workshop":
            setattr(existing, "_local_path", incoming_path)
            setattr(existing, "_has_local_dup", True)
        elif incoming_path and incoming_path != existing_path:
            duplicates = list(getattr(existing, "_duplicate_paths", ()) or ())
            if incoming_path not in duplicates:
                duplicates.append(incoming_path)
            setattr(existing, "_duplicate_paths", duplicates)
