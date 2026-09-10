using System.Collections.ObjectModel;
using System.IO;
using Microsoft.Win32;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using ETS2ModManager.Application;
using ETS2ModManager.Contracts;
using ETS2ModManager.Domain;
using ETS2ModManager.Infrastructure.Indexing;

namespace ETS2ModManager.WpfClient;

public sealed partial class ModRowViewModel : ObservableObject
{
    private readonly Action<string, string>? _categoryChanged;
    public ModRecord Record { get; }
    public ModRowViewModel(ModRecord record, bool enabled, string category = "", Action<string, string>? categoryChanged = null)
    {
        Record = record;
        _categoryChanged = categoryChanged;
        IsEnabled = enabled;
        Category = category;
    }
    public string DisplayName => Record.DisplayName;
    public string PackageName => Record.PackageName;
    public string PackageType => Record.PackageType;
    public string PackagePath => Record.PackagePath;

    [ObservableProperty]
    private bool isEnabled;

    [ObservableProperty]
    private string category = "";

    partial void OnCategoryChanged(string value) => _categoryChanged?.Invoke(Record.ModId, value);
}

public sealed partial class LocalizationRowViewModel : ObservableObject
{
    private readonly Action<LocalizationRowViewModel, string>? _valueChanged;
    private LocalizationEntry _entry;
    private string value;
    private string status;

    public LocalizationRowViewModel(LocalizationEntry entry, Action<LocalizationRowViewModel, string>? valueChanged = null)
    {
        _entry = entry;
        value = entry.Value;
        status = entry.Status;
        _valueChanged = valueChanged;
    }

    public string Key => _entry.Key;
    public string SourcePath => _entry.SourcePath;
    public string PackageName => _entry.PackageName;
    public string Category => _entry.Category;

    public string Value
    {
        get => value;
        set
        {
            var normalized = value ?? string.Empty;
            if (string.Equals(this.value, normalized, StringComparison.Ordinal)) return;
            this.value = normalized;
            OnPropertyChanged();
            _valueChanged?.Invoke(this, normalized);
        }
    }

    public string Status
    {
        get => status;
        private set => SetProperty(ref status, value);
    }

    public void Apply(LocalizationEntry entry)
    {
        _entry = entry;
        value = entry.Value;
        Status = entry.Status;
        OnPropertyChanged(nameof(Key));
        OnPropertyChanged(nameof(SourcePath));
        OnPropertyChanged(nameof(PackageName));
        OnPropertyChanged(nameof(Category));
        OnPropertyChanged(nameof(Value));
    }

    public LocalizationEntry ToEntry() => _entry with { Value = Value, Status = Status };
    public void MarkLocal(string translated) { value = translated; Status = "local"; OnPropertyChanged(nameof(Value)); }
}

public partial class MainWindowViewModel : ObservableObject
{
    public UiTextViewModel Ui { get; }
    private readonly IModScanner _scanner;
    private readonly SqliteModIndex _index;
    private readonly IProfileRepository _profiles;
    private readonly ProfileApplicationService _profileService;
    private readonly SaveApplicationService _saveService;
    private readonly CrashApplicationService _crashService;
    private readonly LocalizationApplicationService _localizationService;
    private readonly LinkMigrationApplicationService _linkService;
    private readonly UpdateApplicationService _updateService;
    private readonly GameLaunchApplicationService _gameService;
    private readonly CategoryApplicationService _categoryService;
    private readonly CityLookupApplicationService _cityService;
    private readonly WorkshopMetadataApplicationService _workshopService;
    private readonly ExternalArchiveApplicationService _archiveService;
    private readonly ModPresetApplicationService _presetService;
    private readonly string _documentsDirectory;
    private readonly string _modDirectory;
    private CancellationTokenSource? _scanCts;
    private CancellationTokenSource? _localizationCts;
    private UpdateInfo? _availableUpdate;
    private const string CurrentVersion = "1.2.3";

    public ObservableCollection<ModRowViewModel> Mods { get; } = [];
    public ObservableCollection<ProfileRef> Profiles { get; }
    public ObservableCollection<SaveSlotRef> SaveSlots { get; } = [];
    public ObservableCollection<SaveFieldValue> SaveFields { get; } = [];
    public ObservableCollection<CrashSuspect> CrashSuspects { get; } = [];
    public ObservableCollection<PrecheckIssue> PrecheckIssues { get; } = [];
    public ObservableCollection<LocalizationRowViewModel> LocalizationEntries { get; } = [];
    public ObservableCollection<string> CategoryFolders { get; } = [];
    public ObservableCollection<CitySearchResult> CityResults { get; } = [];
    public ObservableCollection<WorkshopMetadata> WorkshopMetadata { get; } = [];
    public ObservableCollection<string> PresetNames { get; } = [];

