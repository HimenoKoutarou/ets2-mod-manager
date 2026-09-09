using System.Text.Json;

namespace ETS2ModManager.Infrastructure.Session;

public sealed class JsonSessionStore
{
    private readonly string _path;
    private readonly object _gate = new();
    public JsonSessionStore(string? path = null)
    {
        _path = path ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ETS2ModManager", "session.json");
        Directory.CreateDirectory(Path.GetDirectoryName(_path) ?? ".");
    }
    public void Save<T>(T value)
    {
        lock (_gate)
        {
            var temp = _path + ".tmp-" + Guid.NewGuid().ToString("N");
            File.WriteAllText(temp, JsonSerializer.Serialize(value, new JsonSerializerOptions { WriteIndented = true }));
            File.Move(temp, _path, true);
        }
    }
    public T? Load<T>()
    {
        lock (_gate)
        {
            try { return File.Exists(_path) ? JsonSerializer.Deserialize<T>(File.ReadAllText(_path)) : default; }
            catch (JsonException) { return default; }
            catch (IOException) { return default; }
        }
    }
}
