using System.Diagnostics;
using System.IO.Compression;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using ETS2ModManager.Application;
using ETS2ModManager.Contracts;
using ETS2ModManager.Infrastructure.Archives;
using ETS2ModManager.Infrastructure.Indexing;

namespace ETS2ModManager.Infrastructure.Localization;

/// <summary>
/// File-backed localization workflow. Scanning is kept independent from the
/// UI, while dictionary editing and generated-SCS export use the same port.
/// </summary>
public sealed class FileLocalizationService : ILocalizationService
{
    private const int LocalizationScanVersion = 1;
    private static readonly Regex ArrayKey = new(
        "(?m)key\\[\\]\\s*:\\s*[\\\"](?<key>(?:\\\\.|[^\\\"\\\\])*)[\\\"]",
        RegexOptions.Compiled);
    private static readonly Regex ArrayValue = new(
        "(?m)val\\[\\]\\s*:\\s*[\\\"](?<value>(?:\\\\.|[^\\\"\\\\])*)[\\\"]",
        RegexOptions.Compiled);
    private static readonly Regex ScalarEntry = new(
        "(?m)^\\s*[\\\"']?(?<key>[A-Za-z0-9_.@/\\-]+)[\\\"']?\\s*:\\s*[\\\"](?<value>(?:\\\\.|[^\\\"\\\\])*)[\\\"]",
        RegexOptions.Compiled);
    private static readonly Regex DefinitionUnit = new(
        "(?is)(?<type>city_data|country_data|ferry_data)\\s*:\\s*(?<unit>[A-Za-z0-9_.-]+)\\s*\\{(?<body>.*?)\\}",
        RegexOptions.Compiled);
    private static readonly Regex DefinitionField = new(
        "(?m)(?<key>city_name|city_name_localized|name|name_localized|ferry_name|ferry_name_localized)\\s*:\\s*[\\\"](?<value>(?:\\\\.|[^\\\"\\\\])*)[\\\"]",
        RegexOptions.Compiled);

    private readonly object _sync = new();
    private readonly string _dictionaryPath;
    private readonly IExternalArchiveService _archiveService;
    private readonly SqliteLocalizationIndex? _index;
    private Dictionary<string, string> _dictionary = new(StringComparer.OrdinalIgnoreCase);
    private Dictionary<string, string> _baselineDictionary = new(StringComparer.OrdinalIgnoreCase);
    private Dictionary<string, string> _uflDictionary = new(StringComparer.OrdinalIgnoreCase);
    private string _targetLocale = "zh_cn";
    private string? _baselinePackagePath;

    public string TargetLocale => _targetLocale;
    public string? BaselinePackagePath => _baselinePackagePath;

