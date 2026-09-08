"""Canonical Mod identity and Profile active_mods alias rules."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Set


_PROFILE_SUFFIX_RE = re.compile(r"_(workshop|copy\d*|local)$", re.IGNORECASE)
_LEGACY_WORKSHOP_RE = re.compile(
    r"mod_workshop_package\.0*([0-9a-f]{1,8})$", re.IGNORECASE
)


def profile_entry_package(value: object) -> str:
    """Return the package portion of ``package|saved display title``."""
    return str(value or "").split("|", 1)[0].strip()


def profile_entry_title(value: object) -> str:
    """Return the optional saved display title from a Profile entry."""
    raw = str(value or "")
    return raw.split("|", 1)[1].strip() if "|" in raw else ""


def _add_alias(aliases: Set[str], value: object) -> None:
    text = str(value or "").strip()
    if not text:
        return
    aliases.add(text.casefold())
    package = profile_entry_package(text)
    if package:
        aliases.add(package.casefold())
        stripped = _PROFILE_SUFFIX_RE.sub("", package)
        aliases.add(stripped.casefold())
        legacy = _LEGACY_WORKSHOP_RE.fullmatch(package)
        if legacy:
            try:
                aliases.add(str(int(legacy.group(1), 16)))
            except ValueError:
                pass


def profile_entry_aliases(value: object) -> Set[str]:
    """Return normalized lookup aliases for a Profile active_mods entry."""
    aliases: Set[str] = set()
    _add_alias(aliases, value)
    title = profile_entry_title(value)
    if title:
        aliases.add(title.casefold())
    return aliases


def mod_aliases(mod) -> Set[str]:
    """Return aliases exposed by a scanned Mod object.

    The function deliberately uses duck typing so the domain layer does not
    depend on the concrete Mod dataclass or scanner implementation.
    """
    aliases: Set[str] = set()
    manifest = getattr(mod, "manifest", None)
    values: Iterable[object] = (
        getattr(mod, "mod_id", ""),
        getattr(mod, "display_title", ""),
        getattr(manifest, "package_name", "") if manifest else "",
        getattr(manifest, "display_name", "") if manifest else "",
    )
    for value in values:
        _add_alias(aliases, value)
    package_path = str(getattr(mod, "package_path", "") or "")
    if package_path:
        _add_alias(aliases, Path(package_path).stem)
    return aliases


def aliases_match(entry: object, mod) -> bool:
    """Return whether a Profile entry refers to the supplied Mod."""
    return bool(profile_entry_aliases(entry) & mod_aliases(mod))
