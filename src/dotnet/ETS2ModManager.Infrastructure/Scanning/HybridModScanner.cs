using System.Text.Json;
using ETS2ModManager.Application;
using ETS2ModManager.Contracts;
using ETS2ModManager.Infrastructure.Archives;
using ETS2ModManager.Infrastructure.Rust;
using ETS2ModManager.Domain;

namespace ETS2ModManager.Infrastructure.Scanning;

public sealed class HybridModScanner : IModScanner
{
    private readonly string? _localDirectory;
    private readonly string? _workshopDirectory;
    private readonly FileSystemModScanner _fallback;
    private readonly FileSystemModScanner _metadataFallback;

    public HybridModScanner(string? localDirectory, string? workshopDirectory, IExternalArchiveService? externalArchive = null)
    {
        _localDirectory = localDirectory;
        _workshopDirectory = workshopDirectory;
        var archive = externalArchive ?? new ExternalArchiveService();
        _fallback = new FileSystemModScanner(localDirectory, workshopDirectory, archive, allowExternalExtraction: true);
        _metadataFallback = new FileSystemModScanner(localDirectory, workshopDirectory, archive, allowExternalExtraction: false);
    }

    public async Task<ModScanResult> ScanAsync(IProgress<ProgressEvent>? progress, CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        try
        {
            var client = new Ets2CoreClient();
            client.EnsureCompatible();
            var json = client.ScanRoots(_localDirectory, _workshopDirectory);
            var result = Parse(json);
            // The native scanner already supplies a stable package id when a
            // manifest uses a template placeholder such as `.manifest`.
            // Only request the slower managed fallback when the display name
            // is genuinely missing; package-name fallbacks are not worth
            // extracting every large SCS archive again.
            if (result.Mods.Any(NeedsMetadata))
            {
                var managed = await _metadataFallback.ScanAsync(null, cancellationToken).ConfigureAwait(false);
                var byPath = managed.Mods.ToDictionary(x => x.PackagePath, StringComparer.OrdinalIgnoreCase);
                var merged = result.Mods.Select(row =>
                {
                    if (!byPath.TryGetValue(row.PackagePath, out var detail)) return row;
                    return row with
                    {
                        PackageName = IsDefault(row.PackageName, row.ModId) && IsUsablePackageName(detail.PackageName, row.ModId)
                            ? detail.PackageName
                            : row.PackageName,
                        DisplayName = IsDefault(row.DisplayName, row.ModId) ? detail.DisplayName : row.DisplayName,
                    };
                }).ToArray();
                result = result with { Mods = merged, ScannedCount = merged.Length };
            }
            progress?.Report(new ProgressEvent("mod-scan", "complete", result.ScannedCount, result.ScannedCount, "", ScanStatus.Completed, "Rust scan completed"));
            return result with { Mods = ModCatalog.Deduplicate(result.Mods), ScannedCount = ModCatalog.Deduplicate(result.Mods).Count };
        }
        catch (DllNotFoundException) { }
        catch (EntryPointNotFoundException) { }
        catch (BadImageFormatException) { }
        catch (InvalidOperationException) { }

        var fallback = await _fallback.ScanAsync(progress, cancellationToken);
        return fallback with { Mods = ModCatalog.Deduplicate(fallback.Mods), ScannedCount = ModCatalog.Deduplicate(fallback.Mods).Count };
    }

    private static ModScanResult Parse(string json)
    {
        using var document = JsonDocument.Parse(json);
        var rows = new List<ModRecord>();
        foreach (var item in document.RootElement.GetProperty("packages").EnumerateArray())
        {
            var path = item.GetProperty("path").GetString() ?? "";
            var type = item.GetProperty("type").GetString() ?? "unknown";
            var id = item.GetProperty("mod_id").GetString() ?? Path.GetFileNameWithoutExtension(path);
            var package = item.TryGetProperty("package_name", out var packageProperty)
                ? packageProperty.GetString() ?? id
                : id;
            var display = item.GetProperty("display_name").GetString() ?? id;
            rows.Add(new ModRecord(id, package, path, type, display, item.GetProperty("size").GetInt64(), item.GetProperty("modified_ms").GetInt64()));
        }
        return new ModScanResult(rows, [], rows.Count, 0, ScanStatus.Completed, null);
    }

    private static bool NeedsMetadata(ModRecord row) => IsDefault(row.DisplayName, row.ModId);

    private static bool IsDefault(string value, string modId) =>
        string.IsNullOrWhiteSpace(value) || string.Equals(value, modId, StringComparison.OrdinalIgnoreCase);

    private static bool IsUsablePackageName(string value, string modId)
    {
        if (string.IsNullOrWhiteSpace(value) || string.Equals(value, modId, StringComparison.OrdinalIgnoreCase)) return false;
        var normalized = value.Trim().TrimStart('.');
        return normalized.Length > 0 && !new[] { "manifest", "package_name", "mods_info", "nameless", "mod_package" }
            .Contains(normalized, StringComparer.OrdinalIgnoreCase);
    }
}
