using System.Buffers.Binary;
using System.Text;

namespace ETS2ModManager.Infrastructure.Saves;

internal sealed record BsiiField(string Name, uint TypeId, object? Value, int Offset, int Size);
internal sealed record BsiiObject(string StructureName, IReadOnlyDictionary<string, IReadOnlyList<BsiiField>> Fields);

internal sealed class BsiiDocument(uint version, IReadOnlyList<BsiiObject> objects)
{
    public uint Version { get; } = version;
    public IReadOnlyList<BsiiObject> Objects { get; } = objects;

    public IReadOnlyList<(BsiiObject Object, BsiiField Field)> FindFields(string name, string? structureName = null)
    {
        var result = new List<(BsiiObject, BsiiField)>();
        foreach (var obj in Objects)
        {
            if (structureName is not null && !string.Equals(obj.StructureName, structureName, StringComparison.Ordinal)) continue;
            if (!obj.Fields.TryGetValue(name, out var values)) continue;
            result.AddRange(values.Select(value => (obj, value)));
        }
        return result;
    }
}

internal sealed class BsiiParseException(string message) : Exception(message);

internal static class BsiiParser
{
    private static readonly string CharTable = "0123456789abcdefghijklmnopqrstuvwxyz_";

    private sealed class Reader(byte[] data, uint version)
    {
        private readonly byte[] _data = data;
        public int Position { get; private set; } = 8;
        public uint Version { get; } = version;

        private void Need(int count)
        {
            if (count < 0 || Position > _data.Length - count)
                throw new BsiiParseException($"Truncated BSII at offset {Position}; need {count} bytes.");
        }

        public byte U8() { Need(1); return _data[Position++]; }
        public ushort U16() { Need(2); var value = BinaryPrimitives.ReadUInt16LittleEndian(_data.AsSpan(Position, 2)); Position += 2; return value; }
        public uint U32() { Need(4); var value = BinaryPrimitives.ReadUInt32LittleEndian(_data.AsSpan(Position, 4)); Position += 4; return value; }
        public ulong U64() { Need(8); var value = BinaryPrimitives.ReadUInt64LittleEndian(_data.AsSpan(Position, 8)); Position += 8; return value; }
        public short I16() => unchecked((short)U16());
        public int I32() => unchecked((int)U32());
        public long I64() => unchecked((long)U64());
        public float F32() => BitConverter.Int32BitsToSingle(I32());

        public string String()
        {
            var size = checked((int)U32());
            Need(size);
            try
            {
                var value = Encoding.UTF8.GetString(_data, Position, size);
                Position += size;
                return value;
            }
            catch (DecoderFallbackException error)
            {
                throw new BsiiParseException($"Invalid UTF-8 string at offset {Position}: {error.Message}");
            }
        }
    }

    private sealed record FieldDefinition(string Name, uint TypeId, IReadOnlyDictionary<uint, string> Ordinals);
    private sealed record Definition(string Name, IReadOnlyList<FieldDefinition> Fields);

    public static BsiiDocument Parse(byte[] data)
    {
        if (data.Length < 8 || !data.AsSpan(0, 4).SequenceEqual("BSII"u8))
            throw new BsiiParseException("Not a BSII stream.");
        var version = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(4, 4));
        if (version is < 1 or > 3) throw new BsiiParseException($"Unsupported BSII version {version}.");
        var reader = new Reader(data, version);
        var definitions = new Dictionary<uint, Definition>();
        var objects = new List<BsiiObject>();

        while (reader.Position < data.Length)
        {
            var blockType = reader.U32();
            if (blockType == 0)
            {
                var valid = reader.U8() != 0;
                if (!valid) continue;
                var structureId = reader.U32();
                var name = reader.String();
                var fields = new List<FieldDefinition>();
                while (true)
                {
                    var typeId = reader.U32();
                    if (typeId == 0) break;
                    var fieldName = reader.String();
                    var ordinals = new Dictionary<uint, string>();
                    if (typeId == 0x37)
                    {
                        var count = checked((int)reader.U32());
                        for (var i = 0; i < count; i++) ordinals[reader.U32()] = reader.String();
                    }
                    fields.Add(new FieldDefinition(fieldName, typeId, ordinals));
                }
                definitions[structureId] = new Definition(name, fields);
                continue;
            }

            if (!definitions.TryGetValue(blockType, out var definition))
                throw new BsiiParseException($"Object references unknown BSII structure {blockType}.");
            DecodeId(reader);
            var values = new Dictionary<string, List<BsiiField>>(StringComparer.Ordinal);
            foreach (var field in definition.Fields)
            {
                var offset = reader.Position;
                var value = DecodeValue(reader, field.TypeId, field.Ordinals);
                var parsed = new BsiiField(field.Name, field.TypeId, value, offset, reader.Position - offset);
                if (!values.TryGetValue(field.Name, out var list)) values[field.Name] = list = [];
                list.Add(parsed);
            }
            objects.Add(new BsiiObject(definition.Name,
                values.ToDictionary(pair => pair.Key, pair => (IReadOnlyList<BsiiField>)pair.Value, StringComparer.Ordinal)));
        }

