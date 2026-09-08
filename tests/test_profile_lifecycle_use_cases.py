"""Tests for M7 Profile lifecycle Application boundaries."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from application.profile_lifecycle_use_cases import ProfileLifecycleUseCases
from services.profile_service import ProfileInfo


class _Repository:
    def __init__(self):
        self.calls = []

    def copy_profile(self, profile, new_display_name="", new_company_name=""):
        self.calls.append(("copy", profile, new_display_name, new_company_name))
        return "copied-profile"

    def delete_profile(self, profile, backup_first=True):
        self.calls.append(("delete", profile, backup_first))


class _Settings:
    def __init__(self):
        self.calls = []

    def rename_profile(self, profile, new_profile_name="", new_company_name=""):
        self.calls.append(("rename", profile, new_profile_name, new_company_name))
        return "profile.sii"

    def copy_profile_settings(self, source, destination, copy_active_mods=True, copy_controls=False):
        self.calls.append(("settings", source, destination, copy_active_mods, copy_controls))


class _Backup:
    def __init__(self):
        self.calls = []

    def backup(self, source, tag="auto"):
        self.calls.append((source, tag))
        return "backup.bak"


class _GameState:
    def __init__(self, running=False): self.running = running
    def is_running(self): return self.running


class ProfileLifecycleUseCasesTests(unittest.TestCase):
    def setUp(self):
        self.repository = _Repository()
        self.settings = _Settings()
        self.backup = _Backup()
        self.profile = ProfileInfo("p", "local", Path("p"), Path("p/profile.sii"))
        self.use_cases = ProfileLifecycleUseCases(
            self.repository, _GameState(), self.backup, self.settings
        )

    def test_backup_is_read_only_and_delegated(self):
        self.assertEqual("backup.bak", self.use_cases.backup_profile(self.profile, tag="manual"))
        self.assertEqual([(self.profile.profile_sii, "manual")], self.backup.calls)

    def test_copy_and_delete_delegate_after_guards(self):
        result = self.use_cases.copy_profile(self.profile, "Copy", "Company")
        self.assertEqual("copied-profile", result)
        self.use_cases.delete_profile(self.profile, backup_first=True)
        self.assertEqual("copy", self.repository.calls[0][0])
        self.assertEqual(("delete", self.profile, True), self.repository.calls[1])

    def test_rename_and_settings_copy_use_settings_port(self):
        self.use_cases.rename_profile(self.profile, "Renamed", "Company")
        self.use_cases.copy_profile_settings(self.profile, self.profile, True, True)
        self.assertEqual("rename", self.settings.calls[0][0])
        self.assertEqual(("settings", self.profile, self.profile, True, True), self.settings.calls[1])

    def test_cloud_and_running_game_are_rejected_before_adapters(self):
        cloud = ProfileInfo("c", "cloud", Path("c"), Path("c/profile.sii"))
        with self.assertRaises(PermissionError):
            self.use_cases.delete_profile(cloud)
        running = ProfileLifecycleUseCases(
            self.repository, _GameState(True), self.backup, self.settings
        )
        with self.assertRaises(RuntimeError):
            running.copy_profile(self.profile)
        self.assertEqual([], self.repository.calls)


if __name__ == "__main__":
    unittest.main()
