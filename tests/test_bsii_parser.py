from __future__ import annotations

import struct
import unittest
from pathlib import Path
from typing import Any, Iterable, Mapping

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from domain.bsii import BSIIParseError, parse_bsii  # noqa: E402
from services.save_editor_service import decrypt_scsc  # noqa: E402


_CHAR_TABLE = "0123456789abcdefghijklmnopqrstuvwxyz_"


def _encoded_string(value: str) -> bytes:
    number = 0
    for index, char in enumerate(value):
        digit = _CHAR_TABLE.index(char) + 1
        number += digit * (38 ** index)
    return struct.pack("<Q", number)


def _string(value: str) -> bytes:
    raw = value.encode("utf-8")
    return struct.pack("<I", len(raw)) + raw


def _id(parts: Iterable[str]) -> bytes:
    parts = tuple(parts)
    return bytes([len(parts)]) + b"".join(_encoded_string(part) for part in parts)


def _array(values: Iterable[Any], encoder) -> bytes:
    values = tuple(values)
    return struct.pack("<I", len(values)) + b"".join(encoder(value) for value in values)


def _encode_value(type_id: int, value: Any, version: int) -> bytes:
    if type_id == 0x01:
        return _string(value)
    if type_id == 0x02:
        return _array(value, _string)
    if type_id == 0x03:
        return _encoded_string(value)
    if type_id == 0x04:
        return _array(value, _encoded_string)
    if type_id == 0x05:
        return struct.pack("<f", value)
    if type_id == 0x06:
        return _array(value, lambda item: struct.pack("<f", item))
    if type_id == 0x07:
        return struct.pack("<2f", *value)
    if type_id == 0x09:
        return struct.pack("<3f", *value)
    if type_id == 0x19:
        width = 8 if version >= 2 else 7
        assert len(value) == width
        return struct.pack(f"<{width}f", *value)
    if type_id == 0x25:
        return struct.pack("<i", value)
    if type_id == 0x27:
        return struct.pack("<I", value)
    if type_id == 0x28:
        return _array(value, lambda item: struct.pack("<I", item))
    if type_id == 0x29:
        return struct.pack("<h", value)
    if type_id == 0x2B:
        return struct.pack("<H", value)
    if type_id == 0x31:
        return struct.pack("<q", value)
    if type_id == 0x33:
        return struct.pack("<Q", value)
    if type_id == 0x35:
        return bytes([int(value)])
    if type_id in (0x39, 0x3B, 0x3D):
        return _id(value)
    if type_id in (0x3A, 0x3C, 0x3E):
        return _array(value, _id)
    if type_id == 0x37:
        return struct.pack("<I", value)
    raise AssertionError(f"fixture encoder does not cover type 0x{type_id:02x}")


def _fixture(
    *,
    version: int,
    fields: list[tuple[str, int, Any]],
    values: Mapping[str, Any],
    structure_id: int = 7,
) -> tuple[bytes, dict[str, bytes]]:
    data = bytearray(b"BSII" + struct.pack("<I", version))
    data += struct.pack("<I", 0)  # structure-definition block
    data += bytes([1]) + struct.pack("<I", structure_id) + _string("demo")
    for name, type_id, enum_values in fields:
        data += struct.pack("<I", type_id) + _string(name)
        if type_id == 0x37:
            enum_values = dict(enum_values)
            data += struct.pack("<I", len(enum_values))
            for ordinal, label in enum_values.items():
                data += struct.pack("<I", ordinal) + _string(label)
    data += struct.pack("<I", 0)  # end of field definitions

    data += struct.pack("<I", structure_id) + _id(("demo", "object"))
    encoded_values: dict[str, bytes] = {}
    for name, type_id, _ in fields:
        encoded_values[name] = _encode_value(type_id, values[name], version)
        data += encoded_values[name]
    return bytes(data), encoded_values


