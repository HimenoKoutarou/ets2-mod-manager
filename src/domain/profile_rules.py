"""Pure Profile writeability rules."""
from __future__ import annotations


WRITABLE_PROFILE_LOCATIONS = frozenset({"local", "test"})


def is_writable_profile_location(location: object) -> bool:
    """Return whether a profile location may be mutated.

    ``test`` is intentionally retained for repository fixtures; production
    profiles are writable only when their location is exactly ``local``.
    """
    return str(location or "") in WRITABLE_PROFILE_LOCATIONS


def require_writable_profile(profile) -> None:
    """Reject Cloud/Steam profiles at the domain boundary."""
    if profile is None or not is_writable_profile_location(
        getattr(profile, "location", "")
    ):
        location = getattr(profile, "location", "unknown") if profile is not None else "unknown"
        raise PermissionError(
            f"只允许修改本地存档，当前存档来源为 {location}。请切换到“本地”存档后再操作。"
        )