    public string SelectedModName => SelectedMod?.DisplayName ?? Ui.NoModSelected;
    public string SelectedModPackage => SelectedMod?.PackageName ?? Ui.SelectModHint;
    public string SelectedModType => SelectedMod?.PackageType ?? string.Empty;
    public string SelectedModPath => SelectedMod?.PackagePath ?? string.Empty;

    [ObservableProperty]
    private ProfileRef? selectedProfile;

    [ObservableProperty]
    private string status = "";

    [ObservableProperty]
    private bool isScanning;

    [ObservableProperty] private SaveSlotRef? selectedSaveSlot;
    [ObservableProperty] private string saveAmount = "1000000";
    [ObservableProperty] private string saveExperience = "10000";
    [ObservableProperty] private string saveLevel = "5";
    [ObservableProperty] private string fuelAmount = "100";
    [ObservableProperty] private string migrationTarget = "";
    [ObservableProperty] private string updateRepository = "HimenoKoutarou/ets2-mod-manager";
    [ObservableProperty] private string updateStatus = "";
    [ObservableProperty] private string localizationStatus = "";
    [ObservableProperty] private string localizationLocale = "zh_cn";
    [ObservableProperty] private string localizationBaseline = "";
    [ObservableProperty] private string crashStatus = "";
    [ObservableProperty] private string cityKeyword = "";
    [ObservableProperty] private string cityStatus = "";
    [ObservableProperty] private string categoryName = "";
    [ObservableProperty] private string categoryStatus = "";
    [ObservableProperty] private string workshopStatus = "";
    [ObservableProperty] private string extractorStatus = "";
    [ObservableProperty] private string extractorPackagePath = "";
    [ObservableProperty] private string extractorEntryName = "manifest.sii";
    [ObservableProperty] private string extractorOutput = "";
    [ObservableProperty] private string presetName = "";
    [ObservableProperty] private string? selectedPresetName;
    [ObservableProperty] private ModRowViewModel? selectedMod;
    [ObservableProperty] private string profileDisplayName = "";
    [ObservableProperty] private string profileCompanyName = "";

    public MainWindowViewModel(IModScanner scanner, SqliteModIndex index, IProfileRepository profiles,
        ProfileApplicationService profileService, SaveApplicationService saveService,
        CrashApplicationService crashService, LocalizationApplicationService localizationService,
        LinkMigrationApplicationService linkService, UpdateApplicationService updateService,
        GameLaunchApplicationService gameService, CategoryApplicationService categoryService,
        CityLookupApplicationService cityService, WorkshopMetadataApplicationService workshopService,
        ExternalArchiveApplicationService archiveService, ModPresetApplicationService presetService,
        string documentsDirectory, string modDirectory)
    {
        Ui = new UiTextViewModel();
        Ui.PropertyChanged += (_, _) =>
        {
            OnPropertyChanged(nameof(SelectedModName));
            OnPropertyChanged(nameof(SelectedModPackage));
            OnPropertyChanged(nameof(SelectedModType));
            OnPropertyChanged(nameof(SelectedModPath));
            if (string.IsNullOrWhiteSpace(Status)) Status = Ui.Translate("wpf.status_ready", "Ready");
            if (string.IsNullOrWhiteSpace(LocalizationBaseline)) LocalizationBaseline = Ui.Translate("wpf.no_baseline", "No baseline package selected");
        };
        _scanner = scanner;
        _index = index;
        _profiles = profiles;
        _profileService = profileService;
        _saveService = saveService;
        _crashService = crashService;
        _localizationService = localizationService;
        _linkService = linkService;
        _updateService = updateService;
        _gameService = gameService;
        _categoryService = categoryService;
        _cityService = cityService;
        _workshopService = workshopService;
        _archiveService = archiveService;
        _presetService = presetService;
        _documentsDirectory = documentsDirectory;
        _modDirectory = modDirectory;
        Status = Ui.Translate("wpf.status_ready", "Ready");
        LocalizationBaseline = Ui.Translate("wpf.no_baseline", "No baseline package selected");
        LocalizationLocale = _localizationService.TargetLocale;
        Profiles = new ObservableCollection<ProfileRef>(profiles.ListProfiles());
        RefreshCategories();
        ExtractorStatus = $"External extractor: legacy={_archiveService.Availability.Legacy}, modern={_archiveService.Availability.Modern}";
        SelectedProfile = Profiles.FirstOrDefault();
        ReloadRows();
    }