class BSIIParserTests(unittest.TestCase):
    def test_versions_and_version_specific_vector_width(self):
        for version, width in ((1, 7), (2, 8), (3, 8)):
            data, _ = _fixture(
                version=version,
                fields=[("placement", 0x19, None)],
                values={"placement": tuple(float(i) for i in range(width))},
            )
            parsed = parse_bsii(data)
            self.assertEqual(version, parsed.version)
            self.assertEqual(tuple(float(i) for i in range(width)), parsed.objects[0].get_one("placement").value)

    def test_types_enum_arrays_ids_and_exact_offsets(self):
        fields = [
            ("label", 0x01, None),
            ("names", 0x02, None),
            ("encoded", 0x03, None),
            ("wear", 0x05, None),
            ("experience_points", 0x27, None),
            ("money_account", 0x31, None),
            ("enabled", 0x35, None),
            ("policy", 0x37, {1: "balanced", 2: "adr"}),
            ("target", 0x39, None),
            ("wheels_wear", 0x06, None),
        ]
        values = {
            "label": "hello",
            "names": ["one", "two"],
            "encoded": "truck",
            "wear": 0.25,
            "experience_points": 279375,
            "money_account": 1253729,
            "enabled": True,
            "policy": 2,
            "target": ("economy", "player"),
            "wheels_wear": [0.1, 0.2],
        }
        data, encoded_values = _fixture(version=3, fields=fields, values=values)
        parsed = parse_bsii(data)
        obj = parsed.objects[0]

        self.assertEqual("hello", obj.get_one("label").value)
        self.assertEqual(["one", "two"], obj.get_one("names").value)
        self.assertEqual("truck", obj.get_one("encoded").value)
        self.assertEqual(279375, obj.get_one("experience_points").value)
        self.assertEqual(1253729, obj.get_one("money_account").value)
        self.assertEqual("adr", obj.get_one("policy").value)
        self.assertEqual("economy.player", obj.get_one("target").value.value)

        for name, raw in encoded_values.items():
            field = obj.get_one(name)
            self.assertIsNotNone(field)
            self.assertEqual(raw, data[field.offset:field.offset + field.size], name)

        self.assertEqual(4, obj.get_one("experience_points").size)
        self.assertEqual(8, obj.get_one("money_account").size)
        self.assertEqual(4, obj.get_one("wear").size)
        self.assertEqual(12, obj.get_one("wheels_wear").size)

    def test_truncated_or_invalid_stream_is_rejected(self):
        data, _ = _fixture(
            version=3,
            fields=[("value", 0x27, None)],
            values={"value": 42},
        )
        with self.assertRaises(BSIIParseError):
            parse_bsii(data[:-1])
        with self.assertRaises(BSIIParseError):
            parse_bsii(b"NOPE" + data[4:])
        with self.assertRaises(BSIIParseError):
            parse_bsii(b"BSII" + struct.pack("<I", 99))

    def test_real_game_save_golden_fields_when_available(self):
        documents = Path.home() / "Documents" / "Euro Truck Simulator 2"
        candidates = sorted(documents.glob("profiles/*/save/*/game.sii"))
        if not candidates:
            self.skipTest("real ETS2 game.sii is not available")

        parsed = parse_bsii(decrypt_scsc(candidates[0].read_bytes()))
        self.assertEqual(3, parsed.version)
        self.assertEqual(46, len(parsed.definitions))
        self.assertGreater(len(parsed.objects), 1000)

        experience = parsed.find_fields("experience_points", structure_names=["economy"])
        money = parsed.find_fields("money_account", structure_names=["bank"])
        wear = parsed.find_fields("engine_wear", structure_names=["vehicle"])
        self.assertEqual([(279375, 0x27, 4)], [(v.value, v.type_id, v.size) for _, v in experience])
        self.assertEqual([(1253729, 0x31, 8)], [(v.value, v.type_id, v.size) for _, v in money])
        self.assertGreater(len(wear), 0)
        self.assertTrue(all(v.type_id == 0x05 and v.size == 4 for _, v in wear))


if __name__ == "__main__":
    unittest.main()
