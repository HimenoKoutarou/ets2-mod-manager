"""Application boundary tests for the core Mod-management workflow."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from application.mod_management_use_cases import ModManagementUseCases
from services.priority_service import PriorityService


class ModManagementUseCasesTests(unittest.TestCase):
    def setUp(self):
        self.priority = PriorityService([])
        self.use_cases = ModManagementUseCases(self.priority)

    def _worklist(self, names=("A", "B", "C", "D")):
        # Profile order is low -> high; the application exposes high -> low.
        return self.use_cases.build_worklist(
            list(names), list(names)
        )

    def test_build_and_profile_order_roundtrip(self):
        worklist = self._worklist(("LOW", "MID", "HIGH"))
        self.assertEqual(
            [row["package_name"] for row in worklist if row["enabled"]],
            ["HIGH", "MID", "LOW"],
        )
        self.assertEqual(
            self.use_cases.worklist_to_profile_active(worklist),
            ["LOW", "MID", "HIGH"],
        )

    def test_batch_toggle_isolated_at_application_boundary(self):
        worklist = self._worklist()
        changed = self.use_cases.batch_toggle(worklist, [0], "disable")
        self.assertEqual(changed[0]["package_name"], "C")
        disabled = next(row for row in changed if row["package_name"] == "D")
        self.assertFalse(disabled["enabled"])
        self.assertEqual(disabled["order"], -1)
        self.assertIsNone(disabled["priority_index"])
        self.assertTrue(all(row["enabled"] for row in changed[:3]))

    def test_move_and_category_move_share_one_entry_point(self):
        worklist = self._worklist()
        moved = self.use_cases.move(worklist, [1], "top")
        self.assertEqual(
            [row["package_name"] for row in moved if row["enabled"]],
            ["C", "D", "B", "A"],
        )
        category_moved = self.use_cases.move_category(
            worklist, {"B", "C"}, "bottom"
        )
        self.assertEqual(
            [row["package_name"] for row in category_moved if row["enabled"]],
            ["D", "A", "C", "B"],
        )

    def test_delta_zero_keeps_priority_adapter_normalization(self):
        worklist = self._worklist()
        for row in worklist:
            row["order"] = 999
            row["priority_index"] = 999
        normalized = self.use_cases.move_delta(worklist, [0], 0)
        self.assertEqual(
            [row["order"] for row in normalized if row["enabled"]],
            [0, 1, 2, 3],
        )

    def test_rebuild_from_active_uses_profile_order(self):
        current = self._worklist(("A", "B", "C"))
        rebuilt = self.use_cases.rebuild_from_active(current, ["A", "B", "C"])
        self.assertEqual(
            [row["package_name"] for row in rebuilt if row["enabled"]],
            ["C", "B", "A"],
        )

    def test_invalid_move_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            self.use_cases.move(self._worklist(), [0], "sideways")
        with self.assertRaises(ValueError):
            self.use_cases.move_category(self._worklist(), {"A"}, "sideways")


if __name__ == "__main__":
    unittest.main()
