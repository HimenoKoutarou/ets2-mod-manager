from __future__ import annotations

import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.save_editor_service import (  # noqa: E402
    SaveEditorService,
    _atomic_write_bytes,
)


def _field(name: str, type_byte: int, payload: bytes) -> bytes:
    raw = name.encode("ascii")
    return struct.pack("<I", len(raw)) + raw + bytes([type_byte]) + b"\x00\x00\x00" + payload


class SaveEditorSafetyTests(unittest.TestCase):
    def setUp(self):
        self.service = SaveEditorService(profile_service=None)

    def test_schema_continuation_is_not_treated_as_float_value(self):
        next_name = b"transmission_wear"
        data = _field(
            "engine_wear",
            self.service.T_F32,
            struct.pack("<I", len(next_name)) + next_name + b"\x05\x00\x00\x00",
        )
        self.assertEqual([], self.service.find_float_field_value(data, "engine_wear"))

    def test_safe_inline_float_has_one_exact_offset(self):
        value = 123.5
        data = _field("money", self.service.T_F32, struct.pack("<f", value) + b"\xff\xff\xff\xff")
        hits = self.service.find_float_field_value(data, "money")
        self.assertEqual(1, len(hits))
        self.assertAlmostEqual(value, hits[0][1])
        self.assertEqual(struct.pack("<f", value), data[hits[0][0]:hits[0][0] + 4])

    def test_integer_lookup_is_disabled_instead_of_guessing(self):
        data = _field("level", 0x09, struct.pack("<I", 42) + b"\xff\xff\xff\xff")
        self.assertEqual([], self.service.find_u32_field_value(data, "level"))

    def test_atomic_write_replaces_file_without_temp_residue(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "game.sii"
            path.write_bytes(b"old")
            _atomic_write_bytes(path, b"new-content")
            self.assertEqual(b"new-content", path.read_bytes())
            leftovers = [p for p in path.parent.iterdir() if p.name != path.name]
            self.assertEqual([], leftovers)


if __name__ == "__main__":
    unittest.main()
