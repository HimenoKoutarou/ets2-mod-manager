using System.Diagnostics;
using ETS2ModManager.Application;
using ETS2ModManager.Contracts;
using ETS2ModManager.Infrastructure.Archives;

namespace ETS2ModManager.Infrastructure.Scanning;

/// <summary>
/// Uses the SQLite snapshot as the source of truth for unchanged packages and
/// performs expensive metadata reads only for new or modified paths.
/// </summary>
public sealed class IncrementalModScanner : IModScanner
{
    private readonly string? _localDirectory;
    private readonly string? _workshopDirectory;
    private readonly Func<IReadOnlyList<ModRecord>> _readCache;
    private readonly HybridModScanner _fullScanner;
    private readonly FileSystemModScanner _detailScanner;

    public IncrementalModScanner(
        string? localDirectory,
        string? workshopDirectory,
        Func<IReadOnlyList<ModRecord>> readCache,
        IExternalArchiveService? externalArchive = null)
    {
        _localDirectory = localDirectory;
        _workshopDirectory = workshopDirectory;
        _readCache = readCache;
        var archive = externalArchive ?? new ExternalArchiveService();
        _fullScanner = new HybridModScanner(localDirectory, workshopDirectory, archive);
        _detailScanner = new FileSystemModScanner(
            localDirectory,
            workshopDirectory,
            archive,
            allowExternalExtraction: true);
    }

    public async Task<ModScanResult> ScanAsync(
        IProgress<ProgressEvent>? progress,
        CancellationToken cancellationToken)
    {
        var watch = Stopwatch.StartNew();
        cancellationToken.ThrowIfCancellationRequested();

        var cached = _readCache();
        if (cached.Count == 0)
        {
            var initial = await _fullScanner.ScanAsync(progress, cancellationToken).ConfigureAwait(false);
            var discoveredInitial = Discover(_localDirectory, _workshopDirectory, cancellationToken)
                .ToDictionary(row => NormalizePath(row.PackagePath), StringComparer.OrdinalIgnoreCase);
            var normalizedInitial = initial.Mods.Select(row =>
            {
                if (!discoveredInitial.TryGetValue(NormalizePath(row.PackagePath), out var metadata)) return row;
                return row with
                {
                    PackagePath = NormalizePath(row.PackagePath),
                    FileSize = metadata.FileSize,
                    LastModifiedUnixMilliseconds = metadata.LastModifiedUnixMilliseconds,
                };
            }).ToArray();
            var initialIds = normalizedInitial.Select(row => row.ModId).Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
            return initial with
            {
                Mods = normalizedInitial,
                NewModIds = initialIds,
                ElapsedMilliseconds = watch.ElapsedMilliseconds,
            };
        }

        var discovered = Discover(_localDirectory, _workshopDirectory, cancellationToken);
        var cachedByPath = cached
            .Where(row => !string.IsNullOrWhiteSpace(row.PackagePath))
            .GroupBy(row => NormalizePath(row.PackagePath), StringComparer.OrdinalIgnoreCase)
            .ToDictionary(group => group.Key, group => group.First(), StringComparer.OrdinalIgnoreCase);

        var changed = discovered
            .Where(row => !cachedByPath.TryGetValue(NormalizePath(row.PackagePath), out var cached)
                || HasChanged(cached, row))
            .ToArray();
        var newIds = changed
            .Where(row => !cachedByPath.ContainsKey(NormalizePath(row.PackagePath)))
            .Select(row => row.ModId)
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();

        progress?.Report(new ProgressEvent(
            "mod-scan", "incremental", 0, changed.Length, string.Empty,
            ScanStatus.Running,
            cachedByPath.Count == 0
                ? $"Initial scan: {changed.Length} packages"
                : $"Incremental scan: {changed.Length} changed, {discovered.Count - changed.Length} cached"));

        var details = changed.Length == 0
            ? new ModScanResult([], [], 0, 0, ScanStatus.Completed, null)
            : await _detailScanner.ScanPathsAsync(changed, progress, cancellationToken).ConfigureAwait(false);
        var detailByPath = details.Mods
            .GroupBy(row => NormalizePath(row.PackagePath), StringComparer.OrdinalIgnoreCase)
            .ToDictionary(group => group.Key, group => group.First(), StringComparer.OrdinalIgnoreCase);

        var merged = new List<ModRecord>(discovered.Count);
        foreach (var item in discovered)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var key = NormalizePath(item.PackagePath);
            if (detailByPath.TryGetValue(key, out var detail))
            {
                if (cachedByPath.TryGetValue(key, out var previous))
                {
                    merged.Add(detail with
                    {
                        PackagePath = item.PackagePath,
                        FileSize = item.FileSize,
                        LastModifiedUnixMilliseconds = item.LastModifiedUnixMilliseconds,
                        PackageName = IsDefault(detail.PackageName, detail.ModId) ? previous.PackageName : detail.PackageName,
                        DisplayName = IsDefault(detail.DisplayName, detail.ModId) ? previous.DisplayName : detail.DisplayName,
                    });
                }
                else
                {
                    merged.Add(detail with
                    {
                        PackagePath = item.PackagePath,
                        FileSize = item.FileSize,
                        LastModifiedUnixMilliseconds = item.LastModifiedUnixMilliseconds,
                    });
                }
            }
            else if (cachedByPath.TryGetValue(key, out var cachedRow) && !HasChanged(cachedRow, item))
            {
                merged.Add(cachedRow);
            }
            else
            {
                merged.Add(item);
            }
        }

