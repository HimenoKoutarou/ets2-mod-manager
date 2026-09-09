using ETS2ModManager.Contracts;

namespace ETS2ModManager.Application;

public sealed class CrashApplicationService(ICrashDiagnosisService service)
{
    public CrashPair Discover() => service.DiscoverLatestCrashPair();
    public CrashAnalysis Analyze(string? crashPath, string? logPath, IReadOnlyList<ModRecord> mods) => service.Analyze(crashPath, logPath, mods);
    public PrecheckReport Precheck(ProfileRef profile, IReadOnlyList<string> activeMods, IReadOnlyList<ModRecord> mods, CancellationToken cancellationToken = default) => service.Precheck(profile, activeMods, mods, cancellationToken);
}

public sealed class SaveApplicationService(ISaveEditorService service, IGameState gameState)
{
    public IReadOnlyList<SaveSlotRef> ListSlots(ProfileRef profile) => service.ListSlots(profile);
    public SaveSnapshot ReadSnapshot(SaveSlotRef slot) => service.ReadSnapshot(slot);
    public SaveMutationResult SetMoney(SaveSlotRef slot, long amount)
    {
        RequireClosed(); return service.SetMoney(slot, amount);
    }
    public SaveMutationResult SetExperience(SaveSlotRef slot, uint experience)
    {
        RequireClosed(); return service.SetExperience(slot, experience);
    }
    public SaveMutationResult SetLevel(SaveSlotRef slot, int level)
    {
        RequireClosed(); return service.SetLevel(slot, level);
    }
    public SaveMutationResult RepairTruck(SaveSlotRef slot)
    {
        RequireClosed(); return service.RepairTruck(slot);
    }
    public SaveMutationResult RefuelTruck(SaveSlotRef slot, float amount)
    {
        RequireClosed(); return service.RefuelTruck(slot, amount);
    }
    public SaveMutationResult UnlockAllGarages(SaveSlotRef slot)
    {
        RequireClosed(); return service.UnlockAllGarages(slot);
    }
    public SaveMutationResult UnlockAllDealers(SaveSlotRef slot)
    {
        RequireClosed(); return service.UnlockAllDealers(slot);
    }
    public SaveSlotRef CopySlot(SaveSlotRef slot, string displayName)
    {
        RequireClosed(); return service.CopySlot(slot, displayName);
    }
    private void RequireClosed() { if (gameState.IsRunning()) throw new InvalidOperationException("Exit ETS2 or ATS before editing a save."); }
}

public sealed class LocalizationApplicationService(ILocalizationService service)
{
    public Task<LocalizationScanResult> ScanAsync(IEnumerable<string> packages, IProgress<ProgressEvent>? progress, CancellationToken cancellationToken = default) => service.ScanAsync(packages, progress, cancellationToken);
    public string TargetLocale => service.TargetLocale;
    public string? BaselinePackagePath => service.BaselinePackagePath;
    public bool SetTargetLocale(string locale) => service.SetTargetLocale(locale);
    public bool SetBaselinePackage(string path) => service.SetBaselinePackage(path);
    public void ClearBaselinePackage() => service.ClearBaselinePackage();
    public void SetUflPackages(IEnumerable<string> paths) => service.SetUflPackages(paths);
    public IReadOnlyDictionary<string, string> ReadDictionary() => service.ReadDictionary();
    public LocalizationDictionaryImportResult ImportDictionary(string path, bool merge = true) => service.ImportDictionary(path, merge);
    public void SetTranslation(string key, string value) => service.SetTranslation(key, value);
    public void ClearTranslation(string key) => service.ClearTranslation(key);
    public Task<LocalizationExportResult> ExportAsync(IEnumerable<LocalizationEntry> entries, string outputPath, string targetLocale, string modName, CancellationToken cancellationToken = default) => service.ExportAsync(entries, outputPath, targetLocale, modName, cancellationToken);
}

public sealed class LinkMigrationApplicationService(ILinkMigrationService service)
{
    public LinkMigrationResult Migrate(string source, string target) => service.Migrate(source, target);
    public LinkMigrationResult Restore(string source, string target) => service.Restore(source, target);
}

public sealed class UpdateApplicationService(IUpdateService service)
{
    public Task<UpdateInfo> CheckAsync(string currentVersion, string repository, CancellationToken cancellationToken = default) => service.CheckAsync(currentVersion, repository, cancellationToken);
    public Task<UpdateInstallResult> DownloadAndInstallAsync(UpdateInfo update, string installDirectory, string executableName, CancellationToken cancellationToken = default) => service.DownloadAndInstallAsync(update, installDirectory, executableName, cancellationToken);
}

public sealed class GameLaunchApplicationService(IGameLauncherService service)
{
    public Task<GameLaunchResult> LaunchAndWaitAsync(string executable, string documentsDirectory, CancellationToken cancellationToken = default) => service.LaunchAndWaitAsync(executable, documentsDirectory, cancellationToken);
}

public sealed class CategoryApplicationService(ICategoryService service)
{
    public CategoryState Snapshot() => service.Snapshot();
    public CategoryMutationResult CreateFolder(string name) => service.CreateFolder(name);
    public CategoryMutationResult RenameFolder(string oldName, string newName) => service.RenameFolder(oldName, newName);
    public CategoryMutationResult DeleteFolder(string name) => service.DeleteFolder(name);
    public CategoryMutationResult SetCategory(string modId, string category) => service.SetCategory(modId, category);
    public string GetCategory(string modId) => service.GetCategory(modId);
    public IReadOnlySet<string> ModsInCategory(string category) => service.ModsInCategory(category);
    public void Touch(IEnumerable<ModRecord> mods) => service.Touch(mods);
}

public sealed class CityLookupApplicationService(ICityLookupService service)
{
    public Task<IReadOnlyList<CitySearchResult>> RebuildAndSearchAsync(IEnumerable<ModRecord> mods, string keyword, CancellationToken cancellationToken = default) => service.RebuildAndSearchAsync(mods, keyword, cancellationToken);
}

public sealed class WorkshopMetadataApplicationService(IWorkshopMetadataService service)
{
    public Task<IReadOnlyList<WorkshopMetadata>> FetchAsync(IEnumerable<string> workshopIds, CancellationToken cancellationToken = default) => service.FetchAsync(workshopIds, cancellationToken);
}

public sealed class ExternalArchiveApplicationService(IExternalArchiveService service)
{
    public ExtractorAvailability Availability => service.Availability;
    public Task<ExtractionResult> ExtractManifestAsync(string packagePath, CancellationToken cancellationToken = default) => service.ExtractManifestAsync(packagePath, cancellationToken);
    public Task<ExtractionResult> ExtractFileAsync(string packagePath, string entryName, CancellationToken cancellationToken = default) => service.ExtractFileAsync(packagePath, entryName, cancellationToken);
    public Task<ExtractionResult> ExtractTreeAsync(string packagePath, string destinationDirectory, IReadOnlyList<string> roots, CancellationToken cancellationToken = default) => service.ExtractTreeAsync(packagePath, destinationDirectory, roots, cancellationToken);
}

public sealed class ModPresetApplicationService(IModPresetService service)
{
    public IReadOnlyList<ModPreset> List(string profileId) => service.List(profileId);
    public void Save(string profileId, string name, IReadOnlyList<string> activeMods) => service.Save(profileId, name, activeMods);
    public IReadOnlyList<string>? Load(string profileId, string name) => service.Load(profileId, name);
    public bool Delete(string profileId, string name) => service.Delete(profileId, name);
}