    partial void OnSelectedProfileChanged(ProfileRef? value)
    {
        ProfileDisplayName = value?.DisplayName ?? "";
        ProfileCompanyName = value?.CompanyName ?? "";
        RefreshPresets();
        ReloadRows();
        RefreshSaveSlots();
    }

    partial void OnSelectedModChanged(ModRowViewModel? value)
    {
        OnPropertyChanged(nameof(SelectedModName));
        OnPropertyChanged(nameof(SelectedModPackage));
        OnPropertyChanged(nameof(SelectedModType));
        OnPropertyChanged(nameof(SelectedModPath));
        MoveModUpCommand.NotifyCanExecuteChanged();
        MoveModDownCommand.NotifyCanExecuteChanged();
        MoveModTopCommand.NotifyCanExecuteChanged();
        MoveModBottomCommand.NotifyCanExecuteChanged();
    }

    partial void OnSelectedSaveSlotChanged(SaveSlotRef? value) => ReadSaveSnapshot();

    partial void OnLocalizationLocaleChanged(string value)
    {
        if (string.IsNullOrWhiteSpace(value)) return;
        if (!_localizationService.SetTargetLocale(value))
        {
            LocalizationStatus = $"Unsupported locale: {value}";
            return;
        }
        ConfigureUflPackages();
        LocalizationEntries.Clear();
        LocalizationStatus = $"Target locale changed to {_localizationService.TargetLocale}; scan again.";
    }

    [RelayCommand(CanExecute = nameof(CanStartScan))]
    private async Task StartScanAsync()
    {
        IsScanning = true;
        _scanCts?.Dispose();
        _scanCts = new CancellationTokenSource();
        StartScanCommand.NotifyCanExecuteChanged();
        CancelScanCommand.NotifyCanExecuteChanged();
        try
        {
            Status = "Scanning mods...";
            var progress = new Progress<ProgressEvent>(eventInfo => Status = eventInfo.Message);
            var result = await _scanner.ScanAsync(progress, _scanCts.Token);
            _index.SyncSnapshot(result);
            _categoryService.Touch(result.Mods);
            ReloadRows();
            Status = $"Scan completed: {result.ScannedCount} mods ({result.ElapsedMilliseconds} ms)";
        }
        catch (OperationCanceledException) { Status = "Scan cancelled"; }
        catch (Exception error) { Status = $"Scan failed: {error.Message}"; }
        finally
        {
            _scanCts?.Dispose();
            _scanCts = null;
            IsScanning = false;
            StartScanCommand.NotifyCanExecuteChanged();
            CancelScanCommand.NotifyCanExecuteChanged();
            SaveProfileCommand.NotifyCanExecuteChanged();
        }
    }

    [RelayCommand(CanExecute = nameof(CanCancelScan))]
    private void CancelScan() => _scanCts?.Cancel();

    private bool CanCancelScan() => IsScanning;

    [RelayCommand(CanExecute = nameof(CanSaveProfile))]
    private void SaveProfile()
    {
        if (SelectedProfile is null) return;
        try
        {
            var entries = Mods.Select((row, index) => new WorklistEntry(row.PackageName, row.IsEnabled, index, row.IsEnabled ? index : null));
            var active = PriorityRules.WorklistToProfileActive(entries);
            _profileService.ReplaceActiveMods(SelectedProfile, active, verify: true);
            Status = $"Saved {active.Count} active mods to {SelectedProfile.DisplayName}";
        }
        catch (Exception error) { Status = $"Save failed: {error.Message}"; }
    }

    private bool CanSaveProfile() => SelectedProfile is { IsWritable: true } && !IsScanning;
    private bool CanStartScan() => !IsScanning;

    [RelayCommand]
    private void EnableAllMods() => ApplyWorklist(PriorityRules.BatchToggle(CurrentWorklist(), Enumerable.Range(0, Mods.Count), "enable"));

    [RelayCommand]
    private void DisableAllMods() => ApplyWorklist(PriorityRules.BatchToggle(CurrentWorklist(), Enumerable.Range(0, Mods.Count), "disable"));

    [RelayCommand]
    private void ToggleAllMods() => ApplyWorklist(PriorityRules.BatchToggle(CurrentWorklist(), Enumerable.Range(0, Mods.Count), "toggle"));

    [RelayCommand(CanExecute = nameof(CanMoveSelectedMod))]
    private void MoveModUp() => MoveSelected(PriorityRules.MoveUp(CurrentWorklist(), [Mods.IndexOf(SelectedMod!)], 1));

    [RelayCommand(CanExecute = nameof(CanMoveSelectedMod))]
    private void MoveModDown() => MoveSelected(PriorityRules.MoveDown(CurrentWorklist(), [Mods.IndexOf(SelectedMod!)], 1));

