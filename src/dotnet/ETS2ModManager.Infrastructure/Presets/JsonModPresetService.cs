using System.Text.Json;
using ETS2ModManager.Application;
using ETS2ModManager.Contracts;

namespace ETS2ModManager.Infrastructure.Presets;

/// <summary>Stores profile-specific active Mod lists in a small, user-readable JSON file.</summary>
public sealed class JsonModPresetService : IModPresetService
{
    private readonly string _path;
    private readonly object _gate = new();

    public JsonModPresetService(string? root = null)
    {
        var baseRoot = root ?? Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "ETS2ModManager");
        Directory.CreateDirectory(baseRoot);
        _path = Path.Combine(baseRoot, "mod-presets.json");
    }

    public IReadOnlyList<ModPreset> List(string profileId)
    {
        lock (_gate)
        {
            var store = Load();
            return store.GetValueOrDefault(profileId, new Dictionary<string, string[]>(StringComparer.Ordinal))
                .OrderBy(pair => pair.Key, StringComparer.OrdinalIgnoreCase)
                .Select(pair => new ModPreset(pair.Key, pair.Value))
                .ToArray();
        }
    }

    public void Save(string profileId, string name, IReadOnlyList<string> activeMods)
    {
        if (string.IsNullOrWhiteSpace(profileId)) throw new ArgumentException("Profile id is required.", nameof(profileId));
        name = name.Trim();
        if (name.Length == 0) throw new ArgumentException("Preset name is required.", nameof(name));
        lock (_gate)
        {
            var store = Load();
            var presets = store.GetValueOrDefault(profileId);
            if (presets is null)
            {
                presets = new Dictionary<string, string[]>(StringComparer.OrdinalIgnoreCase);
                store[profileId] = presets;
            }
            presets[name] = activeMods.Where(x => !string.IsNullOrWhiteSpace(x)).Select(x => x.Trim()).ToArray();
            Persist(store);
        }
    }

    public IReadOnlyList<string>? Load(string profileId, string name)
    {
        lock (_gate)
        {
            var store = Load();
            return store.GetValueOrDefault(profileId)?.GetValueOrDefault(name.Trim());
        }
    }

    public bool Delete(string profileId, string name)
    {
        lock (_gate)
        {
            var store = Load();
            if (!store.TryGetValue(profileId, out var presets) || !presets.Remove(name.Trim())) return false;
            if (presets.Count == 0) store.Remove(profileId);
            Persist(store);
            return true;
        }
    }

    private Dictionary<string, Dictionary<string, string[]>> Load()
    {
        try
        {
            if (!File.Exists(_path)) return NewStore();
            var value = JsonSerializer.Deserialize<Dictionary<string, Dictionary<string, string[]>>>(File.ReadAllText(_path));
            if (value is null) return NewStore();
            return value.ToDictionary(
                pair => pair.Key,
                pair => new Dictionary<string, string[]>(pair.Value ?? new(), StringComparer.OrdinalIgnoreCase),
                StringComparer.Ordinal);
        }
        catch (JsonException) { return NewStore(); }
        catch (IOException) { return NewStore(); }
    }

    private void Persist(Dictionary<string, Dictionary<string, string[]>> store)
    {
        var temp = _path + ".tmp-" + Guid.NewGuid().ToString("N");
        try
        {
            var json = JsonSerializer.Serialize(store, new JsonSerializerOptions { WriteIndented = true });
            using (var stream = new FileStream(temp, FileMode.CreateNew, FileAccess.Write, FileShare.None, 16 * 1024, FileOptions.WriteThrough))
            using (var writer = new StreamWriter(stream))
            {
                writer.Write(json);
                writer.Flush();
                stream.Flush(true);
            }
            File.Move(temp, _path, true);
        }
        finally { try { File.Delete(temp); } catch { } }
    }

    private static Dictionary<string, Dictionary<string, string[]>> NewStore() =>
        new(StringComparer.Ordinal);
}
