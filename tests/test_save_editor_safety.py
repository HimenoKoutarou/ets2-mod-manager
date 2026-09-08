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
    _combine_unlock_results,
    _atomic_write_bytes,
    decrypt_scsc,
    encrypt_scsc,
)
from services.profile_service import ProfileInfo, _escape_profile_str_for_sii  # noqa: E402
from domain.bsii import parse_bsii  # noqa: E402


def _field(name: str, type_byte: int, payload: bytes) -> bytes:
    raw = name.encode("ascii")
    return struct.pack("<I", len(raw)) + raw + bytes([type_byte]) + b"\x00\x00\x00" + payload


def _encoded_string(value: str) -> bytes:
    table = "0123456789abcdefghijklmnopqrstuvwxyz_"
    number = sum((table.index(char) + 1) * (38 ** index) for index, char in enumerate(value))
    return struct.pack("<Q", number)


def _bsii_id(value: str) -> bytes:
    return b"\x01" + _encoded_string(value)


def _definition(structure_id: int, name: str, fields: list[tuple[str, int]]) -> bytes:
    raw_name = name.encode("ascii")
    data = bytearray(struct.pack("<I", 0) + b"\x01" + struct.pack("<I", structure_id))
    data += struct.pack("<I", len(raw_name)) + raw_name
    for field_name, type_id in fields:
        raw_field = field_name.encode("ascii")
        data += struct.pack("<I", type_id)
        data += struct.pack("<I", len(raw_field)) + raw_field
    data += struct.pack("<I", 0)
    return bytes(data)


def _canonical_bsii(experience: int = 279375, money: int = 1253729) -> bytes:
    data = bytearray(b"BSII" + struct.pack("<I", 3))
    data += _definition(1, "economy", [("bank", 0x39), ("experience_points", 0x27)])
    data += _definition(2, "bank", [("money_account", 0x31)])

    data += struct.pack("<I", 1) + _bsii_id("economy")
    data += _bsii_id("bank") + struct.pack("<I", experience)
    data += struct.pack("<I", 2) + _bsii_id("bank")
    data += struct.pack("<q", money)
    return bytes(data)


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

    def test_profile_text_replacement_preserves_escaped_quotes(self):
        old = ' profile_name: "旧\\"名\\\\路径"\n company_name: "旧公司"\n'
        escaped = _escape_profile_str_for_sii('新"名\\路径')
        replaced = SaveEditorService._replace_text_field(old, "profile_name", escaped)
        self.assertIn(f' profile_name: "{escaped}"', replaced)
        self.assertIn(' company_name: "旧公司"', replaced)

    def test_combined_unlock_requires_both_operations(self):
        self.assertEqual((True, ""), _combine_unlock_results(True, True))
        self.assertEqual((False, ""), _combine_unlock_results(True, False))
        self.assertEqual((False, ""), _combine_unlock_results(False, True))
        self.assertEqual((False, ""), _combine_unlock_results(False, False))


class _BackupStub:
    def backup(self, path, tag=""):
        return None


class _ProfileServiceStub:
    backup = _BackupStub()
    sii_decrypt_exe = None

    class _GameState:
        def is_running(self):
            return False

    game_state = _GameState()

    @staticmethod
    def ensure_local_profile(profile):
        if profile.location not in ("local", "test"):
            raise PermissionError("local profiles only")


