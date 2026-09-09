using System.Diagnostics;
using System.Text.RegularExpressions;
using ETS2ModManager.Application;
using ETS2ModManager.Contracts;
using ETS2ModManager.Domain;

namespace ETS2ModManager.Infrastructure.Diagnostics;

public sealed class CrashDiagnosisService : ICrashDiagnosisService
{
    private static readonly Regex CrashTime = new(@"Crash log created on:\s*(?<v>.*)", RegexOptions.IgnoreCase | RegexOptions.Compiled);
    private static readonly Regex Build = new(@"Build:\s*(?<v>\S+)", RegexOptions.IgnoreCase | RegexOptions.Compiled);
    private static readonly Regex Exception = new(@"Exception code:\s*(?<v>\S+(?:\s+\S+)?)", RegexOptions.IgnoreCase | RegexOptions.Compiled);
    private static readonly Regex FaultDll = new(@"Fault address:.*?(?<v>[\w.-]+\.(?:dll|exe))", RegexOptions.IgnoreCase | RegexOptions.Compiled);
    private static readonly Regex Package = new(@"(?:in package\s+'|\[hashfs\]\s+)(?<v>[^'\s:]+\.scs)", RegexOptions.IgnoreCase | RegexOptions.Compiled);
    private static readonly Regex Missing = new(@"(?:missing_file|could not load unit|unit not found)\s+(?<v>\S+)", RegexOptions.IgnoreCase | RegexOptions.Compiled);

    public CrashPair DiscoverLatestCrashPair()
    {
        var candidates = new[]
        {
            ("ets2", Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "Euro Truck Simulator 2")),
            ("ats", Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "American Truck Simulator")),
        };
        string? crash = null, log = null, source = null; var latest = DateTime.MinValue;
        foreach (var (name, directory) in candidates)
        {
            var crashPath = Path.Combine(directory, "game.crash.txt");
            if (!File.Exists(crashPath)) continue;
            var modified = File.GetLastWriteTimeUtc(crashPath);
            if (modified <= latest) continue;
            latest = modified; crash = crashPath; source = name;
            var candidateLog = Path.Combine(directory, "game.log.txt"); log = File.Exists(candidateLog) ? candidateLog : null;
        }
        if (crash is null)
        {
            foreach (var (_, directory) in candidates)
            {
                var candidateLog = Path.Combine(directory, "game.log.txt");
                if (File.Exists(candidateLog)) { log = candidateLog; break; }
            }
        }
        return new CrashPair(crash, log, source);
    }

    public CrashAnalysis Analyze(string? crashPath, string? logPath, IReadOnlyList<ModRecord> mods)
    {
        var crashText = Read(crashPath); var logText = Read(logPath);
        var head = string.Join('\n', crashText.Split('\n').Take(100));
        var fault = FaultDll.Match(head).Groups["v"].Value;
        var category = string.IsNullOrWhiteSpace(fault) ? "unknown" : fault.Contains("eurotrucks", StringComparison.OrdinalIgnoreCase) || fault.Contains("amtrucks", StringComparison.OrdinalIgnoreCase) ? "game_binary" : "third_party_or_unknown";
        var lookup = BuildAliasLookup(mods);
        var suspects = new List<CrashSuspect>(); var failed = 0;
        var lines = logText.Split('\n');
        for (var i = 0; i < lines.Length; i++)
        {
            var match = Package.Match(lines[i]); if (!match.Success) match = Missing.Match(lines[i]);
            if (!match.Success) continue;
            var token = Path.GetFileNameWithoutExtension(match.Groups["v"].Value);
            var mod = FindMod(lookup, token);
            var evidence = lines.Skip(Math.Max(0, i - 1)).Take(3).ToArray();
            if (mod is null) { failed++; suspects.Add(new CrashSuspect("", $"Unknown mod: {token}", "S", null, evidence)); }
            else
            {
                var priority = FindIndex(mods, mod);
                suspects.Add(new CrashSuspect(mod.ModId, mod.DisplayName, "S", priority >= 0 ? priority : null, evidence));
            }
        }
        var tail = lines.TakeLast(30).ToArray();
        return new CrashAnalysis(CrashTime.Match(head).Groups["v"].Value.Trim(), Build.Match(head).Groups["v"].Value.Trim(), Exception.Match(head).Groups["v"].Value.Trim(), category, suspects.Take(20).ToArray(), failed, tail);
    }

    public PrecheckReport Precheck(ProfileRef profile, IReadOnlyList<string> activeMods, IReadOnlyList<ModRecord> mods, CancellationToken cancellationToken)
    {
        var watch = Stopwatch.StartNew(); var issues = new List<PrecheckIssue>();
        var known = BuildAliasLookup(mods);
        var seen = new HashSet<string>(StringComparer.Ordinal); var index = 0;
        foreach (var active in activeMods.Reverse())
        {
            cancellationToken.ThrowIfCancellationRequested();
            var canonical = ModIdentity.CanonicalKey(active);
            var matched = FindMod(known, active);
            if (canonical.Length > 0 && !seen.Add(canonical))
            {
                issues.Add(new PrecheckIssue(active, matched?.DisplayName ?? active, "yellow", "L0", "DUPLICATE_ACTIVE_MOD", "The profile lists this mod more than once.", "Remove the duplicate entry.", index));
            }
            else if (matched is null)
            {
                issues.Add(new PrecheckIssue(active, active, "red", "L0", "MISSING_PACKAGE", "The active profile entry was not found in the scanned package catalog.", "Check the package path or disable the missing entry.", index));
            }
            index++;
        }
        watch.Stop(); var red = issues.Count(x => x.Severity == "red"); var yellow = issues.Count(x => x.Severity == "yellow");
        return new PrecheckReport(profile.ProfileId, activeMods.Count, issues.Count, red, yellow, issues, watch.ElapsedMilliseconds);
    }

    private static string Read(string? path) => string.IsNullOrWhiteSpace(path) || !File.Exists(path) ? "" : File.ReadAllText(path);

    private static Dictionary<string, ModRecord> BuildAliasLookup(IEnumerable<ModRecord> mods)
    {
        var lookup = new Dictionary<string, ModRecord>(StringComparer.Ordinal);
        foreach (var mod in mods)
        {
            var values = new[]
            {
                mod.ModId,
                mod.PackageName,
                Path.GetFileNameWithoutExtension(mod.PackagePath),
                mod.DisplayName,
            };
            foreach (var value in values)
            {
                foreach (var alias in ModIdentity.ProfileEntryAliases(value)) lookup.TryAdd(alias, mod);
            }
        }
        return lookup;
    }

    private static ModRecord? FindMod(IReadOnlyDictionary<string, ModRecord> lookup, string value)
    {
        foreach (var alias in ModIdentity.ProfileEntryAliases(value))
        {
            if (lookup.TryGetValue(alias, out var exact)) return exact;
        }
        var tokenAliases = ModIdentity.ProfileEntryAliases(value);
        foreach (var pair in lookup)
        {
            if (tokenAliases.Any(token => token.Length >= 4 && (pair.Key.Contains(token, StringComparison.Ordinal) || token.Contains(pair.Key, StringComparison.Ordinal)))) return pair.Value;
        }
        return null;
    }

    private static int FindIndex(IReadOnlyList<ModRecord> mods, ModRecord target)
    {
        for (var index = 0; index < mods.Count; index++)
        {
            if (ReferenceEquals(mods[index], target) || string.Equals(mods[index].PackagePath, target.PackagePath, StringComparison.OrdinalIgnoreCase)) return index;
        }
        return -1;
    }
}