    public FileLocalizationService(
        string? dictionaryPath = null,
        IExternalArchiveService? archiveService = null,
        SqliteLocalizationIndex? index = null)
    {
        _dictionaryPath = dictionaryPath ?? Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "ETS2ModManager", "l10n_dict.json");
        _archiveService = archiveService ?? new ExternalArchiveService();
        _index = index;
        LoadDictionary();
    }

    public bool SetTargetLocale(string locale)
    {
        locale = (locale ?? string.Empty).Trim().ToLowerInvariant();
        if (!Regex.IsMatch(locale, "^[a-z]{2}_[a-z]{2}$", RegexOptions.CultureInvariant)) return false;
        _targetLocale = locale;
        if (!string.IsNullOrWhiteSpace(_baselinePackagePath))
            _baselineDictionary = LoadLocaleDictionaryCached(_baselinePackagePath, _targetLocale);
        return true;
    }

    public bool SetBaselinePackage(string path)
    {
        if (string.IsNullOrWhiteSpace(path) || (!Directory.Exists(path) && !File.Exists(path))) return false;
        var values = LoadLocaleDictionaryCached(path, _targetLocale);
        if (values.Count == 0) return false;
        _baselinePackagePath = Path.GetFullPath(path);
        _baselineDictionary = values;
        return true;
    }

    public void ClearBaselinePackage()
    {
        _baselinePackagePath = null;
        _baselineDictionary = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
    }

    public void SetUflPackages(IEnumerable<string> paths)
    {
        var merged = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var path in paths.Where(path => !string.IsNullOrWhiteSpace(path)))
        {
            foreach (var pair in LoadLocaleDictionaryCached(path, _targetLocale)) merged[pair.Key] = pair.Value;
        }
        _uflDictionary = merged;
    }

    public Task<LocalizationScanResult> ScanAsync(
        IEnumerable<string> packagePaths,
        IProgress<ProgressEvent>? progress,
        CancellationToken cancellationToken) =>
        Task.Run(() => Scan(packagePaths, progress, cancellationToken), cancellationToken);

    public IReadOnlyDictionary<string, string> ReadDictionary()
    {
        lock (_sync)
            return new Dictionary<string, string>(_dictionary, StringComparer.OrdinalIgnoreCase);
    }

    public void SetTranslation(string key, string value)
    {
        key = (key ?? string.Empty).Trim();
        value = NormalizeTranslation(value);
        var issues = Validate(key, value);
        if (issues.Count > 0) throw new ArgumentException(string.Join(" ", issues), nameof(value));
        lock (_sync) _dictionary[key] = value;
        SaveDictionary();
    }

    public void ClearTranslation(string key)
    {
        lock (_sync) _dictionary.Remove((key ?? string.Empty).Trim());
        SaveDictionary();
    }

    public LocalizationDictionaryImportResult ImportDictionary(string path, bool merge)
    {
        var messages = new List<string>();
        var pairs = new List<(string Key, string Value)>();
        if (string.IsNullOrWhiteSpace(path) || !File.Exists(path))
            return new LocalizationDictionaryImportResult(0, 0, [$"File does not exist: {path}"]);

        try
        {
            switch (Path.GetExtension(path).ToLowerInvariant())
            {
                case ".json":
                    using (var document = JsonDocument.Parse(File.ReadAllText(path, Encoding.UTF8)))
                    {
                        if (document.RootElement.ValueKind != JsonValueKind.Object)
                            messages.Add("JSON top level must be an object.");
                        else
                            foreach (var property in document.RootElement.EnumerateObject())
                                pairs.Add((property.Name, property.Value.ToString()));
                    }
                    break;
                case ".csv":
                    var csvLine = 0;
                    foreach (var line in File.ReadLines(path, Encoding.UTF8))
                    {
                        csvLine++;
                        var columns = ParseCsvLine(line);
                        if (columns.Count >= 2) pairs.Add((columns[0], columns[1]));
                        else if (columns.Count == 1 && !string.IsNullOrWhiteSpace(columns[0]))
                            messages.Add($"Warning: CSV line {csvLine} has no value column.");
                    }
                    break;
                case ".txt":
                    var textLine = 0;
                    foreach (var line in File.ReadLines(path, Encoding.UTF8))
                    {
                        textLine++;
                        if (string.IsNullOrWhiteSpace(line)) continue;
                        var separator = line.IndexOf('=');
                        if (separator < 0) separator = line.IndexOf(':');
                        if (separator <= 0)
                        {
                            messages.Add($"Warning: TXT line {textLine} has no '=' or ':' separator.");
                            continue;
                        }
                        pairs.Add((line[..separator].Trim(), line[(separator + 1)..].Trim()));
                    }
                    break;
                default:
                    return new LocalizationDictionaryImportResult(0, 0,
                        [$"Unsupported dictionary format: {Path.GetExtension(path)}"]);
            }
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or JsonException)
        {
            return new LocalizationDictionaryImportResult(0, 0, [$"Failed to read dictionary: {error.Message}"]);
        }

        var valid = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        var skipped = 0;
        foreach (var (rawKey, rawValue) in pairs)
        {
            var key = (rawKey ?? string.Empty).Trim();
            var value = NormalizeTranslation(rawValue);
            var issues = Validate(key, value);
            foreach (var issue in issues) messages.Add($"[{key}] {issue}");
            if (issues.Any(issue => issue.StartsWith("Error", StringComparison.OrdinalIgnoreCase)) || key.Length == 0 || value.Length == 0)
            {
                skipped++;
                continue;
            }
            valid[key] = value;
        }

        lock (_sync)
        {
            if (!merge) _dictionary = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (var pair in valid) _dictionary[pair.Key] = pair.Value;
        }
        if (valid.Count > 0) SaveDictionary();
        return new LocalizationDictionaryImportResult(valid.Count, skipped, messages);
    }

    public Task<LocalizationExportResult> ExportAsync(
        IEnumerable<LocalizationEntry> entries,
        string outputPath,
        string targetLocale,
        string modName,
        CancellationToken cancellationToken) =>
        Task.Run(() => Export(entries, outputPath, targetLocale, modName, cancellationToken), cancellationToken);

    private LocalizationScanResult Scan(
        IEnumerable<string> packagePaths,
        IProgress<ProgressEvent>? progress,
        CancellationToken cancellationToken)
    {
        var stopwatch = Stopwatch.StartNew();
        var packageEntries = new List<IReadOnlyList<LocalizationEntry>>();
        var pendingSaves = new List<(string Path, (string PackageType, long FileSize, long ModifiedAtMs) Fingerprint, IReadOnlyList<LocalizationEntry> Entries)>();
        var packages = packagePaths
            .Where(path => !string.IsNullOrWhiteSpace(path))
            .Select(Path.GetFullPath)
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();

        for (var packageIndex = 0; packageIndex < packages.Length; packageIndex++)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var package = packages[packageIndex];
            cancellationToken.ThrowIfCancellationRequested();
            var fingerprint = Fingerprint(package);
            if (_index is not null && _index.TryLoad(
                    package,
                    _targetLocale,
                    fingerprint.PackageType,
                    fingerprint.FileSize,
                    fingerprint.ModifiedAtMs,
                    LocalizationScanVersion,
                    out var cachedEntries))
            {
                progress?.Report(new ProgressEvent("localization", "cache", packageIndex, packages.Length, package,
                    ScanStatus.Running, $"Using cached localization: {Path.GetFileName(package)}"));
                packageEntries.Add(cachedEntries);
                continue;
            }

            var entries = new List<LocalizationEntry>();
            progress?.Report(new ProgressEvent("localization", "scan", packageIndex, packages.Length, package,
                ScanStatus.Running, $"Scanning localization: {Path.GetFileName(package)}"));
            if (Directory.Exists(package)) ScanDirectory(package, entries, cancellationToken, package);
            else if (File.Exists(package) && IsArchive(package)) ScanArchive(package, entries, cancellationToken);
            pendingSaves.Add((package, fingerprint, entries));
            packageEntries.Add(entries);
        }

        // Do not persist package snapshots until every package has been read.
        // If cancellation happens during extraction, the previous complete
        // snapshot remains intact and the next scan cannot consume a partial one.
        cancellationToken.ThrowIfCancellationRequested();
        if (_index is not null)
        {
            foreach (var pending in pendingSaves)
            {
                _index.Save(
                    pending.Path,
                    _targetLocale,
                    pending.Fingerprint.PackageType,
                    pending.Fingerprint.FileSize,
                    pending.Fingerprint.ModifiedAtMs,
                    LocalizationScanVersion,
                    pending.Entries);
            }
        }
        _index?.RemoveExcept(_targetLocale, packages.ToHashSet(StringComparer.OrdinalIgnoreCase));
        var merged = MergeEntries(packageEntries);
        stopwatch.Stop();
        progress?.Report(new ProgressEvent("localization", "complete", packages.Length, packages.Length, "",
            ScanStatus.Completed, $"Localization scan completed: {merged.Count} entries."));
        return new LocalizationScanResult(merged, packages.Length, stopwatch.ElapsedMilliseconds);
    }

    private void ScanDirectory(
        string root,
        List<LocalizationEntry> output,
        CancellationToken cancellationToken,
        string? packagePath = null)
    {
        packagePath ??= root;
        var packageName = Path.GetFileName(packagePath.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar));
        IEnumerable<string> files;
        try { files = Directory.EnumerateFiles(root, "*", SearchOption.AllDirectories); }
        catch (Exception) { return; }
        foreach (var file in files.OrderBy(path => path, StringComparer.OrdinalIgnoreCase))
        {
            cancellationToken.ThrowIfCancellationRequested();
            var normalized = file.Replace('\\', '/');
            if (!IsLocalizationPath(normalized) && !IsDefinitionPath(normalized)) continue;
            try
            {
                var text = File.ReadAllText(file, Encoding.UTF8);
                var relative = Path.GetRelativePath(root, file).Replace('\\', '/');
                var sourcePath = $"{packagePath}::{relative}";
                if (IsLocalizationPath(normalized)) ParseText(text, sourcePath, packageName, output);
                else ParseDefinitions(text, sourcePath, packageName, output);
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }
    }

    private void ScanArchive(string path, List<LocalizationEntry> output, CancellationToken cancellationToken)
    {
        var completedZipRead = false;
        try
        {
            using var archive = ZipFile.OpenRead(path);
            foreach (var entry in archive.Entries.OrderBy(item => item.FullName, StringComparer.OrdinalIgnoreCase))
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (string.IsNullOrEmpty(entry.Name)) continue;
                var normalized = entry.FullName.Replace('\\', '/');
                if (!IsLocalizationPath(normalized) && !IsDefinitionPath(normalized)) continue;
                using var stream = entry.Open();
                using var reader = new StreamReader(stream, Encoding.UTF8, detectEncodingFromByteOrderMarks: true);
                var text = reader.ReadToEnd();
                var sourcePath = $"{path}::{entry.FullName.Replace('\\', '/')}";
                if (IsLocalizationPath(normalized)) ParseText(text, sourcePath, Path.GetFileName(path), output);
                else ParseDefinitions(text, sourcePath, Path.GetFileName(path), output);
            }
            completedZipRead = true;
        }
        catch (InvalidDataException) { }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
        if (completedZipRead)
            return;

        // SCS#/AEM/encrypted ZIP packages cannot be opened by ZipArchive. Extract
        // only the trees needed by localization, then reuse the directory parser.
        var temporary = Path.Combine(Path.GetTempPath(), "ets2mm-l10n-" + Guid.NewGuid().ToString("N"));
        try
        {
            Directory.CreateDirectory(temporary);
            var result = _archiveService.ExtractTreeAsync(
                path,
                temporary,
                ["def", $"locale/{_targetLocale}"],
                cancellationToken).GetAwaiter().GetResult();
            if (result.Success) ScanDirectory(temporary, output, cancellationToken, path);
        }
        catch (OperationCanceledException) { throw; }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
        catch (InvalidOperationException) { }
        finally
        {
            try { Directory.Delete(temporary, recursive: true); } catch { }
        }
    }

    private void ParseText(string text, string sourcePath, string packageName, List<LocalizationEntry> output)
    {
        var keys = ArrayKey.Matches(text).Select(match => Unescape(match.Groups["key"].Value)).ToArray();
        var values = ArrayValue.Matches(text).Select(match => Unescape(match.Groups["value"].Value)).ToArray();
        for (var index = 0; index < keys.Length; index++)
        {
            var key = keys[index];
            if (key.Length == 0) continue;
            output.Add(RawEntry(key, index < values.Length ? values[index] : string.Empty, sourcePath, packageName, true));
        }
        foreach (Match match in ScalarEntry.Matches(text))
        {
            var key = Unescape(match.Groups["key"].Value);
            if (key.Length == 0 || key.Contains("[]", StringComparison.Ordinal)) continue;
            var value = Unescape(match.Groups["value"].Value);
            output.Add(RawEntry(key, value, sourcePath, packageName, true));
        }
    }

    private void ParseDefinitions(string text, string sourcePath, string packageName, List<LocalizationEntry> output)
    {
        foreach (Match unit in DefinitionUnit.Matches(text))
        {
            var type = unit.Groups["type"].Value.ToLowerInvariant();
            var unitName = unit.Groups["unit"].Value;
            var fields = DefinitionField.Matches(unit.Groups["body"].Value)
                .ToDictionary(match => match.Groups["key"].Value, match => Unescape(match.Groups["value"].Value), StringComparer.OrdinalIgnoreCase);
            var sourceField = type switch
            {
                "city_data" => "city_name",
                "country_data" => "name",
                _ => "ferry_name",
            };
            var localizedField = type switch
            {
                "city_data" => "city_name_localized",
                "country_data" => "name_localized",
                _ => "ferry_name_localized",
            };
            if (!fields.TryGetValue(sourceField, out var source) || string.IsNullOrWhiteSpace(source)) source = unitName;
            var localized = fields.GetValueOrDefault(localizedField, string.Empty);
            var defPresent = fields.ContainsKey(localizedField);
            var key = ExtractLocaleKey(localized, source);
            if (key.Length == 0) continue;
            var directValue = IsWrappedLocale(localized) || string.IsNullOrWhiteSpace(localized) ? string.Empty : localized;
            var category = type switch { "country_data" => "country", "ferry_data" => "ferry", _ => "city" };
            var resolved = directValue.Length > 0
                ? new LocalizationEntry(key, directValue, sourcePath, packageName, category, "native", false, defPresent, key, unitName, key)
                : new LocalizationEntry(key, string.Empty, sourcePath, packageName, category, "missing_locale", false, defPresent, key, unitName, key);
            output.Add(resolved);
        }
    }

    private static string ExtractLocaleKey(string localized, string fallback) =>
        IsWrappedLocale(localized) ? localized[2..^2] : (string.IsNullOrWhiteSpace(localized) ? fallback : fallback);

    private static bool IsWrappedLocale(string value) =>
        !string.IsNullOrWhiteSpace(value) && value.StartsWith("@@", StringComparison.Ordinal) && value.EndsWith("@@", StringComparison.Ordinal) && value.Length > 4;

    private static LocalizationEntry RawEntry(string key, string nativeValue, string sourcePath, string packageName, bool localeKeyPresent) =>
        new(
            key,
            nativeValue,
            sourcePath,
            packageName,
            GuessCategory(sourcePath),
            nativeValue.Length > 0 ? "native" : localeKeyPresent ? "missing_value" : "missing_locale",
            localeKeyPresent,
            true,
            key,
            "",
            key);

    private LocalizationEntry Resolve(
        LocalizationEntry value,
        LocalizationEntry? locale,
        LocalizationEntry? definition)
    {
        var output = value with
        {
            Category = definition is not null && definition.Category != "unknown"
                ? definition.Category : value.Category,
            LocaleKeyPresent = locale?.LocaleKeyPresent ?? value.LocaleKeyPresent,
            DefLocaleKeyPresent = definition?.DefLocaleKeyPresent ?? value.DefLocaleKeyPresent,
            UnitName = definition?.UnitName ?? value.UnitName,
            LocaleKey = definition is not null && definition.LocaleKey.Length > 0
                ? definition.LocaleKey : value.LocaleKey,
        };

        // A direct definition value historically bypasses dictionary/baseline
        // resolution. Preserve that behavior when no locale component exists.
        if (locale is null && definition is not null && definition.Value.Length > 0)
            return output with { Value = definition.Value, Status = "native" };

        var nativeValue = locale?.Value ?? definition?.Value ?? string.Empty;
        lock (_sync)
        {
            if (_baselineDictionary.TryGetValue(output.Key, out var baselineValue) && baselineValue.Length > 0)
                return output with { Value = baselineValue, Status = "baseline" };
            if (nativeValue.Length > 0)
                return output with { Value = nativeValue, Status = "native" };
            if (_dictionary.TryGetValue(output.Key, out var localValue) && localValue.Length > 0)
                return output with { Value = localValue, Status = "local" };
            if (_uflDictionary.TryGetValue(output.Key, out var uflValue) && uflValue.Length > 0)
                return output with { Value = uflValue, Status = "ufl" };
        }
        return output with
        {
            Value = string.Empty,
            Status = output.LocaleKeyPresent ? "missing_value" : "missing_locale",
        };
    }

    private Dictionary<string, string> LoadLocaleDictionary(string path, string locale)
    {
        var values = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        var prefix = $"locale/{locale}/";
        var completedZipRead = false;
        try
        {
            if (Directory.Exists(path))
            {
                var root = Path.Combine(path, "locale", locale);
                if (!Directory.Exists(root)) return values;
                foreach (var file in Directory.EnumerateFiles(root, "*", SearchOption.AllDirectories))
                    AddPairs(File.ReadAllText(file, Encoding.UTF8), values);
                return values;
            }
            using var archive = ZipFile.OpenRead(path);
            foreach (var entry in archive.Entries)
            {
                var name = entry.FullName.Replace('\\', '/').TrimStart('.', '/');
                if (!name.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) continue;
                if (!name.EndsWith(".sii", StringComparison.OrdinalIgnoreCase) && !name.EndsWith(".sui", StringComparison.OrdinalIgnoreCase)) continue;
                using var reader = new StreamReader(entry.Open(), Encoding.UTF8, true);
                AddPairs(reader.ReadToEnd(), values);
            }
            completedZipRead = true;
        }
        catch (InvalidDataException) { }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }

        if (completedZipRead) return values;

        if (File.Exists(path))
        {
            var temporary = Path.Combine(Path.GetTempPath(), "ets2mm-l10n-dict-" + Guid.NewGuid().ToString("N"));
            try
            {
                Directory.CreateDirectory(temporary);
                var result = _archiveService.ExtractTreeAsync(path, temporary, [$"locale/{locale}"], CancellationToken.None)
                    .GetAwaiter().GetResult();
                if (result.Success)
                {
                    var localeRoot = Path.Combine(temporary, "locale", locale);
                    if (Directory.Exists(localeRoot))
                    {
                        foreach (var file in Directory.EnumerateFiles(localeRoot, "*", SearchOption.AllDirectories))
                        {
                            if (!file.EndsWith(".sii", StringComparison.OrdinalIgnoreCase)
                                && !file.EndsWith(".sui", StringComparison.OrdinalIgnoreCase)) continue;
                            AddPairs(File.ReadAllText(file, Encoding.UTF8), values);
                        }
                    }
                }
            }
            catch (Exception) { }
            finally
            {
                try { Directory.Delete(temporary, recursive: true); } catch { }
            }
        }
        return values;
    }

    private Dictionary<string, string> LoadLocaleDictionaryCached(string path, string locale)
    {
        path = Path.GetFullPath(path);
        if (_index is not null)
        {
            var fingerprint = Fingerprint(path);
            if (_index.TryLoad(
                    path,
                    locale,
                    fingerprint.PackageType,
                    fingerprint.FileSize,
                    fingerprint.ModifiedAtMs,
                    LocalizationScanVersion,
                    out var entries))
            {
                var cached = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                foreach (var entry in entries.Where(entry => entry.UnitName.Length == 0))
                    cached[entry.Key] = entry.Value;
                return cached;
            }
        }
        return LoadLocaleDictionary(path, locale);
    }

    private IReadOnlyList<LocalizationEntry> MergeEntries(
        IReadOnlyList<IReadOnlyList<LocalizationEntry>> packages)
    {
        var merged = new Dictionary<string, LocalizationParts>(StringComparer.OrdinalIgnoreCase);
        var order = new List<string>();
        foreach (var entries in packages)
        {
            var package = new Dictionary<string, LocalizationParts>(StringComparer.OrdinalIgnoreCase);
            foreach (var incoming in entries)
            {
                if (!package.TryGetValue(incoming.Key, out var parts))
                    package[incoming.Key] = parts = new LocalizationParts();
                if (incoming.UnitName.Length > 0) parts.Definition = incoming;
                else parts.Locale = incoming;
            }
            foreach (var (key, incoming) in package)
            {
                if (!merged.TryGetValue(key, out var current))
                {
                    merged[key] = current = new LocalizationParts();
                    order.Add(key);
                }
                // Packages arrive from highest to lowest UI priority. The
                // first locale/definition component wins independently.
                current.Locale ??= incoming.Locale;
                current.Definition ??= incoming.Definition;
            }
        }

        var result = new List<LocalizationEntry>(merged.Count);
        foreach (var key in order)
        {
            var parts = merged[key];
            var value = parts.Locale ?? parts.Definition;
            if (value is null) continue;
            var definition = parts.Definition;
            result.Add(Resolve(value, parts.Locale, definition));
        }
        return result;
    }

    private static (string PackageType, long FileSize, long ModifiedAtMs) Fingerprint(string package)
    {
        if (Directory.Exists(package))
        {
            var hash = 1469598103934665603UL;
            Mix(ref hash, package);
            try
            {
                foreach (var file in Directory.EnumerateFiles(package, "*", SearchOption.AllDirectories)
                    .OrderBy(path => path, StringComparer.OrdinalIgnoreCase))
                {
                    var normalized = file.Replace('\\', '/');
                    if (!IsLocalizationPath(normalized) && !IsDefinitionPath(normalized)) continue;
                    var info = new FileInfo(file);
                    Mix(ref hash, Path.GetRelativePath(package, file).Replace('\\', '/'));
                    MixValue(ref hash, (ulong)info.Length);
                    MixValue(ref hash, (ulong)info.LastWriteTimeUtc.Ticks);
                }
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
            return ("directory", 0, unchecked((long)hash));
        }

        if (File.Exists(package))
        {
            var info = new FileInfo(package);
            var packageType = IsArchive(package) ? Path.GetExtension(package).TrimStart('.').ToLowerInvariant() : "file";
            return (packageType, info.Length, new DateTimeOffset(info.LastWriteTimeUtc).ToUnixTimeMilliseconds());
        }

        return ("missing", 0, 0);

        static void Mix(ref ulong hash, string value)
        {
            foreach (var character in value)
            {
                hash ^= character;
                hash *= 1099511628211UL;
            }
        }

        static void MixValue(ref ulong hash, ulong value)
        {
            for (var index = 0; index < sizeof(ulong); index++)
            {
                hash ^= (byte)(value >> (index * 8));
                hash *= 1099511628211UL;
            }
        }
    }

    private sealed class LocalizationParts
    {
        public LocalizationEntry? Locale { get; set; }
        public LocalizationEntry? Definition { get; set; }
    }

    private static void AddPairs(string text, Dictionary<string, string> values)
    {
        var keys = ArrayKey.Matches(text).Select(match => Unescape(match.Groups["key"].Value)).ToArray();
        var vals = ArrayValue.Matches(text).Select(match => Unescape(match.Groups["value"].Value)).ToArray();
        for (var index = 0; index < keys.Length; index++)
            if (keys[index].Length > 0) values[keys[index]] = index < vals.Length ? vals[index] : string.Empty;
    }

    private LocalizationExportResult Export(
        IEnumerable<LocalizationEntry> entries,
        string outputPath,
        string targetLocale,
        string modName,
        CancellationToken cancellationToken)
    {
        var path = Path.GetFullPath(outputPath);
        var locale = string.IsNullOrWhiteSpace(targetLocale) ? "zh_cn" : targetLocale.Trim().ToLowerInvariant();
        var translated = entries.Where(entry => !string.IsNullOrWhiteSpace(entry.Value)).ToArray();
        var temp = path + ".tmp-" + Guid.NewGuid().ToString("N");
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            using (var archive = ZipFile.Open(temp, ZipArchiveMode.Create))
            {
                cancellationToken.ThrowIfCancellationRequested();
                WriteEntry(archive, "manifest.sii", $"SiiNunit\n{{\nmod_package : .unnamed\n{{\n\tdisplay_name: \"{EscapeSii(modName)}\"\n\tauthor: \"ETS2ModManager\"\n\tcategory[]: \"map\"\n}}\n}}\n");
                WriteEntry(archive, "description.txt", $"{modName}\nGenerated by ETS2ModManager\n");
                var builder = new StringBuilder("SiiNunit\n{\nlocalization_db : .localization\n{\n");
                foreach (var entry in translated)
                {
                    cancellationToken.ThrowIfCancellationRequested();
                    builder.Append("\tkey[]: \"").Append(EscapeSii(entry.Key)).AppendLine("\"");
                    builder.Append("\tval[]: \"").Append(EscapeSii(entry.Value)).AppendLine("\"");
                }
                builder.Append("}\n}\n");
                WriteEntry(archive, $"locale/{locale}/generated.sii", builder.ToString());
                foreach (var group in translated.Where(entry => !entry.DefLocaleKeyPresent && entry.UnitName.Length > 0)
                    .GroupBy(entry => (Folder: entry.Category switch { "country" => "country", "ferry" => "ferry", _ => "city" }, entry.Category)))
                {
                    var folder = group.Key.Folder;
                    var type = folder switch { "country" => "country_data", "ferry" => "ferry_data", _ => "city_data" };
                    var localizedField = folder switch { "country" => "name_localized", "ferry" => "ferry_name_localized", _ => "city_name_localized" };
                    var def = new StringBuilder("SiiNunit\n{\n");
                    foreach (var item in group)
                    {
                        var key = string.IsNullOrWhiteSpace(item.LocaleKey) ? item.Key : item.LocaleKey;
                        def.Append(type).Append(" : ").Append(item.UnitName).AppendLine(" {");
                        def.Append("\t").Append(localizedField).Append(": \"@@").Append(EscapeSii(key)).AppendLine("@@\"");
                        def.AppendLine("}");
                    }
                    def.AppendLine("}");
                    WriteEntry(archive, $"def/{folder}/generated.sii", def.ToString());
                }
            }
            File.Move(temp, path, true);
            return new LocalizationExportResult(true, path, translated.Length, $"Exported {translated.Length} translations.");
        }
        catch (OperationCanceledException)
        {
            return new LocalizationExportResult(false, path, 0, "Localization export cancelled.");
        }
        catch (Exception error)
        {
            return new LocalizationExportResult(false, path, 0, $"Localization export failed: {error.Message}");
        }
        finally { try { File.Delete(temp); } catch { } }
    }

    private static void WriteEntry(ZipArchive archive, string name, string text)
    {
        var entry = archive.CreateEntry(name, CompressionLevel.Optimal);
        using var writer = new StreamWriter(entry.Open(), new UTF8Encoding(false));
        writer.Write(text);
    }

    private void LoadDictionary()
    {
        try
        {
            if (!File.Exists(_dictionaryPath)) return;
            using var document = JsonDocument.Parse(File.ReadAllText(_dictionaryPath, Encoding.UTF8));
            if (document.RootElement.ValueKind != JsonValueKind.Object) return;
            var loaded = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (var property in document.RootElement.EnumerateObject())
            {
                var value = NormalizeTranslation(property.Value.ToString());
                if (property.Name.Length > 0 && value.Length > 0) loaded[property.Name] = value;
            }
            _dictionary = loaded;
        }
        catch (Exception) { _dictionary = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase); }
    }

    private void SaveDictionary()
    {
        Dictionary<string, string> snapshot;
        lock (_sync) snapshot = new Dictionary<string, string>(_dictionary, StringComparer.OrdinalIgnoreCase);
        var directory = Path.GetDirectoryName(_dictionaryPath);
        if (!string.IsNullOrWhiteSpace(directory)) Directory.CreateDirectory(directory);
        var temp = _dictionaryPath + ".tmp-" + Guid.NewGuid().ToString("N");
        try
        {
            var json = JsonSerializer.Serialize(snapshot, new JsonSerializerOptions { WriteIndented = true });
            using var stream = new FileStream(temp, FileMode.CreateNew, FileAccess.Write, FileShare.None, 32 * 1024, FileOptions.WriteThrough);
            var bytes = Encoding.UTF8.GetBytes(json);
            stream.Write(bytes);
            stream.Flush(true);
            stream.Dispose();
            File.Move(temp, _dictionaryPath, true);
        }
        finally { try { File.Delete(temp); } catch { } }
    }

    private static string NormalizeTranslation(string value) =>
        (value ?? string.Empty).Split(['\r', '\n'], 2, StringSplitOptions.None)[0].Trim();

    private static List<string> Validate(string key, string value)
    {
        var issues = new List<string>();
        if (key.Length == 0) issues.Add("Error: key is empty.");
        if (key.Length > 200) issues.Add("Error: key exceeds 200 characters.");
        if (value.Length > 500) issues.Add("Error: value exceeds 500 characters.");
        if (key.Any(char.IsControl) || value.Any(char.IsControl)) issues.Add("Error: control characters are not allowed.");
        if (key.Contains("..", StringComparison.Ordinal) || value.Contains("SiiNunit", StringComparison.OrdinalIgnoreCase))
            issues.Add("Error: suspicious SII/path content.");
        return issues;
    }

    private static List<string> ParseCsvLine(string line)
    {
        var result = new List<string>();
        var current = new StringBuilder();
        var quoted = false;
        for (var index = 0; index < line.Length; index++)
        {
            var ch = line[index];
            if (ch == '"')
            {
                if (quoted && index + 1 < line.Length && line[index + 1] == '"') { current.Append('"'); index++; }
                else quoted = !quoted;
            }
            else if (ch == ',' && !quoted) { result.Add(current.ToString().Trim()); current.Clear(); }
            else current.Append(ch);
        }
        result.Add(current.ToString().Trim());
        return result;
    }

    private static bool IsArchive(string path) =>
        path.EndsWith(".scs", StringComparison.OrdinalIgnoreCase) || path.EndsWith(".zip", StringComparison.OrdinalIgnoreCase);

    private static bool IsLocalizationPath(string path)
    {
        var normalized = path.Replace('\\', '/');
        return (normalized.EndsWith(".sii", StringComparison.OrdinalIgnoreCase) || normalized.EndsWith(".sui", StringComparison.OrdinalIgnoreCase))
            && (normalized.Contains("local", StringComparison.OrdinalIgnoreCase)
                || normalized.Contains("locale", StringComparison.OrdinalIgnoreCase)
                || normalized.Contains("language", StringComparison.OrdinalIgnoreCase)
                || normalized.Contains("translation", StringComparison.OrdinalIgnoreCase));
    }

    private static bool IsDefinitionPath(string path)
    {
        var normalized = path.Replace('\\', '/');
        return (normalized.EndsWith(".sii", StringComparison.OrdinalIgnoreCase) || normalized.EndsWith(".sui", StringComparison.OrdinalIgnoreCase))
            && (normalized.StartsWith("def/", StringComparison.OrdinalIgnoreCase) || normalized.Contains("/def/", StringComparison.OrdinalIgnoreCase));
    }

    private static string GuessCategory(string sourcePath)
    {
        var value = sourcePath.ToLowerInvariant();
        if (value.Contains("city")) return "city";
        if (value.Contains("country")) return "country";
        if (value.Contains("ferry")) return "ferry";
        return "unknown";
    }

    private static string Unescape(string value) => (value ?? string.Empty)
        .Replace("\\r", "\r", StringComparison.Ordinal)
        .Replace("\\n", "\n", StringComparison.Ordinal)
        .Replace("\\t", "\t", StringComparison.Ordinal)
        .Replace("\\\"", "\"", StringComparison.Ordinal)
        .Replace("\\\\", "\\", StringComparison.Ordinal);

    private static string EscapeSii(string value) => (value ?? string.Empty)
        .Replace("\\", "\\\\", StringComparison.Ordinal)
        .Replace("\"", "\\\"", StringComparison.Ordinal)
        .Replace("\r", "\\r", StringComparison.Ordinal)
        .Replace("\n", "\\n", StringComparison.Ordinal)
        .Replace("\t", "\\t", StringComparison.Ordinal);
}
