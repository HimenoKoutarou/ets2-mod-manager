using System.Buffers.Binary;
using System.Text;
using System.Text.RegularExpressions;
using ETS2ModManager.Application;
using ETS2ModManager.Contracts;

namespace ETS2ModManager.Infrastructure.Saves;

public sealed class SaveEditorService(IBackupStore backup) : ISaveEditorService
{
    private delegate void SpanWriter(Span<byte> value);
    private static readonly Regex SaveName = new(@"(?m)^\s*name\s*:\s*""(?<v>(?:\\.|[^""])*)""", RegexOptions.Compiled);
    private static readonly Regex SaveNameLine = new(@"(?m)^(\s*name\s*:\s*)""(?:\\.|[^""\\])*""\s*$", RegexOptions.Compiled);
    private static readonly Regex FileTimeLine = new(@"(?m)^(\s*file_time\s*:\s*)-?\d+\s*$", RegexOptions.Compiled);
    private static readonly string[] WearFields = [
        "engine_wear", "transmission_wear", "cabin_wear", "chassis_wear", "wheels_wear",
        "engine_wear_unfixable", "transmission_wear_unfixable", "cabin_wear_unfixable",
        "chassis_wear_unfixable", "wheels_wear_unfixable"
    ];
    private static readonly string[] FuelFields = ["fuel", "current_fuel", "fuel_level", "total_fuel_litres"];

    public IReadOnlyList<SaveSlotRef> ListSlots(ProfileRef profile)
    {
        var root = Path.Combine(profile.Folder, "save");
        var result = new List<SaveSlotRef>();
        if (!Directory.Exists(root)) return result;
        foreach (var folder in Directory.EnumerateDirectories(root).OrderBy(x => x, StringComparer.OrdinalIgnoreCase))
        {
            var game = Path.Combine(folder, "game.sii");
            if (!File.Exists(game)) continue;
            var info = Path.Combine(folder, "info.sii");
            var display = Path.GetFileName(folder);
            try
            {
                if (File.Exists(info))
                {
                    var text = Encoding.UTF8.GetString(ScsCCodec.Decrypt(File.ReadAllBytes(info)));
                    display = SaveName.Match(text).Groups["v"].Value;
                    if (string.IsNullOrWhiteSpace(display)) display = Path.GetFileName(folder);
                }
            }
            catch { }
            result.Add(new SaveSlotRef(profile.ProfileId, Path.GetFileName(folder), folder, game, display, File.GetLastWriteTimeUtc(game), profile.Location));
        }
        return result;
    }

    public SaveSnapshot ReadSnapshot(SaveSlotRef slot)
    {
        var data = ScsCCodec.Decrypt(File.ReadAllBytes(slot.GameSii));
        var fields = new List<SaveFieldValue>();
        try
        {
            var document = BsiiParser.Parse(data);
            AddField(document, fields, "money_account", "bank", 0x31, "i64");
            AddField(document, fields, "experience_points", "economy", 0x27, "u32");
        }
        catch (BsiiParseException) { }
        return new SaveSnapshot(slot, fields);
    }

    public SaveMutationResult SetMoney(SaveSlotRef slot, long amount) =>
        Mutate(slot, "set_money", "money_account", "bank", 0x31, 8,
            value => BinaryPrimitives.WriteInt64LittleEndian(value, amount), "Money updated.");

    public SaveMutationResult SetExperience(SaveSlotRef slot, uint experience) =>
        Mutate(slot, "set_experience", "experience_points", "economy", 0x27, 4,
            value => BinaryPrimitives.WriteUInt32LittleEndian(value, experience), "Experience updated.");

    public SaveMutationResult SetLevel(SaveSlotRef slot, int level)
    {
        if (level is < 1 or > 200) return new SaveMutationResult(false, "set_level", "Level must be between 1 and 200.", null);
        return Mutate(slot, "set_level", "experience_points", "economy", 0x27, 4,
            value => BinaryPrimitives.WriteUInt32LittleEndian(value, checked((uint)(level * (level - 1) * 500))), "Level updated through canonical experience.");
    }

    public SaveMutationResult RepairTruck(SaveSlotRef slot) =>
        MutateFloatFields(slot, "repair_truck", WearFields, 0f, "Truck wear values reset.");

    public SaveMutationResult RefuelTruck(SaveSlotRef slot, float amount)
    {
        if (!float.IsFinite(amount) || amount < 0) return new SaveMutationResult(false, "refuel_truck", "Fuel amount must be a finite non-negative value.", null);
        return MutateFloatFields(slot, "refuel_truck", FuelFields, amount, "Fuel values updated.");
    }

