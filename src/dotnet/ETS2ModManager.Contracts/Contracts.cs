namespace ETS2ModManager.Contracts;

public enum ScanStatus
{
    Running,
    Completed,
    Cancelled,
    Failed,
}

public sealed record ProgressEvent(
    string Operation,
    string Phase,
    int Current,
    int Total,
    string Item,
    ScanStatus Status,
    string Message);

public sealed record ModRecord(
    string ModId,
    string PackageName,
    string PackagePath,
    string PackageType,
    string DisplayName,
    long FileSize,
    long LastModifiedUnixMilliseconds);

public sealed record ModScanResult(
    IReadOnlyList<ModRecord> Mods,
    IReadOnlyList<string> NewModIds,
    int ScannedCount,
    long ElapsedMilliseconds,
    ScanStatus Status,
    string? Error);

public sealed record ProfileRef(
    string ProfileId,
    string Location,
    string Folder,
    string ProfileSii,
    string DisplayName,
    string CompanyName,
    int ModCount)
{
    public bool IsWritable => string.Equals(Location, "local", StringComparison.OrdinalIgnoreCase);
}

public sealed record CrashPair(string? CrashPath, string? LogPath, string? Source);
public sealed record CrashSuspect(string ModId, string DisplayName, string Suspicion, int? PriorityIndex, IReadOnlyList<string> Evidence);
public sealed record CrashAnalysis(
    string CrashTime,
    string BuildVersion,
    string ExceptionCode,
    string FaultModuleCategory,
    IReadOnlyList<CrashSuspect> Suspects,
    int FailedToMatch,
    IReadOnlyList<string> RawTailLines);
public sealed record PrecheckIssue(
    string ModId,
    string DisplayName,
    string Severity,
    string Layer,
    string Code,
    string Evidence,
    string Suggestion,
    int? PriorityIndex);
public sealed record PrecheckReport(
    string ProfileId,
    int ScannedMods,
    int TotalIssues,
    int RedCount,
    int YellowCount,
    IReadOnlyList<PrecheckIssue> Issues,
    long ElapsedMilliseconds);

public sealed record SaveSlotRef(string ProfileId, string SlotId, string Folder, string GameSii, string DisplayName, DateTime LastModifiedUtc, string ProfileLocation = "local")
{
    public bool IsWritable => string.Equals(ProfileLocation, "local", StringComparison.OrdinalIgnoreCase);
}
public sealed record SaveFieldValue(string FieldName, string Value, string Type, long Offset);
public sealed record SaveSnapshot(SaveSlotRef Slot, IReadOnlyList<SaveFieldValue> Fields);
public sealed record SaveMutationResult(bool Success, string Operation, string Message, string? BackupPath);

public sealed record LocalizationEntry(
    string Key,
    string Value,
    string SourcePath,
    string PackageName,
    string Category = "unknown",
    string Status = "pending",
    bool LocaleKeyPresent = true,
    bool DefLocaleKeyPresent = true,
    string MatchedKey = "",
    string UnitName = "",
    string LocaleKey = "");
public sealed record LocalizationScanResult(IReadOnlyList<LocalizationEntry> Entries, int ScannedPackages, long ElapsedMilliseconds);
public sealed record LocalizationDictionaryImportResult(int Imported, int Skipped, IReadOnlyList<string> Messages);
public sealed record LocalizationExportResult(bool Success, string OutputPath, int EntriesWritten, string Message);

public sealed record LinkMigrationResult(bool Success, string Operation, string SourcePath, string TargetPath, string Message);
public sealed record UpdateInfo(bool IsAvailable, string CurrentVersion, string? LatestVersion, string? DownloadUrl, string Message);
public sealed record UpdateInstallResult(bool Success, string Message, string? StagedDirectory);
public sealed record GameLaunchResult(bool Started, bool Crashed, string? CrashPath, string? LogPath, int? ExitCode, string Message);

public sealed record CategoryState(IReadOnlyList<string> Folders, IReadOnlyDictionary<string, int> Stats);
public sealed record CategoryMutationResult(string Operation, bool Success, int AffectedMods, bool Conflict, string? Error);
public sealed record CityHit(string CityName, string ShortName, string Country, string ModId, string ModTitle, string PackagePath, int PriorityIndex);
public sealed record CitySearchResult(string CityName, IReadOnlyList<CityHit> Sources);
public sealed record WorkshopMetadata(string WorkshopId, string Title, string? PreviewUrl, string? Description);
public sealed record ExtractorAvailability(bool Legacy, bool Modern, bool Any);
public sealed record ExtractionResult(bool Success, string? Text, byte[]? Bytes, string Message);
