from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.models import Mod, ModManifest  # noqa: E402
from core.mod_scanner import ModScanner  # noqa: E402
from domain.mod_catalog import ModCatalog  # noqa: E402


def _mod(
    mod_id: str,
    package_name: str,
    package_type: str,
    package_path: str,
    title: str = "",
) -> Mod:
    return Mod(
        mod_id=mod_id,
        package_path=package_path,
        package_type=package_type,
        manifest=ModManifest(package_name=package_name, display_name=title),
    )


class ModCatalogTests(unittest.TestCase):
    def test_local_record_wins_and_workshop_path_is_retained(self):
        local = _mod("123", "123", "scs", "C:/mods/123.scs", "No Damage")
        workshop = _mod("123", "123", "workshop", "C:/workshop/123", "No Damage")
        catalog = ModCatalog()

        self.assertTrue(catalog.add(local))
        self.assertFalse(catalog.add(workshop))
        self.assertEqual([local], catalog.mods)
        self.assertEqual("C:/workshop/123", local._workshop_path)
        self.assertTrue(local._has_workshop_dup)
        self.assertIs(local, catalog.resolve("123|No Damage"))

    def test_case_and_copy_aliases_are_one_identity(self):
        first = _mod("My_Mod", "My_Mod", "scs", "C:/mods/My_Mod.scs")
        second = _mod("my_mod_copy2", "my_mod_copy2", "zip", "C:/mods/my_mod_copy2.zip")
        catalog = ModCatalog([first])

        self.assertFalse(catalog.add(second))
        self.assertEqual([first], catalog.mods)

    def test_same_display_title_different_packages_are_not_merged(self):
        first = _mod("a", "a", "scs", "C:/mods/a.scs", "Shared Title")
        second = _mod("b", "b", "scs", "C:/mods/b.scs", "Shared Title")
        catalog = ModCatalog([first, second])

        self.assertEqual([first, second], catalog.mods)
        self.assertIs(first, catalog.resolve("a"))
        self.assertIs(second, catalog.resolve("b"))

    def test_catalog_order_is_discovery_order(self):
        mods = [_mod(str(i), str(i), "scs", f"C:/mods/{i}.scs") for i in range(3)]
        self.assertEqual(mods, ModCatalog(mods).mods)


class ModScannerCatalogIntegrationTests(unittest.TestCase):
    def test_scan_uses_catalog_for_local_workshop_duplicates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            local = root / "mod"
            workshop = root / "workshop"
            local.mkdir()
            workshop.mkdir()
            (local / "123.scs").write_bytes(b"not-a-real-scs")
            (workshop / "123").mkdir()

            scanner = ModScanner(local, workshop)
            mods, _ = scanner.scan(skip_manifest_parse=True)

            self.assertEqual(1, len(mods))
            self.assertEqual("123", mods[0].mod_id)
            self.assertEqual("scs", mods[0].package_type)
            self.assertEqual(str(workshop / "123"), mods[0]._workshop_path)
            self.assertTrue(mods[0]._has_workshop_dup)


if __name__ == "__main__":
    unittest.main()
