using System.IO.Compression;
using System.Text;
using System.Text.RegularExpressions;
using ETS2ModManager.Application;
using ETS2ModManager.Contracts;

namespace ETS2ModManager.Infrastructure.Cities;

public sealed class CityLookupService : ICityLookupService
{
    private static readonly Regex Unit = new(@"(?is)city_data\s*:\s*[^\{]+\{(?<body>.*?)\}", RegexOptions.Compiled);
    private static readonly Regex Field = new("(?m)^\\s*(?<key>city_name|short_city_name|country)\\s*:\\s*\\\"(?<value>(?:\\\\.|[^\\\"\\\\])*)\\\"", RegexOptions.Compiled);

    public Task<IReadOnlyList<CitySearchResult>> RebuildAndSearchAsync(IEnumerable<ModRecord> mods, string keyword, CancellationToken cancellationToken)
    {
        return Task.Run(() =>
        {
            var map = new Dictionary<string, List<CityHit>>(StringComparer.OrdinalIgnoreCase);
            var ordered = mods.Where(x => x.PackageType is "directory" or "scs" or "zip").ToArray();
            for (var index = ordered.Length - 1; index >= 0; index--)
            {
                cancellationToken.ThrowIfCancellationRequested();
                foreach (var text in ReadCityFiles(ordered[index].PackagePath))
                {
                    foreach (Match unit in Unit.Matches(text))
                    {
                        var fields = Field.Matches(unit.Groups["body"].Value).ToDictionary(x => x.Groups["key"].Value, x => Unescape(x.Groups["value"].Value), StringComparer.OrdinalIgnoreCase);
                        if (!fields.TryGetValue("city_name", out var city) || city.Length == 0) continue;
                        var hit = new CityHit(city, fields.GetValueOrDefault("short_city_name", ""), fields.GetValueOrDefault("country", ""), ordered[index].ModId, ordered[index].DisplayName, ordered[index].PackagePath, index);
                        if (!map.TryGetValue(city, out var hits)) map[city] = hits = [];
                        hits.Add(hit);
                    }
                }
            }
            var query = keyword.Trim();
            return (IReadOnlyList<CitySearchResult>)map.Where(x => query.Length == 0 || x.Key.Contains(query, StringComparison.OrdinalIgnoreCase)).OrderBy(x => x.Key, StringComparer.OrdinalIgnoreCase).Select(x => new CitySearchResult(x.Key, x.Value.OrderBy(y => y.PriorityIndex).ToArray())).ToArray();
        }, cancellationToken);
    }

    private static IEnumerable<string> ReadCityFiles(string path)
    {
        if (Directory.Exists(path))
        {
            foreach (var file in Directory.EnumerateFiles(Path.Combine(path, "def"), "city*.sii", SearchOption.AllDirectories))
            {
                string text; try { text = File.ReadAllText(file, Encoding.UTF8); } catch { continue; } yield return text;
            }
            yield break;
        }
        if (!File.Exists(path)) yield break;
        var texts = new List<string>();
        try
        {
            using var archive = ZipFile.OpenRead(path);
            foreach (var entry in archive.Entries.Where(x => x.FullName.Replace('\\', '/').StartsWith("def/city", StringComparison.OrdinalIgnoreCase) && x.FullName.EndsWith(".sii", StringComparison.OrdinalIgnoreCase)))
            {
                using var reader = new StreamReader(entry.Open(), Encoding.UTF8, true); texts.Add(reader.ReadToEnd());
            }
        }
        catch { }
        foreach (var text in texts) yield return text;
    }
    private static string Unescape(string value) => value.Replace("\\\"", "\"").Replace("\\\\", "\\");
}
