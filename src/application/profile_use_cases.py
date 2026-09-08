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


class ProfileUseCases:
    """Small application boundary for Profile mutations.

    This is intentionally additive: the legacy service still owns the actual
    file format work until the repository adapter is migrated.
    """

    def __init__(self, repository: ProfileRepository, game_state: GameState):
        self._repository = repository
        self._game_state = game_state

    def replace_active_mods(self, profile, new_mods: List[str], *, verify: bool = True):
        require_writable_profile(profile)
        if self._game_state.is_running():
            raise RuntimeError("检测到 ETS2 或 ATS 正在运行，请完全退出游戏后再修改 Profile。")
        return self._repository.set_active_mods(profile, list(new_mods), verify=verify)