    [RelayCommand(CanExecute = nameof(CanMoveSelectedMod))]
    private void MoveModTop() => MoveSelected(PriorityRules.MoveTop(CurrentWorklist(), [Mods.IndexOf(SelectedMod!)]));

    [RelayCommand(CanExecute = nameof(CanMoveSelectedMod))]
    private void MoveModBottom() => MoveSelected(PriorityRules.MoveBottom(CurrentWorklist(), [Mods.IndexOf(SelectedMod!)]));

    private bool CanMoveSelectedMod() => SelectedMod is not null && SelectedMod.IsEnabled;

    public void MoveModByIndex(int fromIndex, int toIndex)
    {
        if (fromIndex < 0 || fromIndex >= Mods.Count || toIndex < 0 || toIndex >= Mods.Count) return;
        if (!Mods[fromIndex].IsEnabled || !Mods[toIndex].IsEnabled) return;
        var rows = CurrentWorklist().ToList();
        var moved = rows[fromIndex];
        rows.RemoveAt(fromIndex);
        rows.Insert(toIndex, moved);
        ApplyWorklist(rows);
        SelectedMod = Mods[toIndex];
    }

    private IReadOnlyList<WorklistEntry> CurrentWorklist() =>
        Mods.Select((row, index) => new WorklistEntry(row.PackageName, row.IsEnabled, index, row.IsEnabled ? index : null)).ToArray();

    private void MoveSelected(IReadOnlyList<WorklistEntry> worklist)
    {
        var selectedKey = SelectedMod?.Record.ModId;
        ApplyWorklist(worklist);
        SelectedMod = Mods.FirstOrDefault(row => row.Record.ModId == selectedKey);
    }

    private void ApplyWorklist(IEnumerable<WorklistEntry> worklist)
    {
        var byKey = Mods.ToDictionary(row => ModIdentity.CanonicalKey(row.PackageName), StringComparer.Ordinal);
        var next = worklist.Select(entry => byKey.GetValueOrDefault(ModIdentity.CanonicalKey(entry.PackageName))).Where(row => row is not null).Cast<ModRowViewModel>().ToArray();
        Mods.Clear();
        foreach (var row in next)
        {
            row.IsEnabled = worklist.First(entry => ModIdentity.CanonicalKey(entry.PackageName) == ModIdentity.CanonicalKey(row.PackageName)).Enabled;
            Mods.Add(row);
        }
        SaveProfileCommand.NotifyCanExecuteChanged();
        MoveModUpCommand.NotifyCanExecuteChanged();
        MoveModDownCommand.NotifyCanExecuteChanged();
        MoveModTopCommand.NotifyCanExecuteChanged();
        MoveModBottomCommand.NotifyCanExecuteChanged();
    }

    [RelayCommand]
    private void RefreshPresets()
    {
        PresetNames.Clear();
        if (SelectedProfile is null) return;
        foreach (var preset in _presetService.List(SelectedProfile.ProfileId)) PresetNames.Add(preset.Name);
        SelectedPresetName = PresetNames.FirstOrDefault();
    }

    [RelayCommand]
    private void SavePreset()
    {
        if (SelectedProfile is null || string.IsNullOrWhiteSpace(PresetName)) return;
        _presetService.Save(SelectedProfile.ProfileId, PresetName, PriorityRules.WorklistToProfileActive(CurrentWorklist()));
        PresetName = "";
        RefreshPresets();
        Status = "Preset saved.";
    }

    [RelayCommand]
    private void LoadPreset()
    {
        if (SelectedProfile is null || string.IsNullOrWhiteSpace(SelectedPresetName)) return;
        var active = _presetService.Load(SelectedProfile.ProfileId, SelectedPresetName);
        if (active is null) return;
        ApplyWorklist(PriorityRules.BuildWorklist(active, Mods.Select(row => row.PackageName)));
        Status = $"Preset loaded: {SelectedPresetName}";
    }

    [RelayCommand]
    private void DeletePreset()
    {
        if (SelectedProfile is null || string.IsNullOrWhiteSpace(SelectedPresetName)) return;
        if (_presetService.Delete(SelectedProfile.ProfileId, SelectedPresetName))
        {
            RefreshPresets();
            Status = "Preset deleted.";
        }
    }

    [RelayCommand]
    private void RefreshProfiles()
    {
        var selectedId = SelectedProfile?.ProfileId;
        Profiles.Clear();
        foreach (var profile in _profiles.ListProfiles()) Profiles.Add(profile);
        SelectedProfile = Profiles.FirstOrDefault(profile => profile.ProfileId == selectedId) ?? Profiles.FirstOrDefault();
    }

