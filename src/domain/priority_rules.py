"""Priority order rules independent of UI and persistence implementations."""
from __future__ import annotations

from typing import Iterable, List


def profile_to_ui_order(entries: Iterable[str] | None) -> List[str]:
    """Convert profile load order (low -> high) to UI order (high -> low)."""
    return list(reversed(list(entries or [])))


def ui_to_profile_order(entries: Iterable[str] | None) -> List[str]:
    """Convert UI order (high -> low) to profile load order (low -> high)."""
    return list(reversed(list(entries or [])))
