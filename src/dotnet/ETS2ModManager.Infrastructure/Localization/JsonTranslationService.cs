using System.Text.Json;

namespace ETS2ModManager.Infrastructure.Localization;

public sealed class JsonTranslationService
{
    private readonly string _root;
    private readonly Dictionary<string, Dictionary<string, string>> _languages = new(StringComparer.OrdinalIgnoreCase);
    public JsonTranslationService(string? root = null)
    {
        _root = root ?? Path.Combine(AppContext.BaseDirectory, "assets", "i18n");
        Load();
    }
    public IReadOnlyList<string> Languages => _languages.Keys.Order(StringComparer.OrdinalIgnoreCase).ToArray();
    public string Translate(string language, string key, string fallback = "") => _languages.TryGetValue(language, out var values) && values.TryGetValue(key, out var value) && !string.IsNullOrWhiteSpace(value) ? value : fallback;
    private void Load()
    {
        if (!Directory.Exists(_root)) return;
        foreach (var file in Directory.EnumerateFiles(_root, "*.json"))
        {
            try
            {
                using var doc = JsonDocument.Parse(File.ReadAllText(file));
                var values = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                foreach (var property in doc.RootElement.EnumerateObject()) if (property.Value.ValueKind == JsonValueKind.String) values[property.Name] = property.Value.GetString() ?? "";
                _languages[Path.GetFileNameWithoutExtension(file)] = values;
            }
            catch (JsonException) { }
            catch (IOException) { }
        }
    }
}