    [RelayCommand]
    private void RenameProfile()
    {
        if (SelectedProfile is null) return;
        try
        {
            var renamed = _profileService.Rename(SelectedProfile, ProfileDisplayName, ProfileCompanyName);
            var index = Profiles.IndexOf(SelectedProfile);
            if (index >= 0) Profiles[index] = renamed;
            SelectedProfile = renamed;
            Status = "Profile renamed.";
        }
        catch (Exception error) { Status = $"Profile rename failed: {error.Message}"; }
    }

    [RelayCommand]
    private void CopyProfile()
    {
        if (SelectedProfile is null) return;
        try
        {
            var copy = _profileService.Copy(SelectedProfile, ProfileDisplayName, ProfileCompanyName);
            Profiles.Add(copy);
            SelectedProfile = copy;
            Status = "Profile copied.";
        }
        catch (Exception error) { Status = $"Profile copy failed: {error.Message}"; }
    }

    [RelayCommand]
    private void DeleteProfile()
    {
        if (SelectedProfile is null) return;
        try
        {
            var deleted = SelectedProfile;
            _profileService.Delete(deleted);
            Profiles.Remove(deleted);
            SelectedProfile = Profiles.FirstOrDefault();
            Status = "Profile deleted.";
        }
        catch (Exception error) { Status = $"Profile delete failed: {error.Message}"; }
    }

    [RelayCommand]
    private void RefreshSaveSlots()
    {
        SaveSlots.Clear();
        if (SelectedProfile is null) return;
        foreach (var slot in _saveService.ListSlots(SelectedProfile)) SaveSlots.Add(slot);
        SelectedSaveSlot = SaveSlots.FirstOrDefault();
        Status = $"Loaded {SaveSlots.Count} save slots.";
    }

    [RelayCommand]
    private void ReadSaveSnapshot()
    {
        SaveFields.Clear();
        if (SelectedSaveSlot is null) return;
        try { foreach (var field in _saveService.ReadSnapshot(SelectedSaveSlot).Fields) SaveFields.Add(field); Status = "Save snapshot loaded."; }
        catch (Exception error) { Status = $"Save read failed: {error.Message}"; }
    }

    [RelayCommand]
    private void SetMoney()
    {
        if (SelectedSaveSlot is null || !long.TryParse(SaveAmount, out var amount)) { Status = "Enter a valid money amount."; return; }
        ApplySaveResult(_saveService.SetMoney(SelectedSaveSlot, amount));
    }

    [RelayCommand]
    private void SetExperience()
    {
        if (SelectedSaveSlot is null || !uint.TryParse(SaveExperience, out var value)) { Status = "Enter a valid experience value."; return; }
        ApplySaveResult(_saveService.SetExperience(SelectedSaveSlot, value));
    }

    [RelayCommand]
    private void SetLevel()
    {
        if (SelectedSaveSlot is null || !int.TryParse(SaveLevel, out var value)) { Status = "Enter a valid level."; return; }
        ApplySaveResult(_saveService.SetLevel(SelectedSaveSlot, value));
    }

    [RelayCommand]
    private void RepairTruck() { if (SelectedSaveSlot is not null) ApplySaveResult(_saveService.RepairTruck(SelectedSaveSlot)); }

    [RelayCommand]
    private void RefuelTruck()
    {
        if (SelectedSaveSlot is null || !float.TryParse(FuelAmount, out var value)) { Status = "Enter a valid fuel amount."; return; }
        ApplySaveResult(_saveService.RefuelTruck(SelectedSaveSlot, value));
    }

    [RelayCommand]
    private void UnlockAllGarages()
    {
        if (SelectedSaveSlot is not null) ApplySaveResult(_saveService.UnlockAllGarages(SelectedSaveSlot));
    }

    [RelayCommand]
    private void UnlockAllDealers()
    {
        if (SelectedSaveSlot is not null) ApplySaveResult(_saveService.UnlockAllDealers(SelectedSaveSlot));
    }

    [RelayCommand]
    private void CopySaveSlot()
    {
        if (SelectedSaveSlot is null) return;
        try { var copy = _saveService.CopySlot(SelectedSaveSlot, $"Copy {DateTime.Now:yyyy-MM-dd HHmm}"); SaveSlots.Add(copy); SelectedSaveSlot = copy; Status = "Save slot copied."; }
        catch (Exception error) { Status = $"Save copy failed: {error.Message}"; }
    }

