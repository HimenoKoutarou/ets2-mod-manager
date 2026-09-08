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


def canonical_key(value: object) -> str:
    """Return the stable lookup key for a package/profile alias.

    The key intentionally removes only known storage suffixes.  Display
    titles and Workshop legacy keys remain valid aliases, but are never
    treated as distinct package identities merely because of case or a
    ``_copyN`` suffix.
    """
    package = profile_entry_package(value)
    return _PROFILE_SUFFIX_RE.sub("", package).casefold()


def legacy_workshop_id(value: object) -> str:
    """Convert an old hexadecimal Workshop key to its decimal ID."""
    package = profile_entry_package(value)
    match = _LEGACY_WORKSHOP_RE.fullmatch(package)
    if not match:
        return ""
    try:
        return str(int(match.group(1), 16))
    except ValueError:
        return ""


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
        legacy = legacy_workshop_id(package)
        if legacy:
            aliases.add(legacy)


def profile_entry_aliases(value: object) -> Set[str]:
    """Return normalized lookup aliases for a Profile active_mods entry."""
    aliases: Set[str] = set()
    _add_alias(aliases, value)
    title = profile_entry_title(value)
    if title:
        aliases.add(title.casefold())
    return aliases


def canonical_package_for_mod(mod) -> str:
    """Return the package spelling that should be persisted for *mod*."""
    if mod is None:
        return ""
    manifest = getattr(mod, "manifest", None)
    package = str(getattr(manifest, "package_name", "") or "").strip() if manifest else ""
    mod_id = str(getattr(mod, "mod_id", "") or "").strip()
    if getattr(mod, "package_type", "") == "workshop" and mod_id:
        return mod_id
    return package or mod_id


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