        var output = ModCatalog.Deduplicate(merged);
        watch.Stop();
        progress?.Report(new ProgressEvent(
            "mod-scan", "complete", output.Count, output.Count, string.Empty,
            ScanStatus.Completed,
            $"Scan completed: {output.Count} packages, {changed.Length} inspected"));
        return new ModScanResult(output, newIds, output.Count, watch.ElapsedMilliseconds, ScanStatus.Completed, null);
    }

    private static IReadOnlyList<ModRecord> Discover(
        string? localDirectory,
        string? workshopDirectory,
        CancellationToken cancellationToken)
    {
        var rows = new List<ModRecord>();
        AddRoot(rows, localDirectory, workshop: false, cancellationToken);
        AddRoot(rows, workshopDirectory, workshop: true, cancellationToken);
        return rows;
    }

    private static void AddRoot(
        List<ModRecord> output,
        string? root,
        bool workshop,
        CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(root) || !Directory.Exists(root)) return;
        try
        {
            foreach (var path in Directory.EnumerateFileSystemEntries(root))
            {
                cancellationToken.ThrowIfCancellationRequested();
                var isDirectory = Directory.Exists(path);
                var extension = Path.GetExtension(path);
                if (!isDirectory && !new[] { ".scs", ".zip" }.Contains(extension, StringComparer.OrdinalIgnoreCase)) continue;
                if (isDirectory && !workshop && !File.Exists(Path.Combine(path, "manifest.sii")))
                {
                    // Local unpacked mods may keep manifest.sii below a nested
                    // folder, so they remain valid candidates.
                }

                var id = workshop ? Path.GetFileName(path) : Path.GetFileNameWithoutExtension(path);
                var type = workshop ? "workshop" : isDirectory ? "directory" : extension.TrimStart('.').ToLowerInvariant();
                var size = isDirectory ? 0 : new FileInfo(path).Length;
                var modified = isDirectory ? DirectoryStamp(path) : File.GetLastWriteTimeUtc(path);
                output.Add(new ModRecord(
                    id, id, Path.GetFullPath(path), type, id.Replace('_', ' '), size,
                    new DateTimeOffset(modified).ToUnixTimeMilliseconds()));
            }
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }

    private static bool HasChanged(ModRecord cached, ModRecord current)
    {
        if (!string.Equals(cached.PackageType, current.PackageType, StringComparison.OrdinalIgnoreCase)) return true;
        if (cached.LastModifiedUnixMilliseconds != current.LastModifiedUnixMilliseconds) return true;
        return !string.Equals(current.PackageType, "directory", StringComparison.OrdinalIgnoreCase)
            && !string.Equals(current.PackageType, "workshop", StringComparison.OrdinalIgnoreCase)
            && cached.FileSize != current.FileSize;
    }

    private static bool IsDefault(string value, string modId) =>
        string.IsNullOrWhiteSpace(value) || string.Equals(value, modId, StringComparison.OrdinalIgnoreCase);

    private static DateTime DirectoryStamp(string path)
    {
        var latest = Directory.GetLastWriteTimeUtc(path);
        try
        {
            foreach (var file in Directory.EnumerateFiles(path, "*", SearchOption.AllDirectories))
            {
                var modified = File.GetLastWriteTimeUtc(file);
                if (modified > latest) latest = modified;
            }
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
        return latest;
    }

    private static string NormalizePath(string path)
    {
        try
        {
            var full = Path.GetFullPath(path);
            return full.Replace(Path.AltDirectorySeparatorChar, Path.DirectorySeparatorChar)
                .TrimEnd(Path.DirectorySeparatorChar);
        }
        catch (Exception error) when (error is ArgumentException or NotSupportedException or PathTooLongException)
        { return path; }
    }
}
