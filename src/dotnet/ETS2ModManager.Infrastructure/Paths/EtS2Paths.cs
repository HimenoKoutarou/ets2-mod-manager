using Microsoft.Win32;
using System.Runtime.Versioning;
using System.Text.RegularExpressions;

namespace ETS2ModManager.Infrastructure.Paths;

public sealed record Ets2Paths(
    string DocumentsDirectory,
    string ModDirectory,
    string ModsInfoPath,
    string ProfilesDirectory,
    string? SteamProfilesDirectory,
    string? WorkshopDirectory,
    string? SteamCloudDirectory,
    string DatabasePath)
{
    [SupportedOSPlatform("windows")]
    public static Ets2Paths Detect(string? databasePath = null)
    {
        var documents = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
        var game = Path.Combine(documents, "Euro Truck Simulator 2");
        var steamLibraries = FindSteamLibraries();
        var workshop = steamLibraries
            .Select(path => Path.Combine(path, "steamapps", "workshop", "content", "227300"))
            .FirstOrDefault(Directory.Exists)
            ?? steamLibraries.Select(path => Path.Combine(path, "steamapps", "workshop", "content", "227300")).FirstOrDefault();
        var cloud = FindCloudPath(steamLibraries);
        var db = databasePath ?? Path.Combine(game, "ets2modmanager.db");
        return new Ets2Paths(game, Path.Combine(game, "mod"), Path.Combine(game, "mods_info.sii"),
            Path.Combine(game, "profiles"), Directory.Exists(Path.Combine(game, "steam_profiles")) ? Path.Combine(game, "steam_profiles") : null,
            workshop, cloud, db);
    }

    [SupportedOSPlatform("windows")]
    private static IReadOnlyList<string> FindSteamLibraries()
    {
        var candidates = new List<string>();
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(@"Software\Valve\Steam");
            var value = key?.GetValue("SteamPath") as string;
            if (!string.IsNullOrWhiteSpace(value) && Directory.Exists(value)) candidates.Add(value);
        }
        catch (Exception) { }

        foreach (var root in new[] { "C:\\", "D:\\", "E:\\", "F:\\", "G:\\" })
        foreach (var candidate in new[] { Path.Combine(root, "Program Files (x86)", "Steam"), Path.Combine(root, "Steam"), Path.Combine(root, "SteamLibrary") })
            if (Directory.Exists(candidate)) candidates.Add(candidate);

        foreach (var parent in candidates.ToArray())
        {
            var vdf = Path.Combine(parent, "steamapps", "libraryfolders.vdf");
            if (!File.Exists(vdf)) continue;
            try
            {
                var text = File.ReadAllText(vdf);
                const string pathPattern = @"""path""\s+""(?<path>(?:\\.|[^""])*)""";
                foreach (Match match in Regex.Matches(text, pathPattern, RegexOptions.IgnoreCase))
                {
                    var path = match.Groups["path"].Value.Replace("\\\\", "\\");
                    if (Directory.Exists(path)) candidates.Add(path);
                }
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }
        return candidates
            .Select(Path.GetFullPath)
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .OrderByDescending(path => Directory.Exists(Path.Combine(path, "steamapps", "workshop", "content", "227300")))
            .ThenBy(path => path, StringComparer.OrdinalIgnoreCase)
            .ToArray();
    }

    private static string? FindCloudPath(IEnumerable<string> steamLibraries)
    {
        foreach (var steam in steamLibraries)
        {
            var userdata = Path.Combine(steam, "userdata");
            if (!Directory.Exists(userdata)) continue;
            try
            {
                foreach (var uid in Directory.EnumerateDirectories(userdata))
                {
                    var profiles = Path.Combine(uid, "227300", "remote", "profiles");
                    if (Directory.Exists(profiles)) return profiles;
                }
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }
        return null;
    }
}
