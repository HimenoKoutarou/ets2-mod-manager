using System.Text.Json;
using System.IO;
using CommunityToolkit.Mvvm.ComponentModel;
using ETS2ModManager.Infrastructure.Localization;

namespace ETS2ModManager.WpfClient;

public sealed record LanguageOption(string Code, string DisplayName);

public sealed partial class UiTextViewModel : ObservableObject
{
    private readonly JsonTranslationService _translations;
    private static readonly string[] SupportedLanguages = ["zh_CN", "en_US", "ru_RU"];
    private static readonly string[] RefreshProperties =
    [
        nameof(AppTitle), nameof(ScanMods), nameof(EnableAll), nameof(DisableAll), nameof(Invert),
        nameof(Top), nameof(Up), nameof(Down), nameof(Bottom), nameof(SaveProfile), nameof(LaunchGame),
        nameof(CrashCheck), nameof(StatusCore), nameof(Profiles), nameof(RefreshProfiles), nameof(Rename),
        nameof(Copy), nameof(Delete), nameof(Categories), nameof(ModInventory), nameof(PresetName),
        nameof(SavePreset), nameof(Mods), nameof(Saves), nameof(Refresh), nameof(CopySlot), nameof(Money),
        nameof(Xp), nameof(Level), nameof(Set), nameof(Repair), nameof(Refuel), nameof(UnlockGarages),
        nameof(Localization), nameof(ScanLocalization), nameof(Cancel), nameof(ImportDictionary), nameof(ExportScs),
        nameof(Diagnostics), nameof(AnalyzeLatestCrash), nameof(PrecheckProfile), nameof(Cities), nameof(SearchCities),
        nameof(Workshop), nameof(RefreshWorkshop), nameof(Tools), nameof(ModDirectoryMigration), nameof(MigrateAndLink),
        nameof(Restore), nameof(Updates), nameof(CheckUpdates), nameof(InstallUpdate), nameof(ExternalExtractor),
        nameof(ExtractEntry), nameof(ModPreview), nameof(NoModSelected), nameof(SelectModHint), nameof(Source),
        nameof(Package), nameof(Category), nameof(Enabled), nameof(Name), nameof(Translation), nameof(Status),
        nameof(CompanyNameHint), nameof(ProfileNameHint), nameof(TargetDirectoryHint), nameof(RepositoryHint),
        nameof(PackagePathHint), nameof(TargetLocaleHint), nameof(EntryNameHint), nameof(LanguageLabel),
        nameof(CoreFooter), nameof(CancelScan), nameof(LoadPreset), nameof(DeletePreset),
        nameof(UnlockDealers), nameof(SelectBaseline), nameof(ClearBaseline), nameof(CategoryManagement),
        nameof(CategoryNameHint), nameof(CreateCategory), nameof(AssignUncategorized), nameof(RefreshCategories), nameof(Key)
    ];

    [ObservableProperty]
    private string language;

    public IReadOnlyList<LanguageOption> Languages { get; } =
    [
        new("zh_CN", "简体中文"),
        new("en_US", "English"),
        new("ru_RU", "Русский")
    ];

    public UiTextViewModel(string? resourceRoot = null)
    {
        _translations = new JsonTranslationService(resourceRoot ?? Path.Combine(AppContext.BaseDirectory, "assets", "i18n", "wpf"));
        language = LoadLanguage();
    }

    partial void OnLanguageChanged(string value)
    {
        if (!SupportedLanguages.Contains(value, StringComparer.OrdinalIgnoreCase))
        {
            Language = "zh_CN";
            return;
        }
        SaveLanguage(value);
        foreach (var property in RefreshProperties) OnPropertyChanged(property);
    }

    private string T(string key, string fallback) => _translations.Translate(Language, key, fallback);

    public string Translate(string key, string fallback) => T(key, fallback);

