using ETS2ModManager.Contracts;

namespace ETS2ModManager.Application;

public interface IModScanner
{
    Task<ModScanResult> ScanAsync(
        IProgress<ProgressEvent>? progress,
        CancellationToken cancellationToken);
}

public interface IGameState
{
    bool IsRunning();
}

public interface IProfileRepository
{
    IReadOnlyList<string> ReadActiveMods(ProfileRef profile);
    void ReplaceActiveMods(ProfileRef profile, IReadOnlyList<string> activeMods, bool verify);
    ProfileRef Copy(ProfileRef profile, string displayName, string companyName);
    void Delete(ProfileRef profile, bool backupFirst);
}

public interface IBackupStore
{
    string? Backup(string source, string tag);
}
