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
    IReadOnlyList<ProfileRef> ListProfiles();
    IReadOnlyList<string> ReadActiveMods(ProfileRef profile);
    void ReplaceActiveMods(ProfileRef profile, IReadOnlyList<string> activeMods, bool verify);
    ProfileRef Copy(ProfileRef profile, string displayName, string companyName);
    ProfileRef Rename(ProfileRef profile, string displayName, string companyName);
    void CopySettings(ProfileRef source, ProfileRef destination, bool activeMods, bool controls);
    void Delete(ProfileRef profile, bool backupFirst);
}

public interface IBackupStore
{
    string? Backup(string source, string tag);
}

public interface ICrashDiagnosisService
{
    CrashPair DiscoverLatestCrashPair();
    CrashAnalysis Analyze(string? crashPath, string? logPath, IReadOnlyList<ModRecord> mods);
    PrecheckReport Precheck(ProfileRef profile, IReadOnlyList<string> activeMods, IReadOnlyList<ModRecord> mods, CancellationToken cancellationToken);
}

public interface ISaveEditorService
{
    IReadOnlyList<SaveSlotRef> ListSlots(ProfileRef profile);
    SaveSnapshot ReadSnapshot(SaveSlotRef slot);
    SaveMutationResult SetMoney(SaveSlotRef slot, long amount);
    SaveMutationResult SetExperience(SaveSlotRef slot, uint experience);
    SaveMutationResult SetLevel(SaveSlotRef slot, int level);
    SaveMutationResult RepairTruck(SaveSlotRef slot);
    SaveMutationResult RefuelTruck(SaveSlotRef slot, float amount);
    SaveMutationResult UnlockAllGarages(SaveSlotRef slot);
    SaveMutationResult UnlockAllDealers(SaveSlotRef slot);
    SaveSlotRef CopySlot(SaveSlotRef slot, string displayName);
}

public interface ILocalizationService
{
    Task<LocalizationScanResult> ScanAsync(IEnumerable<string> packagePaths, IProgress<ProgressEvent>? progress, CancellationToken cancellationToken);
    string TargetLocale { get; }
    string? BaselinePackagePath { get; }
    bool SetTargetLocale(string locale);
    bool SetBaselinePackage(string path);
    void ClearBaselinePackage();
    void SetUflPackages(IEnumerable<string> paths);
    IReadOnlyDictionary<string, string> ReadDictionary();
    LocalizationDictionaryImportResult ImportDictionary(string path, bool merge);
    void SetTranslation(string key, string value);
    void ClearTranslation(string key);
    Task<LocalizationExportResult> ExportAsync(
        IEnumerable<LocalizationEntry> entries,
        string outputPath,
        string targetLocale,
        string modName,
        CancellationToken cancellationToken);
}

public interface ILinkMigrationService
{
    LinkMigrationResult Migrate(string sourceDirectory, string targetDirectory);
    LinkMigrationResult Restore(string sourceDirectory, string targetDirectory);
}

public interface IUpdateService
{
    Task<UpdateInfo> CheckAsync(string currentVersion, string repository, CancellationToken cancellationToken);
    Task<UpdateInstallResult> DownloadAndInstallAsync(UpdateInfo update, string installDirectory, string executableName, CancellationToken cancellationToken);
}

public interface IGameLauncherService
{
    Task<GameLaunchResult> LaunchAndWaitAsync(string executable, string documentsDirectory, CancellationToken cancellationToken);
}

public interface ICategoryService
{
    CategoryState Snapshot();
    CategoryMutationResult CreateFolder(string name);
    CategoryMutationResult RenameFolder(string oldName, string newName);
    CategoryMutationResult DeleteFolder(string name);
    CategoryMutationResult SetCategory(string modId, string category);
    string GetCategory(string modId);
    IReadOnlySet<string> ModsInCategory(string category);
    void Touch(IEnumerable<ModRecord> mods);
}

public interface ICityLookupService
{
    Task<IReadOnlyList<CitySearchResult>> RebuildAndSearchAsync(IEnumerable<ModRecord> mods, string keyword, CancellationToken cancellationToken);
}

public interface IWorkshopMetadataService
{
    Task<IReadOnlyList<WorkshopMetadata>> FetchAsync(IEnumerable<string> workshopIds, CancellationToken cancellationToken);
}

public interface IExternalArchiveService
{
    ExtractorAvailability Availability { get; }
    Task<ExtractionResult> ExtractManifestAsync(string packagePath, CancellationToken cancellationToken);
    Task<ExtractionResult> ExtractFileAsync(string packagePath, string entryName, CancellationToken cancellationToken);
    Task<ExtractionResult> ExtractTreeAsync(string packagePath, string destinationDirectory, IReadOnlyList<string> roots, CancellationToken cancellationToken);
}

public sealed record ModPreset(string Name, IReadOnlyList<string> ActiveMods);

public interface IModPresetService
{
    IReadOnlyList<ModPreset> List(string profileId);
    void Save(string profileId, string name, IReadOnlyList<string> activeMods);
    IReadOnlyList<string>? Load(string profileId, string name);
    bool Delete(string profileId, string name);
}
