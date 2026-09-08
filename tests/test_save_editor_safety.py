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
    SaveSlotInfo,
    _atomic_write_bytes,
    encrypt_scsc,
)
from services.profile_service import ProfileInfo  # noqa: E402


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


class _BackupStub:
    def backup(self, path, tag=""):
        return None


class _ProfileServiceStub:
    backup = _BackupStub()

    class _GameState:
        def is_running(self):
            return False

    game_state = _GameState()

    @staticmethod
    def ensure_local_profile(profile):
        if profile.location not in ("local", "test"):
            raise PermissionError("local profiles only")


class SaveSlotCopyTests(unittest.TestCase):
    def _make_slot(self, root: Path, location: str = "local"):
        profile_dir = root / "profile"
        slot_dir = profile_dir / "save" / "1"
        slot_dir.mkdir(parents=True)
        profile_sii = profile_dir / "profile.sii"
        profile_sii.write_text("SiiNunit\n{\n}\n", encoding="utf-8")
        profile = ProfileInfo(
            profile_id="test-profile",
            location=location,
            folder=profile_dir,
            profile_sii=profile_sii,
        )
        game_sii = slot_dir / "game.sii"
        game_sii.write_bytes(b"game-save-payload")
        info_plain = (
            'SiiNunit\n{\n'
            'save_container : .save {\n'
            ' name: "Original Save"\n'
            ' time: 100\n'
            ' file_time: 100\n'
            '}\n}\n'
        ).encode("utf-8")
        info_sii = slot_dir / "info.sii"
        info_sii.write_bytes(encrypt_scsc(info_plain))
        (slot_dir / "preview.tga").write_bytes(b"preview-image")
        slot = SaveSlotInfo(
            profile=profile,
            slot_name="1",
            slot_path=slot_dir,
            game_sii=game_sii,
            info_sii=info_sii,
        )
        return profile, slot

    def test_copy_save_slot_creates_numbered_copy_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as td:
            _, slot = self._make_slot(Path(td))
            source_info = slot.info_sii.read_bytes()
            service = SaveEditorService(_ProfileServiceStub())

            copied = service.copy_save_slot(slot, "测试副本")

            self.assertEqual("2", copied.slot_name)
            self.assertEqual(b"game-save-payload", copied.game_sii.read_bytes())
            self.assertEqual(b"preview-image", (copied.slot_path / "preview.tga").read_bytes())
            copied_info = copied.info_sii.read_bytes()
            self.assertTrue(copied_info.startswith(b"SiiNunit"))
            self.assertIn(b'name: "\\xE6\\xB5\\x8B\\xE8\\xAF\\x95\\xE5\\x89\\xAF\\xE6\\x9C\\xAC"', copied_info)
            self.assertNotIn(b"file_time: 100", copied_info)
            self.assertEqual(source_info, slot.info_sii.read_bytes())
            self.assertFalse(any(p.name.startswith(".2_copy_") for p in copied.slot_path.parent.iterdir()))

            copied_again = service.copy_save_slot(slot, "第二个副本")
            self.assertEqual("3", copied_again.slot_name)

    def test_copy_save_slot_rejects_cloud_profile(self):
        with tempfile.TemporaryDirectory() as td:
            _, slot = self._make_slot(Path(td), location="cloud")
            service = SaveEditorService(_ProfileServiceStub())
            with self.assertRaises(PermissionError):
                service.copy_save_slot(slot, "Cloud Copy")

    def test_copy_save_slot_rejects_running_game(self):
        with tempfile.TemporaryDirectory() as td:
            _, slot = self._make_slot(Path(td))
            class RunningGame:
                def is_running(self):
                    return True

            service = SaveEditorService(_ProfileServiceStub(), game_state=RunningGame())
            with self.assertRaises(RuntimeError):
                service.copy_save_slot(slot, "Running Copy")
            self.assertFalse((slot.slot_path.parent / "2").exists())

    def test_save_game_sii_rejects_running_game_at_service_boundary(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            profile_dir = root / "profile"
            slot_dir = profile_dir / "save" / "1"
            slot_dir.mkdir(parents=True)
            profile_sii = profile_dir / "profile.sii"
            profile_sii.write_text("SiiNunit\n{\n}\n", encoding="utf-8")
            profile = ProfileInfo("p", "local", profile_dir, profile_sii)
            game_sii = slot_dir / "game.sii"
            game_sii.write_bytes(b"old")
            slot = SaveSlotInfo(profile, "1", slot_dir, game_sii, slot_dir / "info.sii")

            class RunningGame:
                def is_running(self):
                    return True

            service = SaveEditorService(_ProfileServiceStub(), game_state=RunningGame())
            with self.assertRaises(RuntimeError):
                service._save_game_sii(slot, b"new")
            self.assertEqual(b"old", game_sii.read_bytes())


if __name__ == "__main__":
    unittest.main()
