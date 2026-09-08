"""Read-only BSII binary save parser.

The parser deliberately stops at decoding and offset discovery.  It does not
serialize arbitrary BSII objects, so callers cannot accidentally write a
schema byte as if it were a value.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple


class BSIIParseError(ValueError):
    """Raised when a BSII stream is truncated or structurally invalid."""


@dataclass(frozen=True)
class BSIIId:
    part_count: int
    address: int = 0
    value: str = ""


@dataclass(frozen=True)
class BSIIFieldDefinition:
    name: str
    type_id: int
    ordinal_values: Optional[Mapping[int, str]] = None


@dataclass(frozen=True)
class BSIIFieldValue:
    name: str
    type_id: int
    value: Any
    offset: int
    size: int


@dataclass(frozen=True)
class BSIIDefinition:
    structure_id: int
    name: str
    valid: bool
    fields: Tuple[BSIIFieldDefinition, ...] = ()


@dataclass
class BSIIObject:
    structure_id: int
    name: str
    object_id: BSIIId
    fields: Dict[str, List[BSIIFieldValue]] = field(default_factory=dict)

    def get_all(self, name: str) -> List[BSIIFieldValue]:
        return list(self.fields.get(name, ()))

    def get_one(self, name: str) -> Optional[BSIIFieldValue]:
        values = self.fields.get(name)
        return values[0] if values else None


@dataclass
class BSIIFile:
    version: int
    definitions: Dict[int, BSIIDefinition]
    objects: List[BSIIObject]

    def find_fields(
        self,
        name: str,
        *,
        structure_names: Optional[Iterable[str]] = None,
    ) -> List[Tuple[BSIIObject, BSIIFieldValue]]:
        allowed = set(structure_names) if structure_names is not None else None
        found: List[Tuple[BSIIObject, BSIIFieldValue]] = []
        for obj in self.objects:
            if allowed is not None and obj.name not in allowed:
                continue
            for value in obj.fields.get(name, ()):
                found.append((obj, value))
        return found


_CHAR_TABLE = "0123456789abcdefghijklmnopqrstuvwxyz_"


class _Reader:
    def __init__(self, data: bytes):
        self.data = memoryview(data)
        self.pos = 0

    def _need(self, size: int) -> None:
        if size < 0 or self.pos + size > len(self.data):
            raise BSIIParseError(
                f"truncated BSII at offset {self.pos}, need {size} bytes"
            )

    def u8(self) -> int:
        self._need(1)
        value = self.data[self.pos]
        self.pos += 1
        return int(value)

    def u16(self) -> int:
        self._need(2)
        value = struct.unpack_from("<H", self.data, self.pos)[0]
        self.pos += 2
        return value

    def u32(self) -> int:
        self._need(4)
        value = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return value

    def i16(self) -> int:
        self._need(2)
        value = struct.unpack_from("<h", self.data, self.pos)[0]
        self.pos += 2
        return value

    def i32(self) -> int:
        self._need(4)
        value = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return value

    def u64(self) -> int:
        self._need(8)
        value = struct.unpack_from("<Q", self.data, self.pos)[0]
        self.pos += 8
        return value

    def i64(self) -> int:
        self._need(8)
        value = struct.unpack_from("<q", self.data, self.pos)[0]
        self.pos += 8
        return value

    def f32(self) -> float:
        self._need(4)
        value = struct.unpack_from("<f", self.data, self.pos)[0]
        self.pos += 4
        return value

    def raw(self, size: int) -> bytes:
        self._need(size)
        value = self.data[self.pos:self.pos + size].tobytes()
        self.pos += size
        return value

    def string(self) -> str:
        size = self.u32()
        try:
            return self.raw(size).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BSIIParseError(f"invalid UTF-8 string at offset {self.pos - size}") from exc


def _decode_encoded_string(reader: _Reader) -> str:
    value = reader.u64()
    chars: List[str] = []
    while value:
        remainder = value % 38
        value //= 38
        index = remainder - 1
        if 0 <= index < len(_CHAR_TABLE):
            chars.append(_CHAR_TABLE[index])
    return "".join(chars)


def _decode_id(reader: _Reader) -> BSIIId:
    part_count = reader.u8()
    if part_count == 0xFF:
        address = reader.u64()
        raw = address.to_bytes(8, "little")
        parts: List[str] = []
        for index in range(0, len(raw), 2):
            part = f"{int.from_bytes(raw[index:index + 2], 'little'):x}".lstrip("0")
            parts.append(part)
        while parts and not parts[-1]:
            parts.pop()
        value = "_nameless." + ".".join(reversed(parts)) if parts else "_nameless"
        return BSIIId(part_count=part_count, address=address, value=value)
    parts = [_decode_encoded_string(reader) for _ in range(part_count)]
    return BSIIId(part_count=part_count, value=".".join(parts) if parts else "null")


def _array(reader: _Reader, decoder) -> List[Any]:
    return [decoder(reader) for _ in range(reader.u32())]


def _vector(reader: _Reader, count: int, decoder) -> Tuple[Any, ...]:
    return tuple(decoder(reader) for _ in range(count))


def _decode_value(reader: _Reader, type_id: int, ordinal_values: Mapping[int, str]) -> Any:
    if type_id == 0x01:
        return reader.string()
    if type_id == 0x02:
        return _array(reader, lambda r: r.string())
    if type_id == 0x03:
        return _decode_encoded_string(reader)
    if type_id == 0x04:
        return _array(reader, _decode_encoded_string)
    if type_id == 0x05:
        return reader.f32()
    if type_id == 0x06:
        return _array(reader, lambda r: r.f32())
    if type_id == 0x07:
        return _vector(reader, 2, lambda r: r.f32())
    if type_id == 0x08:
        return _array(reader, lambda r: _vector(r, 2, lambda rr: rr.f32()))
    if type_id == 0x09:
        return _vector(reader, 3, lambda r: r.f32())
    if type_id == 0x0A:
        return _array(reader, lambda r: _vector(r, 3, lambda rr: rr.f32()))
    if type_id == 0x11:
        return _vector(reader, 3, lambda r: r.i32())
    if type_id == 0x12:
        return _array(reader, lambda r: _vector(r, 3, lambda rr: rr.i32()))
    if type_id == 0x17:
        return _vector(reader, 4, lambda r: r.f32())
    if type_id == 0x18:
        return _array(reader, lambda r: _vector(r, 4, lambda rr: rr.f32()))
    if type_id == 0x19:
        return _vector(reader, 8 if reader.version >= 2 else 7, lambda r: r.f32())
    if type_id == 0x1A:
        return _array(
            reader,
            lambda r: _vector(r, 8 if r.version >= 2 else 7, lambda rr: rr.f32()),
        )
    if type_id == 0x25:
        return reader.i32()
    if type_id == 0x26:
        return _array(reader, lambda r: r.i32())
    if type_id in (0x27, 0x2F):
        return reader.u32()
    if type_id == 0x28:
        return _array(reader, lambda r: r.u32())
    if type_id == 0x29:
        return reader.i16()
    if type_id == 0x2A:
        return _array(reader, lambda r: r.i16())
    if type_id == 0x2B:
        return reader.u16()
    if type_id == 0x2C:
        return _array(reader, lambda r: r.u16())
    if type_id == 0x31:
        return reader.i64()
    if type_id == 0x32:
        return _array(reader, lambda r: r.i64())
    if type_id == 0x33:
        return reader.u64()
    if type_id == 0x34:
        return _array(reader, lambda r: r.u64())
    if type_id == 0x35:
        return bool(reader.u8())
    if type_id == 0x36:
        return _array(reader, lambda r: bool(r.u8()))
    if type_id == 0x37:
        return ordinal_values.get(reader.u32(), "")
    if type_id in (0x39, 0x3B, 0x3D):
        return _decode_id(reader)
    if type_id in (0x3A, 0x3C, 0x3E):
        return _array(reader, _decode_id)
    raise BSIIParseError(f"unsupported BSII type 0x{type_id:02x} at offset {reader.pos}")


def parse_bsii(data: bytes) -> BSIIFile:
    """Decode a BSII stream into definitions and object values."""
    reader = _Reader(data)
    if reader.raw(4) != b"BSII":
        raise BSIIParseError("not a BSII stream")
    version = reader.u32()
    if version not in (1, 2, 3):
        raise BSIIParseError(f"unsupported BSII version {version}")
    reader.version = version

    definitions: Dict[int, BSIIDefinition] = {}
    objects: List[BSIIObject] = []
    while reader.pos < len(reader.data):
        block_type = reader.u32()
        if block_type == 0:
            valid = bool(reader.u8())
            if not valid:
                continue
            structure_id = reader.u32()
            name = reader.string()
            fields: List[BSIIFieldDefinition] = []
            while True:
                type_id = reader.u32()
                if type_id == 0:
                    break
                field_name = reader.string()
                ordinal_values = None
                if type_id == 0x37:
                    count = reader.u32()
                    values: Dict[int, str] = {}
                    for _ in range(count):
                        ordinal = reader.u32()
                        values[ordinal] = reader.string()
                    ordinal_values = values
                fields.append(BSIIFieldDefinition(field_name, type_id, ordinal_values))
            definitions[structure_id] = BSIIDefinition(
                structure_id=structure_id,
                name=name,
                valid=True,
                fields=tuple(fields),
            )
            continue

        definition = definitions.get(block_type)
        if definition is None:
            raise BSIIParseError(
                f"object references unknown BSII structure {block_type} at offset {reader.pos - 4}"
            )
        object_id = _decode_id(reader)
        values: Dict[str, List[BSIIFieldValue]] = {}
        for field_def in definition.fields:
            start = reader.pos
            value = _decode_value(reader, field_def.type_id, field_def.ordinal_values or {})
            values.setdefault(field_def.name, []).append(
                BSIIFieldValue(
                    name=field_def.name,
                    type_id=field_def.type_id,
                    value=value,
                    offset=start,
                    size=reader.pos - start,
                )
            )
        objects.append(
            BSIIObject(
                structure_id=definition.structure_id,
                name=definition.name,
                object_id=object_id,
                fields=values,
            )
        )

    return BSIIFile(version=version, definitions=definitions, objects=objects)
