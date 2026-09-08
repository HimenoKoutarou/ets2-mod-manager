"""Shared golden contracts for Python, C#, and Rust migration tests."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from application.contract_serialization import to_transport_value
from application.contracts import ProgressEvent, ProgressStatus
from domain.mod_identity import canonical_key, legacy_workshop_id, profile_entry_aliases
from domain import mod_priority_rules


GOLDEN_PATH = Path(__file__).resolve().parent / "golden" / "migration_contracts_v1.json"


def _worklist_projection(rows):
    keys = ("package_name", "enabled", "order", "priority_index")
    return [{key: row.get(key) for key in keys} for row in rows]


class MigrationGoldenContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        if cls.golden.get("contract_version") != 1:
            raise AssertionError("Unsupported migration contract version")

    def test_mod_identity_cases(self):
        for case in self.golden["identity_cases"]:
            value = case["input"]
            self.assertEqual(case["canonical_key"], canonical_key(value))
            self.assertEqual(case["legacy_workshop_id"], legacy_workshop_id(value))
            self.assertEqual(case["aliases"], sorted(profile_entry_aliases(value)))

    def test_priority_and_profile_order_case(self):
        case = self.golden["priority_case"]
        worklist = mod_priority_rules.build_worklist(
            case["profile_active_mods"],
            case["all_package_names"],
            resolve_mod=lambda _key: None,
        )
        self.assertEqual(case["built_worklist"], _worklist_projection(worklist))
        self.assertEqual(case["ui_active_mods"], mod_priority_rules.worklist_to_active(worklist))
        self.assertEqual(
            case["roundtrip_profile_active_mods"],
            mod_priority_rules.worklist_to_profile_active(worklist),
        )
        self.assertEqual(
            case["disable_index_1"],
            _worklist_projection(mod_priority_rules.batch_toggle(worklist, [1], "disable")),
        )
        self.assertEqual(
            case["move_index_0_to_bottom"],
            _worklist_projection(mod_priority_rules.move_bottom(worklist, [0])),
        )

    def test_application_dto_transport_shape(self):
        expected = self.golden["dto_case"]
        event = ProgressEvent(
            operation=expected["operation"],
            phase=expected["phase"],
            current=expected["current"],
            total=expected["total"],
            item=expected["item"],
            status=ProgressStatus.RUNNING,
            message=expected["message"],
        )
        self.assertEqual(expected, to_transport_value(event))


if __name__ == "__main__":
    unittest.main()
