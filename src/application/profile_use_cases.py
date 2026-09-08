"""Technology-neutral Profile use-case contracts.

The existing ProfileService remains the compatibility adapter for now. New
UI code can depend on these protocols while the repository implementation is
migrated incrementally.
"""
from __future__ import annotations

from typing import List, Protocol

from domain.profile_rules import require_writable_profile


class ProfileRepository(Protocol):
    def get_active_mods(self, profile) -> List[str]: ...

    def set_active_mods(self, profile, new_mods: List[str], *, verify: bool = False): ...


class GameState(Protocol):
    def is_running(self) -> bool: ...


def require_game_closed(game_state: GameState, action: str = "修改存档") -> None:
    """Enforce the write-side invariant shared by all Profile/save use cases."""
    if game_state.is_running():
        raise RuntimeError(
            f"检测到 ETS2 或 ATS 正在运行，请完全退出游戏后再{action}。"
        )


class ProfileUseCases:
    """Small application boundary for Profile mutations.

    This is intentionally additive: the legacy service still owns the actual
    file format work until the repository adapter is migrated.
    """

    def __init__(self, repository: ProfileRepository, game_state: GameState):
        self._repository = repository
        self._game_state = game_state

    def read_active_mods(self, profile) -> List[str]:
        """Read Profile ``active_mods`` through the repository port.

        Reads are allowed for Local, Steam, and Cloud profiles.  The
        repository remains responsible for decryption, caching, and parsing;
        the application boundary normalizes the result to a detached list so
        callers cannot mutate repository-owned state accidentally.
        """
        return list(self._repository.get_active_mods(profile) or [])

    def replace_active_mods(self, profile, new_mods: List[str], *, verify: bool = True):
        require_writable_profile(profile)
        require_game_closed(self._game_state, "修改 Profile")
        return self._repository.set_active_mods(profile, list(new_mods), verify=verify)
