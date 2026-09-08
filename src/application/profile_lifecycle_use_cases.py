"""Application boundary for non-Mod Profile lifecycle operations.

The compatibility services still implement filesystem and SII details.  This
facade owns the write invariants and keeps presentation code from calling
those services directly, matching the future C# Application layer boundary.
"""
from __future__ import annotations

from typing import Protocol

from domain.profile_rules import require_writable_profile
from .profile_use_cases import GameState, require_game_closed


class ProfileLifecycleRepository(Protocol):
    def copy_profile(self, profile, new_display_name: str = "", new_company_name: str = ""): ...
    def delete_profile(self, profile, backup_first: bool = True) -> None: ...


class ProfileSettingsPort(Protocol):
    def rename_profile(self, profile, new_profile_name: str = "", new_company_name: str = ""): ...
    def copy_profile_settings(
        self,
        source,
        destination,
        copy_active_mods: bool = True,
        copy_controls: bool = False,
    ) -> None: ...


class BackupStore(Protocol):
    def backup(self, source, tag: str = "auto"): ...


class ProfileLifecycleUseCases:
    """Coordinate Profile backup, copy, delete, rename, and settings copy."""

    def __init__(
        self,
        repository: ProfileLifecycleRepository,
        game_state: GameState,
        backup_store: BackupStore,
        settings: ProfileSettingsPort | None = None,
    ):
        self._repository = repository
        self._game_state = game_state
        self._backup_store = backup_store
        self._settings = settings

    def with_settings(self, settings: ProfileSettingsPort) -> "ProfileLifecycleUseCases":
        return ProfileLifecycleUseCases(
            self._repository,
            self._game_state,
            self._backup_store,
            settings,
        )

    def backup_profile(self, profile, *, tag: str = "manual"):
        path = getattr(profile, "profile_sii", None)
        if path is None:
            raise ValueError("Profile path is not configured")
        return self._backup_store.backup(path, tag=tag)

    def copy_profile(self, profile, new_display_name: str = "", new_company_name: str = ""):
        require_writable_profile(profile)
        require_game_closed(self._game_state, "复制 Profile")
        return self._repository.copy_profile(
            profile,
            str(new_display_name or ""),
            str(new_company_name or ""),
        )

    def delete_profile(self, profile, *, backup_first: bool = True) -> None:
        require_writable_profile(profile)
        require_game_closed(self._game_state, "删除 Profile")
        self._repository.delete_profile(profile, backup_first=bool(backup_first))

    def rename_profile(self, profile, new_profile_name: str = "", new_company_name: str = ""):
        require_writable_profile(profile)
        require_game_closed(self._game_state, "重命名 Profile")
        settings = self._require_settings()
        return settings.rename_profile(
            profile,
            str(new_profile_name or ""),
            str(new_company_name or ""),
        )

    def copy_profile_settings(
        self,
        source,
        destination,
        copy_active_mods: bool = True,
        copy_controls: bool = False,
    ) -> None:
        require_writable_profile(destination)
        require_game_closed(self._game_state, "复制 Profile 设置")
        settings = self._require_settings()
        settings.copy_profile_settings(
            source,
            destination,
            bool(copy_active_mods),
            bool(copy_controls),
        )

    def _require_settings(self) -> ProfileSettingsPort:
        if self._settings is None:
            raise RuntimeError("Profile settings adapter is not configured")
        return self._settings
