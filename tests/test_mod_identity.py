from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.models import Mod, ModManifest  # noqa: E402
from domain.mod_identity import (  # noqa: E402
    aliases_match,
    canonical_key,
    canonical_package_for_mod,
    legacy_workshop_id,
    mod_aliases,
    profile_entry_aliases,
)
from services.priority_service import PriorityService  # noqa: E402


def _workshop_mod() -> Mod:
    return Mod(
        mod_id="1254665283_workshop",
        package_path="H:/workshop/1254665283_workshop",
        package_type="workshop",
        manifest=ModManifest(
            package_name="1254665283",
            display_name="No Damage",
        ),
    )


class ModIdentityTests(unittest.TestCase):
    def test_canonical_key_strips_only_storage_suffix_and_case(self):
        self.assertEqual("my_mod", canonical_key("My_Mod_COPY2|Saved Title"))
        self.assertEqual("my_mod", canonical_key("MY_MOD_local"))
        self.assertEqual("my_mod", canonical_key("my_mod_workshop"))


    def test_profile_aliases_include_package_title_and_legacy_workshop_id(self):
        aliases = profile_entry_aliases("mod_workshop_package.000000004AC8AC43|No Damage")
        self.assertIn("mod_workshop_package.000000004ac8ac43", aliases)
        self.assertIn("1254665283", aliases)
        self.assertIn("no damage", aliases)
        self.assertEqual("1254665283", legacy_workshop_id("mod_workshop_package.000000004AC8AC43"))


    def test_scanned_mod_aliases_and_persisted_package_are_consistent(self):
        mod = _workshop_mod()
        aliases = mod_aliases(mod)
        self.assertIn("1254665283", aliases)
        self.assertIn("no damage", aliases)
        self.assertEqual("1254665283_workshop", canonical_package_for_mod(mod))
        self.assertTrue(aliases_match("1254665283|No Damage", mod))


    def test_priority_service_resolves_aliases_case_insensitively(self):
        mod = _workshop_mod()
        service = PriorityService([mod])
        self.assertIs(mod, service._resolve_mod("1254665283|NO DAMAGE"))
        self.assertIs(mod, service._resolve_mod("mod_workshop_package.000000004AC8AC43|No Damage"))

    def test_rebuild_from_active_matches_aliases_without_duplicate_rows(self):
        mod = _workshop_mod()
        service = PriorityService([mod])
        current = service.build_worklist(["1254665283|No Damage"], ["1254665283"])
        rebuilt = service.rebuild_from_active(
            service,
            current,
            ["1254665283_workshop|NO DAMAGE"],
        )
        enabled = [row for row in rebuilt if row.get("enabled")]
        self.assertEqual(1, len(enabled))
        self.assertEqual(0, enabled[0]["priority_index"])
        self.assertIs(mod, enabled[0]["mod"])

    def test_rebuild_deduplicates_duplicate_profile_aliases(self):
        service = PriorityService([])
        current = service.build_worklist(["A"], ["A"])
        rebuilt = service.rebuild_from_active(service, current, ["A", "a_copy1"])
        enabled = [row for row in rebuilt if row.get("enabled")]
        self.assertEqual(1, len(enabled))
        self.assertEqual(0, enabled[0]["priority_index"])


if __name__ == "__main__":
    unittest.main()