class _ProfileSettingsStub(_ProfileServiceStub):
    def __init__(self):
        self.active = {}
        self.fail_next_write = False

    def get_active_mods(self, profile):
        return list(self.active.get(profile.profile_id, []))

    def set_active_mods(self, profile, new_mods, *, verify=False):
        if self.fail_next_write:
            self.fail_next_write = False
            raise RuntimeError("simulated active_mods write failure")
        self.active[profile.profile_id] = list(new_mods)
        return profile.profile_sii


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
            self.assertTrue(copied_info.startswith(b"ScsC"))
            copied_plain = decrypt_scsc(copied_info)
            self.assertIn(b'name: "\\xE6\\xB5\\x8B\\xE8\\xAF\\x95\\xE5\\x89\\xAF\\xE6\\x9C\\xAC"', copied_plain)
            self.assertNotIn(b"file_time: 100", copied_plain)
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

    def test_copy_profile_settings_rolls_back_controls_when_mod_write_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_dir = root / "src"
            dst_dir = root / "dst"
            src_dir.mkdir()
            dst_dir.mkdir()
            src_sii = src_dir / "profile.sii"
            dst_sii = dst_dir / "profile.sii"
            src_sii.write_text("SiiNunit\n", encoding="utf-8")
            dst_sii.write_text("SiiNunit\n", encoding="utf-8")
            (src_dir / "controls.sii").write_bytes(b"new-controls")
            dst_controls = dst_dir / "controls.sii"
            dst_controls.write_bytes(b"old-controls")
            src = ProfileInfo("src", "local", src_dir, src_sii)
            dst = ProfileInfo("dst", "local", dst_dir, dst_sii)
            profile_service = _ProfileSettingsStub()
            profile_service.active = {"src": ["new"], "dst": ["old"]}
            profile_service.fail_next_write = True

            service = SaveEditorService(profile_service)
            with self.assertRaises(RuntimeError):
                service.copy_profile_settings(src, dst, True, True)

            self.assertEqual(["old"], profile_service.active["dst"])
            self.assertEqual(b"old-controls", dst_controls.read_bytes())


class StructuredStatsEditTests(unittest.TestCase):
    def _make_structured_slot(self, root: Path, experience=279375, money=1253729):
        profile_dir = root / "profile"
        slot_dir = profile_dir / "save" / "1"
        slot_dir.mkdir(parents=True)
        profile_sii = profile_dir / "profile.sii"
        profile_sii.write_text("SiiNunit\n{\n}\n", encoding="utf-8")
        profile = ProfileInfo("p", "local", profile_dir, profile_sii)
        game_sii = slot_dir / "game.sii"
        game_sii.write_bytes(_canonical_bsii(experience, money))
        return SaveSlotInfo(profile, "1", slot_dir, game_sii, slot_dir / "info.sii")

    def test_reads_and_writes_canonical_integer_fields(self):
        with tempfile.TemporaryDirectory() as td:
            slot = self._make_structured_slot(Path(td))
            service = SaveEditorService(_ProfileServiceStub())

            self.assertEqual(1253729.0, service.read_current_money(slot))
            self.assertEqual(279375.0, service.read_current_xp(slot))
            self.assertTrue(service.set_player_money(slot, 2000000, 1253729))
            self.assertTrue(service.set_player_experience(slot, 6000, 279375))

            parsed = parse_bsii(slot.game_sii.read_bytes())
            self.assertEqual(2000000, parsed.find_fields("money_account", structure_names=["bank"])[0][1].value)
            self.assertEqual(6000, parsed.find_fields("experience_points", structure_names=["economy"])[0][1].value)
            self.assertEqual(3, parsed.version)

    def test_wrong_hint_and_noop_do_not_write(self):
        with tempfile.TemporaryDirectory() as td:
            slot = self._make_structured_slot(Path(td))
            service = SaveEditorService(_ProfileServiceStub())
            original = slot.game_sii.read_bytes()

            self.assertFalse(service.set_player_money(slot, 2000000, 1))
            self.assertEqual(original, slot.game_sii.read_bytes())
            self.assertFalse(service.set_player_experience(slot, 279375, 279375))
            self.assertEqual(original, slot.game_sii.read_bytes())

    def test_level_edit_updates_derived_experience(self):
        with tempfile.TemporaryDirectory() as td:
            slot = self._make_structured_slot(Path(td), experience=1000)
            service = SaveEditorService(_ProfileServiceStub())
            self.assertTrue(service.set_player_level(slot, 4, current_level_hint=2))
            self.assertEqual(6000.0, service.read_current_xp(slot))
            self.assertEqual(4, service.read_current_level(slot))

    def test_integer_edits_reject_non_bsii_data(self):
        with tempfile.TemporaryDirectory() as td:
            slot = self._make_structured_slot(Path(td))
            slot.game_sii.write_bytes(b"not-a-save")
            service = SaveEditorService(_ProfileServiceStub())
            self.assertFalse(service.set_player_money(slot, 1))
            self.assertFalse(service.set_player_experience(slot, 1))


if __name__ == "__main__":
    unittest.main()