    [RelayCommand]
    private void AnalyzeCrash()
    {
        try
        {
            var pair = _crashService.Discover();
            var result = _crashService.Analyze(pair.CrashPath, pair.LogPath, Mods.Select(row => row.Record).ToArray());
            CrashSuspects.Clear(); foreach (var suspect in result.Suspects) CrashSuspects.Add(suspect);
            CrashStatus = $"{result.FaultModuleCategory}; suspects {result.Suspects.Count}; unmatched {result.FailedToMatch}";
        }
        catch (Exception error) { CrashStatus = $"Crash analysis failed: {error.Message}"; }
    }

    [RelayCommand]
    private void PrecheckProfile()
    {
        PrecheckIssues.Clear();
        if (SelectedProfile is null) { CrashStatus = "Select a Profile first."; return; }
        try
        {
            var active = _profileService.ReadActiveMods(SelectedProfile);
            var report = _crashService.Precheck(SelectedProfile, active, Mods.Select(row => row.Record).ToArray());
            foreach (var issue in report.Issues) PrecheckIssues.Add(issue);
            CrashStatus = $"Precheck: {report.TotalIssues} issues ({report.RedCount} red, {report.YellowCount} yellow).";
        }
        catch (OperationCanceledException) { CrashStatus = "Profile precheck cancelled."; }
        catch (Exception error) { CrashStatus = $"Profile precheck failed: {error.Message}"; }
    }

    [RelayCommand]
    private async Task ScanLocalizationAsync()
    {
        _localizationCts?.Dispose();
        _localizationCts = new CancellationTokenSource();
        try
        {
            if (!_localizationService.SetTargetLocale(LocalizationLocale))
            {
                LocalizationStatus = $"Unsupported locale: {LocalizationLocale}";
                return;
            }
            ConfigureUflPackages();
            var packages = Mods.Where(x => x.IsEnabled).Select(x => x.PackagePath).ToArray();
            var result = await _localizationService.ScanAsync(packages, new Progress<ProgressEvent>(p => LocalizationStatus = p.Message), _localizationCts.Token);
            LocalizationEntries.Clear();
            foreach (var entry in result.Entries) LocalizationEntries.Add(new LocalizationRowViewModel(entry, OnLocalizationValueChanged));
            LocalizationStatus = $"Localization entries: {result.Entries.Count} from {result.ScannedPackages} packages.";
        }
        catch (OperationCanceledException) { LocalizationStatus = "Localization scan cancelled."; }
        catch (Exception error) { LocalizationStatus = $"Localization scan failed: {error.Message}"; }
        finally
        {
            _localizationCts?.Dispose();
            _localizationCts = null;
        }
    }

    [RelayCommand]
    private void CancelLocalization() => _localizationCts?.Cancel();

    [RelayCommand]
    private void SelectLocalizationBaseline()
    {
        var dialog = new OpenFileDialog
        {
            Filter = "Localization Mod (*.scs;*.zip)|*.scs;*.zip|All files (*.*)|*.*",
            InitialDirectory = Directory.Exists(_modDirectory) ? _modDirectory : null,
            Title = "Select baseline localization Mod"
        };
        if (dialog.ShowDialog() != true) return;
        if (!_localizationService.SetBaselinePackage(dialog.FileName))
        {
            LocalizationStatus = "The selected package has no localization data for the target locale.";
            return;
        }
        LocalizationBaseline = Path.GetFileName(dialog.FileName);
        LocalizationEntries.Clear();
        LocalizationStatus = "Baseline selected; scan again.";
    }

    [RelayCommand]
    private void ClearLocalizationBaseline()
    {
        _localizationService.ClearBaselinePackage();
        LocalizationBaseline = "No baseline package selected";
        LocalizationEntries.Clear();
        LocalizationStatus = "Baseline cleared; scan again.";
    }

    private void ConfigureUflPackages()
    {
        static bool LooksLikeLocalizationPackage(ModRowViewModel row)
        {
            var text = $"{row.Record.ModId} {row.DisplayName} {row.PackageName}".ToLowerInvariant();
            return text.Contains("ufl") || text.Contains("localization") || text.Contains("translation")
                || text.Contains("chinese") || text.Contains("language") || text.Contains("汉化") || text.Contains("中文");
        }
        _localizationService.SetUflPackages(Mods.Where(row => row.IsEnabled && LooksLikeLocalizationPackage(row)).Select(row => row.PackagePath));
    }

