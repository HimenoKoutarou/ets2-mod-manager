"""Application boundary tests for Profile active_mods reads and writes."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from application.profile_use_cases import ProfileUseCases
from services.profile_service import ProfileInfo


class _Repository:
    def __init__(self):
        self.active = ["low", "high"]
        self.calls = []

    def get_active_mods(self, profile):
        self.calls.append(("read", profile))
        return self.active

    def set_active_mods(self, profile, new_mods, *, verify=False):
        self.calls.append(("write", profile, list(new_mods), verify))
        self.active = list(new_mods)
        return "profile.sii"


class _GameState:
    def __init__(self, running=False):
        self.running = running

    def is_running(self):
        return self.running


class ProfileUseCasesTests(unittest.TestCase):
    def setUp(self):
        self.repository = _Repository()
        self.profile = ProfileInfo("p", "local", Path("."), Path("profile.sii"))

    def test_read_returns_detached_list_and_preserves_repository_order(self):
        use_cases = ProfileUseCases(self.repository, _GameState())
        active = use_cases.read_active_mods(self.profile)
        self.assertEqual(active, ["low", "high"])
        active.append("new")
        self.assertEqual(self.repository.active, ["low", "high"])
        self.assertEqual(self.repository.calls[0][0], "read")

    def test_write_copies_input_and_forwards_verify_flag(self):
        use_cases = ProfileUseCases(self.repository, _GameState())
        requested = ["high", "low"]
        result = use_cases.replace_active_mods(self.profile, requested, verify=False)
        requested.append("mutated-after-call")
        self.assertEqual(result, "profile.sii")
        self.assertEqual(self.repository.active, ["high", "low"])
        self.assertEqual(self.repository.calls[-1][3], False)

    def test_write_rejects_cloud_profile_before_repository_call(self):
        use_cases = ProfileUseCases(self.repository, _GameState())
        cloud = ProfileInfo("cloud", "cloud", Path("."), Path("profile.sii"))
        with self.assertRaises(PermissionError):
            use_cases.replace_active_mods(cloud, ["mod"])
        self.assertEqual(self.repository.calls, [])

    def test_write_rejects_running_game_before_repository_call(self):
        use_cases = ProfileUseCases(self.repository, _GameState(running=True))
        with self.assertRaises(RuntimeError):
            use_cases.replace_active_mods(self.profile, ["mod"])
        self.assertEqual(self.repository.calls, [])


if __name__ == "__main__":
    unittest.main()
