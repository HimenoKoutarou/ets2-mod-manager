"""Direct tests for the service-free Domain priority rules."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from domain import mod_priority_rules as rules


class ModPriorityRulesTests(unittest.TestCase):
    def test_order_conversion_roundtrip(self):
        raw = ["low", "mid", "high"]
        ui = rules.profile_to_ui_order(raw)
        self.assertEqual(ui, ["high", "mid", "low"])
        self.assertEqual(rules.ui_to_profile_order(ui), raw)

    def test_build_worklist_uses_injected_resolver_and_deduplicates_aliases(self):
        mod = SimpleNamespace(mod_id="A", package_name="A", display_title="A")
        resolver = lambda key: mod if key.casefold() in {"a", "a_copy1"} else None
        worklist = rules.build_worklist(
            ["A_copy1"], ["A", "B"], resolve_mod=resolver
        )
        self.assertEqual([row["package_name"] for row in worklist], ["A_copy1", "B"])
        self.assertTrue(worklist[0]["enabled"])
        self.assertIs(worklist[0]["mod"], mod)

    def test_batch_toggle_and_category_move_are_pure(self):
        worklist = [
            {"package_name": "A", "enabled": True, "order": 0, "priority_index": 0},
            {"package_name": "B", "enabled": True, "order": 1, "priority_index": 1},
            {"package_name": "C", "enabled": False, "order": -1, "priority_index": None},
        ]
        original = [dict(row) for row in worklist]
        changed = rules.batch_toggle(worklist, [2], "enable")
        self.assertEqual([row["package_name"] for row in changed], ["A", "B", "C"])
        moved = rules.move_bottom_by_package_set(changed, {"A"})
        self.assertEqual([row["package_name"] for row in moved if row["enabled"]], ["B", "C", "A"])
        self.assertEqual(worklist, original)

    def test_preset_uses_duck_typed_metadata(self):
        def mod(name):
            return SimpleNamespace(
                mod_id=name,
                display_title=name,
                manifest=SimpleNamespace(display_name=name, categories=[]),
            )

        worklist = [
            {"package_name": "promods", "enabled": True, "mod": mod("promods")},
            {"package_name": "traffic_fix", "enabled": True, "mod": mod("traffic_fix")},
            {"package_name": "generic", "enabled": True, "mod": mod("generic")},
        ]
        result = rules.apply_preset(worklist)
        self.assertEqual(
            [row["package_name"] for row in result],
            ["traffic_fix", "generic", "promods"],
        )


if __name__ == "__main__":
    unittest.main()