    public SaveMutationResult UnlockAllGarages(SaveSlotRef slot) =>
        MutateBooleanFields(slot, "unlock_all_garages", ["garages"], "Garage unlock flag enabled.");

    public SaveMutationResult UnlockAllDealers(SaveSlotRef slot) =>
        MutateBooleanFields(slot, "unlock_all_dealers", ["dealers", "all_dealers"], "Dealer unlock flag enabled.");

    public SaveSlotRef CopySlot(SaveSlotRef slot, string displayName)
    {
        RequireWritable(slot);
        displayName = displayName.Trim();
        if (displayName.Length == 0) throw new ArgumentException("Save display name is required.", nameof(displayName));
        var source = Path.GetFullPath(slot.Folder);
        var saveRoot = Directory.GetParent(source)?.FullName ?? throw new IOException("Save root is missing.");
        if (!Directory.Exists(source) || !File.Exists(Path.Combine(source, "game.sii")) || !File.Exists(Path.Combine(source, "info.sii")))
            throw new FileNotFoundException("The source save is incomplete.");
        var next = Directory.EnumerateDirectories(saveRoot).Select(Path.GetFileName)
            .Where(name => int.TryParse(name, out _)).Select(name => int.Parse(name!)).DefaultIfEmpty(0).Max() + 1;
        string target;
        do target = Path.Combine(saveRoot, (next++).ToString(System.Globalization.CultureInfo.InvariantCulture)); while (Directory.Exists(target));
        var staging = target + ".copy-" + Guid.NewGuid().ToString("N");
        try
        {
            CopyDirectory(source, staging);
            var infoPath = Path.Combine(staging, "info.sii");
            var originalInfo = File.ReadAllBytes(infoPath);
            var plain = Encoding.UTF8.GetString(ScsCCodec.Decrypt(originalInfo));
            var escaped = EscapeSii(displayName);
            var renamed = SaveNameLine.Replace(plain, match => $"{match.Groups[1].Value}\"{escaped}\"", 1);
            if (string.Equals(renamed, plain, StringComparison.Ordinal)) throw new InvalidDataException("info.sii does not contain a writable save name.");
            var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            renamed = FileTimeLine.Replace(renamed, match => $"{match.Groups[1].Value}{now}", 1);
            var infoBytes = Encoding.UTF8.GetBytes(renamed.TrimStart('\uFEFF'));
            AtomicWrite(infoPath, ScsCCodec.IsScsC(originalInfo) ? ScsCCodec.Encrypt(infoBytes) : infoBytes);
            Directory.Move(staging, target);
        }
        catch
        {
            try { Directory.Delete(staging, true); } catch { }
            throw;
        }
        var game = Path.Combine(target, "game.sii");
        return new SaveSlotRef(slot.ProfileId, Path.GetFileName(target), target, game, displayName, File.GetLastWriteTimeUtc(game), slot.ProfileLocation);
    }

    private static void AddField(BsiiDocument document, List<SaveFieldValue> fields, string name, string structure, uint type, string displayType)
    {
        var matches = document.FindFields(name, structure).Where(pair => pair.Field.TypeId == type).ToArray();
        if (matches.Length != 1) return;
        fields.Add(new SaveFieldValue(name, Convert.ToString(matches[0].Field.Value, System.Globalization.CultureInfo.InvariantCulture) ?? "", displayType, matches[0].Field.Offset));
    }

    private SaveMutationResult Mutate(SaveSlotRef slot, string operation, string field, string structure, uint type, int size, SpanWriter writer, string message)
    {
        RequireWritable(slot);
        var original = File.ReadAllBytes(slot.GameSii);
        var plain = ScsCCodec.Decrypt(original);
        BsiiDocument document;
        try { document = BsiiParser.Parse(plain); }
        catch (BsiiParseException error) { return new SaveMutationResult(false, operation, error.Message, null); }
        var matches = document.FindFields(field, structure).Where(pair => pair.Field.TypeId == type && pair.Field.Size == size).ToArray();
        if (matches.Length != 1) return new SaveMutationResult(false, operation, $"Field {structure}.{field} was not found uniquely in the BSII save.", null);
        var outputPlain = (byte[])plain.Clone();
        writer(outputPlain.AsSpan(matches[0].Field.Offset, size));
        if (outputPlain.AsSpan().SequenceEqual(plain)) return new SaveMutationResult(false, operation, "The requested value is already stored.", null);
        return Save(slot, original, outputPlain, operation, message);
    }