    private void OnLocalizationValueChanged(LocalizationRowViewModel row, string value)
    {
        try
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                _localizationService.ClearTranslation(row.Key);
                row.Apply(row.ToEntry() with { Value = "", Status = "missing_value" });
                LocalizationStatus = $"Cleared translation: {row.Key}";
                return;
            }
            _localizationService.SetTranslation(row.Key, value);
            row.MarkLocal(value);
            LocalizationStatus = $"Saved translation: {row.Key}";
        }
        catch (Exception error)
        {
            LocalizationStatus = $"Translation was not saved: {error.Message}";
        }
    }

    [RelayCommand]
    private void ImportLocalizationDictionary()
    {
        var dialog = new OpenFileDialog
        {
            Filter = "Dictionary (*.json;*.csv;*.txt)|*.json;*.csv;*.txt|All files (*.*)|*.*",
            Title = "Import localization dictionary"
        };
        if (dialog.ShowDialog() != true) return;
        try
        {
            var result = _localizationService.ImportDictionary(dialog.FileName, merge: true);
            LocalizationStatus = $"Imported {result.Imported} translations; skipped {result.Skipped}.";
            if (LocalizationEntries.Count > 0) RescanLocalizationRows();
        }
        catch (Exception error) { LocalizationStatus = $"Dictionary import failed: {error.Message}"; }
    }

    [RelayCommand]
    private async Task ExportLocalizationAsync()
    {
        if (LocalizationEntries.Count == 0) { LocalizationStatus = "Scan localization before exporting."; return; }
        var dialog = new SaveFileDialog
        {
            Filter = "SCS Mod (*.scs)|*.scs|ZIP archive (*.zip)|*.zip",
            FileName = "ETS2ModManager.localization.scs",
            Title = "Export localization mod"
        };
        if (dialog.ShowDialog() != true) return;
        try
        {
            var entries = LocalizationEntries.Select(row => row.ToEntry()).ToArray();
            var result = await _localizationService.ExportAsync(entries, dialog.FileName, LocalizationLocale, "Generated L10n by ETS2ModManager", CancellationToken.None);
            LocalizationStatus = result.Message;
        }
        catch (OperationCanceledException) { LocalizationStatus = "Localization export cancelled."; }
        catch (Exception error) { LocalizationStatus = $"Localization export failed: {error.Message}"; }
    }

    private void RescanLocalizationRows()
    {
        // Re-resolve only the current rows so dictionary imports are visible
        // immediately without starting another archive traversal.
        var dictionary = _localizationService.ReadDictionary();
        foreach (var row in LocalizationEntries)
        {
            if (dictionary.TryGetValue(row.Key, out var value)) row.Apply(row.ToEntry() with { Value = value, Status = "local" });
        }
    }

    [RelayCommand]
    private void MigrateMods()
    {
        if (string.IsNullOrWhiteSpace(MigrationTarget)) { Status = "Enter a target directory."; return; }
        var result = _linkService.Migrate(_modDirectory, MigrationTarget); Status = result.Message;
    }

    [RelayCommand]
    private void RestoreMods()
    {
        if (string.IsNullOrWhiteSpace(MigrationTarget)) { Status = "Enter the migrated directory."; return; }
        var result = _linkService.Restore(_modDirectory, MigrationTarget); Status = result.Message;
    }

    [RelayCommand]
    private async Task CheckUpdateAsync()
    {
        try
        {
            var result = await _updateService.CheckAsync(CurrentVersion, UpdateRepository, CancellationToken.None);
            _availableUpdate = result;
            InstallUpdateCommand.NotifyCanExecuteChanged();
            UpdateStatus = result.Message + (result.LatestVersion is null ? "" : $" Latest: {result.LatestVersion}");
        }
        catch (OperationCanceledException) { UpdateStatus = "Update check cancelled."; }
        catch (Exception error) { UpdateStatus = $"Update check failed: {error.Message}"; }
    }

    [RelayCommand(CanExecute = nameof(CanInstallUpdate))]
    private async Task InstallUpdateAsync()
    {
        if (_availableUpdate is not { IsAvailable: true } update) return;
        try
        {
            var result = await _updateService.DownloadAndInstallAsync(
                update, AppContext.BaseDirectory, "ETS2ModManager.WpfClient.exe", CancellationToken.None);
            UpdateStatus = result.Message;
            if (result.Success) InstallUpdateCommand.NotifyCanExecuteChanged();
        }
        catch (OperationCanceledException) { UpdateStatus = "Update installation cancelled."; }
        catch (Exception error) { UpdateStatus = $"Update installation failed: {error.Message}"; }
    }

    private bool CanInstallUpdate() => _availableUpdate is { IsAvailable: true };

    [RelayCommand]
    private async Task ExtractEntryAsync()
    {
        var package = ExtractorPackagePath.Trim();
        var entry = ExtractorEntryName.Trim();
        if (package.Length == 0 || entry.Length == 0)
        {
            ExtractorOutput = "Enter a package path and entry name.";
            return;
        }
        try
        {
            var result = await _archiveService.ExtractFileAsync(package, entry, CancellationToken.None);
            ExtractorOutput = result.Success
                ? result.Text ?? $"Extracted {result.Bytes?.Length ?? 0} bytes."
                : result.Message;
        }
        catch (OperationCanceledException) { ExtractorOutput = "Extraction cancelled."; }
        catch (Exception error) { ExtractorOutput = $"Extraction failed: {error.Message}"; }
    }

    [RelayCommand]
    private async Task LaunchGameAsync()
    {
        try
        {
            var executable = Infrastructure.Windows.GameExecutableLocator.Find();
            if (executable is null) { Status = "Game executable was not found."; return; }
            var result = await _gameService.LaunchAndWaitAsync(executable, _documentsDirectory, CancellationToken.None);
            Status = result.Message;
            if (result.Crashed) AnalyzeCrash();
        }
        catch (OperationCanceledException) { Status = "Game launch cancelled."; }
        catch (Exception error) { Status = $"Game launch failed: {error.Message}"; }
    }

    private void ApplySaveResult(SaveMutationResult result) { Status = result.Message; if (result.Success) ReadSaveSnapshot(); }

    [RelayCommand]
    private void RefreshCategories()
    {
        CategoryFolders.Clear();
        foreach (var folder in _categoryService.Snapshot().Folders) CategoryFolders.Add(folder);
        CategoryStatus = $"Categories: {CategoryFolders.Count}";
    }

    [RelayCommand]
    private void CreateCategory()
    {
        var result = _categoryService.CreateFolder(CategoryName);
        CategoryStatus = result.Success ? "Category created." : $"Category not created: {result.Error}";
        if (result.Success) { CategoryName = ""; RefreshCategories(); }
    }

    [RelayCommand]
    private void AssignAllUncategorized()
    {
        var category = CategoryName.Trim();
        if (category.Length == 0) { CategoryStatus = "Enter a category name."; return; }
        if (!CategoryFolders.Contains(category, StringComparer.Ordinal)) _categoryService.CreateFolder(category);
        foreach (var row in Mods.Where(row => string.IsNullOrWhiteSpace(row.Category))) row.Category = category;
        RefreshCategories();
        CategoryStatus = $"Assigned uncategorized mods to {category}.";
    }

    [RelayCommand]
    private async Task SearchCitiesAsync()
    {
        try
        {
            var result = await _cityService.RebuildAndSearchAsync(_index.Query(), CityKeyword, CancellationToken.None);
            CityResults.Clear(); foreach (var city in result) CityResults.Add(city);
            CityStatus = $"Cities found: {CityResults.Count}";
        }
        catch (Exception error) { CityStatus = $"City search failed: {error.Message}"; }
    }

    [RelayCommand]
    private async Task RefreshWorkshopMetadataAsync()
    {
        try
        {
            var ids = _index.Query().Where(x => x.PackageType == "workshop").Select(x => ModIdentity.LegacyWorkshopId(x.ModId)).Where(x => x.Length > 0);
            var result = await _workshopService.FetchAsync(ids, CancellationToken.None);
            WorkshopMetadata.Clear(); foreach (var item in result) WorkshopMetadata.Add(item);
            WorkshopStatus = $"Workshop metadata loaded: {WorkshopMetadata.Count}";
        }
        catch (OperationCanceledException) { WorkshopStatus = "Workshop lookup cancelled."; }
        catch (Exception error) { WorkshopStatus = $"Workshop lookup failed: {error.Message}"; }
    }

    private void ReloadRows()
    {
        var all = _index.Query();
        var active = SelectedProfile is null ? [] : _profiles.ReadActiveMods(SelectedProfile);
        var worklist = PriorityRules.BuildWorklist(active, all.Select(x => x.PackageName));
        var byKey = all.GroupBy(x => ModIdentity.CanonicalKey(x.PackageName)).ToDictionary(g => g.Key, g => g.First(), StringComparer.Ordinal);
        Mods.Clear();
        foreach (var entry in worklist)
        {
            var key = ModIdentity.CanonicalKey(entry.PackageName);
            var record = byKey.GetValueOrDefault(key) ?? new ModRecord(key, entry.PackageName, "", "unknown", entry.PackageName, 0, 0);
            Mods.Add(new ModRowViewModel(record, entry.Enabled, _categoryService.GetCategory(record.ModId), (id, category) => _categoryService.SetCategory(id, category)));
        }
        SaveProfileCommand.NotifyCanExecuteChanged();
        MoveModUpCommand.NotifyCanExecuteChanged();
        MoveModDownCommand.NotifyCanExecuteChanged();
        MoveModTopCommand.NotifyCanExecuteChanged();
        MoveModBottomCommand.NotifyCanExecuteChanged();
    }
}
