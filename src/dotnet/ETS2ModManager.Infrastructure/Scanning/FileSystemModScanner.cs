using System.Diagnostics;
using System.IO.Compression;
using System.Text;
using System.Text.RegularExpressions;
using ETS2ModManager.Application;
using ETS2ModManager.Contracts;
using ETS2ModManager.Domain;
using ETS2ModManager.Infrastructure.Archives;

namespace ETS2ModManager.Infrastructure.Scanning;

public sealed class FileSystemModScanner : IModScanner
{
    private static readonly Regex ManifestField = new(@"^\s*(?<key>package_name|display_name|name|author|version|package_version)\s*:\s*""(?<value>(?:\\.|[^""\\])*)""", RegexOptions.Compiled);
    private static readonly Regex ManifestHeader = new(@"(?m)^\s*mod_package\s*:\s*(?<name>[A-Za-z0-9_.-]+)\s*\{", RegexOptions.Compiled);
    private readonly string? _localDirectory;
    private readonly string? _workshopDirectory;
    private readonly IExternalArchiveService _externalArchive;
    private readonly bool _allowExternalExtraction;

    public FileSystemModScanner(
        string? localDirectory,
        string? workshopDirectory,
        IExternalArchiveService? externalArchive = null,
        bool allowExternalExtraction = true)
    {
        _localDirectory = localDirectory;
        _workshopDirectory = workshopDirectory;
        _externalArchive = externalArchive ?? new ExternalArchiveService();
        _allowExternalExtraction = allowExternalExtraction;
    }

    public Task<ModScanResult> ScanAsync(IProgress<ProgressEvent>? progress, CancellationToken cancellationToken) =>
        Task.Run(() => ScanCore(progress, cancellationToken), cancellationToken);

    public Task<ModScanResult> ScanPathsAsync(
        IEnumerable<ModRecord> candidates,
        IProgress<ProgressEvent>? progress,
        CancellationToken cancellationToken) =>
        Task.Run(() => ScanItems(
            candidates
                .Where(row => !string.IsNullOrWhiteSpace(row.PackagePath))
                .Select(row => (row.PackagePath, row.PackageType)),
            progress,
            cancellationToken), cancellationToken);

    private ModScanResult ScanCore(IProgress<ProgressEvent>? progress, CancellationToken cancellationToken)
    {
        var watch = Stopwatch.StartNew();
        var items = new List<(string Path, string Type)>();
        Add(items, _localDirectory, false); Add(items, _workshopDirectory, true);
        var result = ScanItems(items, progress, cancellationToken);
        watch.Stop();
        return result with { ElapsedMilliseconds = watch.ElapsedMilliseconds };
    }