    public string AppTitle => T("wpf.app_title", "ETS2 Mod Manager");
    public string ScanMods => T("wpf.scan_mods", "Scan Mods");
    public string EnableAll => T("wpf.enable_all", "Enable All");
    public string DisableAll => T("wpf.disable_all", "Disable All");
    public string Invert => T("wpf.invert", "Invert");
    public string Top => T("wpf.top", "Top");
    public string Up => T("wpf.up", "Up");
    public string Down => T("wpf.down", "Down");
    public string Bottom => T("wpf.bottom", "Bottom");
    public string SaveProfile => T("wpf.save_profile", "Save Profile");
    public string LaunchGame => T("wpf.launch_game", "Launch Game");
    public string CrashCheck => T("wpf.crash_check", "Crash Check");
    public string StatusCore => T("wpf.status_core", ".NET / Rust core");
    public string Profiles => T("wpf.profiles", "Profiles");
    public string RefreshProfiles => T("wpf.refresh_profiles", "Refresh Profiles");
    public string Rename => T("wpf.rename", "Rename");
    public string Copy => T("wpf.copy", "Copy");
    public string Delete => T("wpf.delete", "Delete");
    public string Categories => T("wpf.categories", "Categories");
    public string ModInventory => T("wpf.mod_inventory", "Mod Inventory");
    public string PresetName => T("wpf.preset_name", "Preset name");
    public string SavePreset => T("wpf.save_preset", "Save Preset");
    public string Mods => T("wpf.tab_mods", "Mods");
    public string Saves => T("wpf.tab_saves", "Saves");
    public string Refresh => T("wpf.refresh", "Refresh");
    public string CopySlot => T("wpf.copy_slot", "Copy Slot");
    public string Money => T("wpf.money", "Money");
    public string Xp => T("wpf.xp", "XP");
    public string Level => T("wpf.level", "Level");
    public string Set => T("wpf.set", "Set");
    public string Repair => T("wpf.repair", "Repair");
    public string Refuel => T("wpf.refuel", "Refuel");
    public string UnlockGarages => T("wpf.unlock_garages", "Unlock Garages");
    public string Localization => T("wpf.tab_localization", "Localization");
    public string ScanLocalization => T("wpf.scan_localization", "Scan Localization");
    public string Cancel => T("wpf.cancel", "Cancel");
    public string ImportDictionary => T("wpf.import_dictionary", "Import Dictionary");
    public string ExportScs => T("wpf.export_scs", "Export SCS");
    public string Diagnostics => T("wpf.tab_diagnostics", "Diagnostics");
    public string AnalyzeLatestCrash => T("wpf.analyze_crash", "Analyze Latest Crash");
    public string PrecheckProfile => T("wpf.precheck_profile", "Precheck Profile");
    public string Cities => T("wpf.tab_cities", "Cities");
    public string SearchCities => T("wpf.search_cities", "Search Cities");
    public string Workshop => T("wpf.tab_workshop", "Workshop");
    public string RefreshWorkshop => T("wpf.refresh_workshop", "Refresh Workshop Metadata");
    public string Tools => T("wpf.tab_tools", "Tools");
    public string ModDirectoryMigration => T("wpf.mod_directory_migration", "Mod Directory Migration");
    public string MigrateAndLink => T("wpf.migrate_link", "Migrate and Link");
    public string Restore => T("wpf.restore", "Restore");
    public string Updates => T("wpf.updates", "Updates");
    public string CheckUpdates => T("wpf.check_updates", "Check for Updates");
    public string InstallUpdate => T("wpf.install_update", "Install Update");
    public string ExternalExtractor => T("wpf.external_extractor", "External Archive Extractor");
    public string ExtractEntry => T("wpf.extract_entry", "Extract Entry");
    public string ModPreview => T("wpf.mod_preview", "Mod Preview");
    public string NoModSelected => T("wpf.no_mod_selected", "No mod selected");
    public string SelectModHint => T("wpf.select_mod_hint", "Select a mod to inspect it");
    public string Source => T("wpf.source", "Source");
    public string Package => T("wpf.package", "Package");
    public string Category => T("wpf.category", "Category");
    public string Enabled => T("wpf.enabled", "Enabled");
    public string Name => T("wpf.name", "Name");
    public string Translation => T("wpf.translation", "Translation");
    public string Status => T("wpf.status", "Status");
    public string Key => T("wpf.key", "Key");
    public string CompanyNameHint => T("wpf.company_name_hint", "Company name");
    public string ProfileNameHint => T("wpf.profile_name_hint", "Profile name");
    public string TargetDirectoryHint => T("wpf.target_directory_hint", "Target directory");
    public string RepositoryHint => T("wpf.repository_hint", "GitHub owner/name");
    public string PackagePathHint => T("wpf.package_path_hint", "Package path");
    public string TargetLocaleHint => T("wpf.target_locale_hint", "Target locale");
    public string EntryNameHint => T("wpf.entry_name_hint", "Entry name");
    public string LanguageLabel => T("wpf.language", "Language");
    public string CoreFooter => T("wpf.core_footer", ".NET / Rust core");
    public string CancelScan => T("wpf.cancel_scan", "Cancel Scan");
    public string LoadPreset => T("wpf.load_preset", "Load");
    public string DeletePreset => T("wpf.delete_preset", "Delete");
    public string UnlockDealers => T("wpf.unlock_dealers", "Unlock Dealers");
    public string SelectBaseline => T("wpf.select_baseline", "Select Baseline");
    public string ClearBaseline => T("wpf.clear_baseline", "Clear Baseline");
    public string CategoryManagement => T("wpf.category_management", "Categories");
    public string CategoryNameHint => T("wpf.category_name_hint", "Category name");
    public string CreateCategory => T("wpf.create_category", "Create");
    public string AssignUncategorized => T("wpf.assign_uncategorized", "Assign Uncategorized");
    public string RefreshCategories => T("wpf.refresh_categories", "Refresh Categories");

    private static string LoadLanguage()
    {
        try
        {
            var path = Path.Combine(AppContext.BaseDirectory, "config", "language.json");
            if (File.Exists(path))
            {
                using var document = JsonDocument.Parse(File.ReadAllText(path));
                var value = document.RootElement.TryGetProperty("language", out var property) ? property.GetString() : null;
                if (SupportedLanguages.Contains(value ?? "", StringComparer.OrdinalIgnoreCase)) return value!;
            }
        }
        catch (JsonException) { }
        catch (IOException) { }
        return "zh_CN";
    }

    private static void SaveLanguage(string value)
    {
        try
        {
            var directory = Path.Combine(AppContext.BaseDirectory, "config");
            Directory.CreateDirectory(directory);
            File.WriteAllText(Path.Combine(directory, "language.json"), JsonSerializer.Serialize(new { language = value }));
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }
}
