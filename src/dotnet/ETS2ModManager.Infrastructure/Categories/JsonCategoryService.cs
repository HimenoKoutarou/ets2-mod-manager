using System.Text.Json;
using ETS2ModManager.Application;
using ETS2ModManager.Contracts;

namespace ETS2ModManager.Infrastructure.Categories;

public sealed class JsonCategoryService : ICategoryService
{
    private readonly string _path;
    private readonly object _gate = new();
    private Dictionary<string, CategoryRecord>? _records;
    private List<string>? _folders;

    private sealed record CategoryRecord(string Category, long FirstSeen, long LastSeen, string NameHint);

    public JsonCategoryService(string? root = null)
    {
        var baseRoot = root ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ETS2ModManager");
        Directory.CreateDirectory(baseRoot);
        _path = Path.Combine(baseRoot, "categories.json");
    }

    public CategoryState Snapshot()
    {
        lock (_gate)
        {
            Load();
            var folders = _folders!.ToArray();
            var stats = folders.ToDictionary(x => x, _ => 0, StringComparer.Ordinal);
            stats[""] = 0;
            foreach (var record in _records!.Values)
            {
                var key = !string.IsNullOrWhiteSpace(record.Category) && stats.ContainsKey(record.Category) ? record.Category : "";
                stats[key]++;
            }
            return new CategoryState(folders, stats);
        }
    }

    public CategoryMutationResult CreateFolder(string name)
    {
        lock (_gate) { Load(); name = name.Trim(); var folders = _folders!; if (name.Length == 0 || folders.Contains(name, StringComparer.Ordinal)) return new("create_folder", false, 0, true, "folder_exists_or_empty"); folders.Add(name); Save(); return new("create_folder", true, 0, false, null); }
    }

    public CategoryMutationResult RenameFolder(string oldName, string newName)
    {
        lock (_gate)
        {
            Load(); oldName = oldName.Trim(); newName = newName.Trim();
            if (!_folders!.Contains(oldName, StringComparer.Ordinal)) return new("rename_folder", true, 0, false, null);
            if (newName.Length == 0 || (_folders!.Contains(newName, StringComparer.Ordinal) && oldName != newName)) return new("rename_folder", false, 0, true, "folder_exists");
            var folders = _folders!; var index = folders.IndexOf(oldName); folders[index] = newName; var count = 0;
            foreach (var key in _records!.Keys.ToArray()) if (_records[key].Category == oldName) { _records[key] = _records[key] with { Category = newName }; count++; }
            Save(); return new("rename_folder", true, count, false, null);
        }
    }

    public CategoryMutationResult DeleteFolder(string name)
    {
        lock (_gate)
        {
            Load(); if (!_folders!.Remove(name.Trim())) return new("delete_folder", true, 0, false, null);
            var count = 0; foreach (var key in _records!.Keys.ToArray()) if (_records[key].Category == name) { _records[key] = _records[key] with { Category = "" }; count++; } Save(); return new("delete_folder", true, count, false, null);
        }
    }

    public CategoryMutationResult SetCategory(string modId, string category)
    {
        lock (_gate)
        {
            Load(); if (string.IsNullOrWhiteSpace(modId)) return new("set_category", false, 0, false, "mod_id_required");
            var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            var records = _records!; records[modId] = records.GetValueOrDefault(modId) is { } old ? old with { Category = category.Trim(), LastSeen = now } : new(category.Trim(), now, now, ""); Save(); return new("set_category", true, 1, false, null);
        }
    }

    public string GetCategory(string modId)
    {
        lock (_gate) { Load(); return _records!.GetValueOrDefault(modId)?.Category ?? ""; }
    }

    public IReadOnlySet<string> ModsInCategory(string category)
    {
        lock (_gate) { Load(); var known = _folders!.ToHashSet(StringComparer.Ordinal); return _records!.Where(x => string.Equals(category, "", StringComparison.Ordinal) ? string.IsNullOrWhiteSpace(x.Value.Category) || !known.Contains(x.Value.Category) : x.Value.Category == category).Select(x => x.Key).ToHashSet(StringComparer.Ordinal); }
    }

    public void Touch(IEnumerable<ModRecord> mods)
    {
        lock (_gate)
        {
            Load(); var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            var records = _records!; foreach (var mod in mods) if (!string.IsNullOrWhiteSpace(mod.ModId)) records[mod.ModId] = records.GetValueOrDefault(mod.ModId) is { } old ? old with { LastSeen = now, NameHint = mod.DisplayName } : new("", now, now, mod.DisplayName);
            Save();
        }
    }

    private void Load()
    {
        if (_records is not null) return;
        _records = new(StringComparer.Ordinal); _folders = [];
        try
        {
            if (!File.Exists(_path)) return;
            var model = JsonSerializer.Deserialize<Store>(File.ReadAllText(_path));
            _folders = model?.Folders?.Where(x => !string.IsNullOrWhiteSpace(x)).Distinct(StringComparer.Ordinal).ToList() ?? [];
            _records = model?.Records?.ToDictionary(x => x.Key, x => x.Value, StringComparer.Ordinal) ?? new(StringComparer.Ordinal);
        }
        catch (Exception) { _records = new(StringComparer.Ordinal); _folders = []; }
    }

    private void Save()
    {
        var temp = _path + ".tmp-" + Guid.NewGuid().ToString("N");
        try
        {
            var json = JsonSerializer.Serialize(new Store(_folders!, _records!.Select(x => new KeyValuePair<string, CategoryRecord>(x.Key, x.Value)).ToArray()), new JsonSerializerOptions { WriteIndented = true });
            File.WriteAllText(temp, json);
            File.Move(temp, _path, true);
        }
        finally { try { File.Delete(temp); } catch { } }
    }
    private sealed record Store(List<string> Folders, KeyValuePair<string, CategoryRecord>[] Records);
}
