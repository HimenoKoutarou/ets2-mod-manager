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