        return new BsiiDocument(version, objects);
    }

    private static string DecodeEncodedString(Reader reader)
    {
        var value = reader.U64();
        var chars = new StringBuilder();
        while (value != 0)
        {
            var remainder = value % 38;
            value /= 38;
            var index = (int)remainder - 1;
            if ((uint)index < (uint)CharTable.Length) chars.Append(CharTable[index]);
        }
        var array = chars.ToString().ToCharArray();
        Array.Reverse(array);
        return new string(array);
    }

    private static string DecodeId(Reader reader)
    {
        var count = reader.U8();
        if (count == 0xFF) return $"_nameless.{reader.U64():x}";
        var parts = new string[count];
        for (var i = 0; i < count; i++) parts[i] = DecodeEncodedString(reader);
        return count == 0 ? "null" : string.Join('.', parts);
    }

    private static object? DecodeValue(Reader reader, uint typeId, IReadOnlyDictionary<uint, string> ordinals)
    {
        return typeId switch
        {
            0x01 => reader.String(),
            0x02 => DecodeArray(reader, r => r.String()),
            0x03 => DecodeEncodedString(reader),
            0x04 => DecodeArray(reader, DecodeEncodedString),
            0x05 => reader.F32(),
            0x06 => DecodeArray(reader, r => r.F32()),
            0x07 => DecodeFloatVector(reader, 2),
            0x08 => DecodeArray(reader, r => DecodeFloatVector(r, 2)),
            0x09 => DecodeFloatVector(reader, 3),
            0x0A => DecodeArray(reader, r => DecodeFloatVector(r, 3)),
            0x11 => DecodeIntVector(reader, 3),
            0x12 => DecodeArray(reader, r => DecodeIntVector(r, 3)),
            0x17 => DecodeFloatVector(reader, 4),
            0x18 => DecodeArray(reader, r => DecodeFloatVector(r, 4)),
            0x19 => DecodeFloatVector(reader, reader.Version >= 2 ? 8 : 7),
            0x1A => DecodeArray(reader, r => DecodeFloatVector(r, reader.Version >= 2 ? 8 : 7)),
            0x25 => reader.I32(),
            0x26 => DecodeArray(reader, r => r.I32()),
            0x27 or 0x2F => reader.U32(),
            0x28 => DecodeArray(reader, r => r.U32()),
            0x29 => reader.I16(),
            0x2A => DecodeArray(reader, r => r.I16()),
            0x2B => reader.U16(),
            0x2C => DecodeArray(reader, r => r.U16()),
            0x31 => reader.I64(),
            0x32 => DecodeArray(reader, r => r.I64()),
            0x33 => reader.U64(),
            0x34 => DecodeArray(reader, r => r.U64()),
            0x35 => reader.U8() != 0,
            0x36 => DecodeArray(reader, r => r.U8() != 0),
            0x37 => ordinals.TryGetValue(reader.U32(), out var label) ? label : string.Empty,
            0x39 or 0x3B or 0x3D => DecodeId(reader),
            0x3A or 0x3C or 0x3E => DecodeArray(reader, DecodeId),
            _ => throw new BsiiParseException($"Unsupported BSII type 0x{typeId:x2} at offset {reader.Position}.")
        };
    }

    private static List<T> DecodeArray<T>(Reader reader, Func<Reader, T> decoder)
    {
        var count = checked((int)reader.U32());
        var result = new List<T>(count);
        for (var i = 0; i < count; i++) result.Add(decoder(reader));
        return result;
    }

    private static float[] DecodeFloatVector(Reader reader, int count)
    {
        var result = new float[count];
        for (var i = 0; i < count; i++) result[i] = reader.F32();
        return result;
    }

    private static int[] DecodeIntVector(Reader reader, int count)
    {
        var result = new int[count];
        for (var i = 0; i < count; i++) result[i] = reader.I32();
        return result;
    }
}