    private ModScanResult ScanItems(
        IEnumerable<(string Path, string Type)> source,
        IProgress<ProgressEvent>? progress,
        CancellationToken cancellationToken)
    {
        var items = source.ToList();
        var mods = new List<ModRecord>();
        var newIds = new List<string>();
        for (var i = 0; i < items.Count; i++)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var (path, type) = items[i]; var info = new FileInfo(path);
            var id = Path.GetFileNameWithoutExtension(path); if (type == "workshop") id = Path.GetFileName(path);
            var package = id;
            var display = id.Replace('_', ' ');
            var manifest = ReadManifest(path, cancellationToken);
            if (manifest.TryGetValue("package_name", out var packageName)
                && IsUsablePackageName(packageName, id)) package = packageName;
            if (manifest.TryGetValue("display_name", out var displayName) && !string.IsNullOrWhiteSpace(displayName)) display = displayName;
            else if (manifest.TryGetValue("name", out var name) && !string.IsNullOrWhiteSpace(name)) display = name;
            var last = info.Exists ? info.LastWriteTimeUtc : Directory.GetLastWriteTimeUtc(path);
            var size = info.Exists ? info.Length : DirectorySize(path);
            mods.Add(new ModRecord(id, package, path, type, display, size, new DateTimeOffset(last).ToUnixTimeMilliseconds()));
            progress?.Report(new ProgressEvent("mod-scan", "scan", i + 1, items.Count, path, ScanStatus.Running, $"Scanned {i + 1}/{items.Count}"));
        }
        var deduped = ModCatalog.Deduplicate(mods);
        progress?.Report(new ProgressEvent("mod-scan", "complete", deduped.Count, items.Count, "", ScanStatus.Completed, "Scan completed"));
        return new ModScanResult(deduped, newIds, deduped.Count, 0, ScanStatus.Completed, null);
    }

    private static void Add(List<(string, string)> output, string? root, bool workshop)
    {
        if (string.IsNullOrWhiteSpace(root) || !Directory.Exists(root)) return;
        try
        {
            foreach (var item in Directory.EnumerateFileSystemEntries(root))
            {
                if (Directory.Exists(item) && workshop) output.Add((item, "workshop"));
                else if (Directory.Exists(item) && !workshop) output.Add((item, "directory"));
                else if (File.Exists(item) && new[] { ".scs", ".zip" }.Contains(Path.GetExtension(item), StringComparer.OrdinalIgnoreCase)) output.Add((item, Path.GetExtension(item)[1..].ToLowerInvariant()));
            }
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }
    private static long DirectorySize(string path) { try { return Directory.EnumerateFiles(path, "*", SearchOption.AllDirectories).Sum(x => new FileInfo(x).Length); } catch { return 0; } }

    private Dictionary<string, string> ReadManifest(string path, CancellationToken cancellationToken)
    {
        try
        {
            if (Directory.Exists(path))
            {
                var candidates = Directory.EnumerateFiles(path, "manifest.sii", SearchOption.AllDirectories);
                var file = candidates.FirstOrDefault();
                return file is null ? new(StringComparer.OrdinalIgnoreCase) : ParseManifest(File.ReadAllText(file, Encoding.UTF8));
            }
            if (File.Exists(path) && IsZipContainer(path)
                && (string.Equals(Path.GetExtension(path), ".zip", StringComparison.OrdinalIgnoreCase)
                    || string.Equals(Path.GetExtension(path), ".scs", StringComparison.OrdinalIgnoreCase)))
            {
                using var archive = ZipFile.OpenRead(path);
                var entry = archive.Entries.FirstOrDefault(x => string.Equals(Path.GetFileName(x.FullName), "manifest.sii", StringComparison.OrdinalIgnoreCase));
                if (entry is not null)
                {
                    using var reader = new StreamReader(entry.Open(), Encoding.UTF8, true);
                    return ParseManifest(reader.ReadToEnd());
                }
            }
        }
        catch (OperationCanceledException) { throw; }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
        catch (InvalidDataException) { }

        if (!_allowExternalExtraction)
            return new(StringComparer.OrdinalIgnoreCase);

        var extracted = _externalArchive.ExtractManifestAsync(path, cancellationToken).GetAwaiter().GetResult();
        return extracted.Success && !string.IsNullOrWhiteSpace(extracted.Text)
            ? ParseManifest(extracted.Text)
            : new(StringComparer.OrdinalIgnoreCase);
    }

    private static Dictionary<string, string> ParseManifest(string text)
    {
        var result = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        var header = ManifestHeader.Match(text);
        if (header.Success) result["package_name"] = header.Groups["name"].Value;
        foreach (var line in text.Split('\n'))
        {
            var match = ManifestField.Match(line);
            if (match.Success && !result.ContainsKey(match.Groups["key"].Value))
                result[match.Groups["key"].Value] = match.Groups["value"].Value.Replace("\\\"", "\"").Replace("\\\\", "\\");
        }
        if (!result.ContainsKey("version") && result.TryGetValue("package_version", out var packageVersion)) result["version"] = packageVersion;
        return result;
    }

    private static bool IsZipContainer(string path)
    {
        try
        {
            Span<byte> header = stackalloc byte[4];
            using var stream = File.OpenRead(path);
            return stream.Read(header) >= 2 && header[0] == (byte)'P' && header[1] == (byte)'K';
        }
        catch (IOException) { return false; }
        catch (UnauthorizedAccessException) { return false; }
    }

    private static bool IsUsablePackageName(string value, string modId)
    {
        if (string.IsNullOrWhiteSpace(value) || string.Equals(value, modId, StringComparison.OrdinalIgnoreCase)) return false;
        var normalized = value.Trim().TrimStart('.');
        return normalized.Length > 0 && !new[] { "manifest", "package_name", "mods_info", "nameless", "mod_package" }
            .Contains(normalized, StringComparer.OrdinalIgnoreCase);
    }
}