    private SaveMutationResult MutateFloatFields(SaveSlotRef slot, string operation, IEnumerable<string> names, float value, string message)
    {
        RequireWritable(slot);
        var original = File.ReadAllBytes(slot.GameSii);
        var plain = ScsCCodec.Decrypt(original);
        BsiiDocument document;
        try { document = BsiiParser.Parse(plain); }
        catch (BsiiParseException error) { return new SaveMutationResult(false, operation, error.Message, null); }
        var fields = names.SelectMany(name => document.FindFields(name))
            .Select(pair => pair.Field).Where(field => field.TypeId == 0x05 && field.Size == 4).ToArray();
        if (fields.Length == 0) return new SaveMutationResult(false, operation, "No compatible BSII fields were found.", null);
        var outputPlain = (byte[])plain.Clone();
        foreach (var field in fields) BinaryPrimitives.WriteSingleLittleEndian(outputPlain.AsSpan(field.Offset, 4), value);
        if (outputPlain.AsSpan().SequenceEqual(plain)) return new SaveMutationResult(false, operation, "The requested values are already stored.", null);
        return Save(slot, original, outputPlain, operation, $"{message} Fields changed: {fields.Length}.");
    }

    private SaveMutationResult MutateBooleanFields(SaveSlotRef slot, string operation, IEnumerable<string> names, string message)
    {
        RequireWritable(slot);
        var original = File.ReadAllBytes(slot.GameSii);
        var plain = ScsCCodec.Decrypt(original);
        BsiiDocument document;
        try { document = BsiiParser.Parse(plain); }
        catch (BsiiParseException error) { return new SaveMutationResult(false, operation, error.Message, null); }

        var allowed = names.ToHashSet(StringComparer.Ordinal);
        var fields = document.Objects
            .SelectMany(obj => obj.Fields.Where(pair => allowed.Contains(pair.Key)).SelectMany(pair => pair.Value))
            .Where(field => field.TypeId == 0x35 && field.Size == 1)
            .ToArray();
        if (fields.Length == 0)
            return new SaveMutationResult(false, operation,
                $"No compatible boolean field was found ({string.Join(", ", allowed)}). This save format does not expose a safe structured unlock flag.", null);

        var outputPlain = (byte[])plain.Clone();
        var changed = 0;
        foreach (var field in fields)
        {
            if (outputPlain[field.Offset] == 1) continue;
            outputPlain[field.Offset] = 1;
            changed++;
        }
        if (changed == 0) return new SaveMutationResult(false, operation, "The requested unlock flags are already enabled.", null);
        return Save(slot, original, outputPlain, operation, $"{message} Fields changed: {changed}.");
    }

    private SaveMutationResult Save(SaveSlotRef slot, byte[] original, byte[] outputPlain, string operation, string message)
    {
        var output = ScsCCodec.IsScsC(original) ? ScsCCodec.Encrypt(outputPlain) : outputPlain;
        var backupPath = backup.Backup(slot.GameSii, "pre-" + operation);
        AtomicWrite(slot.GameSii, output);
        return new SaveMutationResult(true, operation, message, backupPath);
    }

    private static void RequireWritable(SaveSlotRef slot)
    {
        if (!slot.IsWritable) throw new UnauthorizedAccessException("Steam/Cloud saves are read-only.");
    }

    private static string EscapeSii(string value)
    {
        var result = new StringBuilder();
        foreach (var rune in value.EnumerateRunes())
        {
            if (rune.IsAscii && rune.Value is not (34 or 92)) result.Append((char)rune.Value);
            else if (rune.Value == 34) result.Append("\\\"");
            else if (rune.Value == 92) result.Append("\\\\");
            else foreach (var b in Encoding.UTF8.GetBytes(rune.ToString())) result.Append($"\\x{b:X2}");
        }
        return result.ToString();
    }

    private static void CopyDirectory(string source, string target)
    {
        Directory.CreateDirectory(target);
        foreach (var file in Directory.EnumerateFiles(source)) File.Copy(file, Path.Combine(target, Path.GetFileName(file)), true);
        foreach (var directory in Directory.EnumerateDirectories(source)) CopyDirectory(directory, Path.Combine(target, Path.GetFileName(directory)));
    }

    private static void AtomicWrite(string path, byte[] data)
    {
        var temp = path + ".tmp-" + Guid.NewGuid().ToString("N");
        try
        {
            using (var stream = new FileStream(temp, FileMode.CreateNew, FileAccess.Write, FileShare.None, 64 * 1024, FileOptions.WriteThrough))
            {
                stream.Write(data);
                stream.Flush(true);
            }
            File.Move(temp, path, true);
        }
        finally { try { File.Delete(temp); } catch { } }
    }
}
