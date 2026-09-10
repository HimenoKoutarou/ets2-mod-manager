using System.Text.Json;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Net;
using System.Net.Http;
using System.Text;
using ETS2ModManager.Application;
using ETS2ModManager.Contracts;
using ETS2ModManager.Domain;
using ETS2ModManager.Infrastructure.Archives;
using ETS2ModManager.Infrastructure.Backup;
using ETS2ModManager.Infrastructure.Categories;
using ETS2ModManager.Infrastructure.Cities;
using ETS2ModManager.Infrastructure.Indexing;
using ETS2ModManager.Infrastructure.Localization;
using ETS2ModManager.Infrastructure.Profiles;
using ETS2ModManager.Infrastructure.Scanning;
using ETS2ModManager.Infrastructure.Saves;
using ETS2ModManager.Infrastructure.Rust;
using ETS2ModManager.Infrastructure.Session;
using ETS2ModManager.Infrastructure.Updates;

using var document = JsonDocument.Parse(File.ReadAllText(
    Path.Combine(AppContext.BaseDirectory, "migration_contracts_v1.json")));
var root = document.RootElement;
if (root.GetProperty("contract_version").GetInt32() != 1)
{
    throw new InvalidOperationException("Unsupported golden contract version.");
}

foreach (var item in root.GetProperty("identity_cases").EnumerateArray())
{
    var input = item.GetProperty("input").GetString();
    EqualString(item.GetProperty("canonical_key").GetString(), ModIdentity.CanonicalKey(input));
    EqualString(item.GetProperty("legacy_workshop_id").GetString(), ModIdentity.LegacyWorkshopId(input));
    var expectedAliases = item.GetProperty("aliases").EnumerateArray()
        .Select(value => value.GetString() ?? string.Empty).ToArray();
    EqualSequence(expectedAliases, ModIdentity.ProfileEntryAliases(input));
}

var priority = root.GetProperty("priority_case");
var active = Strings(priority.GetProperty("profile_active_mods"));
var packages = Strings(priority.GetProperty("all_package_names"));
var worklist = PriorityRules.BuildWorklist(active, packages);
EqualWorklist(priority.GetProperty("built_worklist"), worklist);
EqualSequence(Strings(priority.GetProperty("ui_active_mods")),
    worklist.Where(row => row.Enabled).Select(row => row.PackageName));
EqualSequence(Strings(priority.GetProperty("roundtrip_profile_active_mods")),
    PriorityRules.WorklistToProfileActive(worklist));
EqualWorklist(
    priority.GetProperty("disable_index_1"),
    PriorityRules.BatchToggle(worklist, [1], "disable"));
EqualWorklist(
    priority.GetProperty("move_index_0_to_bottom"),
    PriorityRules.MoveBottom(worklist, [0]));
EqualSequence(["mid", "high", "low", "extra"],
    PriorityRules.MoveUp(worklist, [1]).Select(row => row.PackageName));
EqualSequence(["mid", "high", "low", "extra"],
    PriorityRules.MoveDown(worklist, [0]).Select(row => row.PackageName));
EqualSequence(["low", "high", "mid", "extra"],
    PriorityRules.MoveTop(worklist, [2]).Select(row => row.PackageName));
EqualSequence(["mid", "low", "high", "extra"],
    PriorityRules.MoveBottom(worklist, [0]).Select(row => row.PackageName));
var disabled = PriorityRules.BatchToggle(worklist, [1], "disable");
var reenabled = PriorityRules.BatchToggle(disabled, [2], "enable");
EqualSequence(["high", "low", "mid", "extra"], reenabled.Select(row => row.PackageName));
EqualSequence(["mid", "low", "high"], PriorityRules.WorklistToProfileActive(reenabled));

var dto = root.GetProperty("dto_case");
var eventDto = new ProgressEvent(
    dto.GetProperty("operation").GetString() ?? "",
    dto.GetProperty("phase").GetString() ?? "",
    dto.GetProperty("current").GetInt32(),
    dto.GetProperty("total").GetInt32(),
    dto.GetProperty("item").GetString() ?? "",
    ScanStatus.Running,
    dto.GetProperty("message").GetString() ?? "");
EqualString(dto.GetProperty("operation").GetString(), eventDto.Operation);
EqualString(dto.GetProperty("phase").GetString(), eventDto.Phase);
EqualString(dto.GetProperty("item").GetString(), eventDto.Item);
EqualString(dto.GetProperty("message").GetString(), eventDto.Message);
EqualString(dto.GetProperty("status").GetString(), eventDto.Status.ToString().ToLowerInvariant());

    await RunInfrastructureSmokeAsync();
    await RunAdapterContractsAsync();

    RunNativeContract();

Console.WriteLine("Migration contract v1 passed.");
return 0;

static string[] Strings(JsonElement element) => element.EnumerateArray()
    .Select(value => value.GetString() ?? string.Empty).ToArray();

static string ReadZipText(ZipArchiveEntry entry)
{
    using var reader = new StreamReader(entry.Open(), Encoding.UTF8, detectEncodingFromByteOrderMarks: true);
    return reader.ReadToEnd();
}

static void EqualString(string? expected, string? actual)
{
    if (!string.Equals(expected, actual, StringComparison.Ordinal))
    {
        throw new InvalidOperationException($"Expected '{expected}', got '{actual}'.");
    }
}

static void EqualSequence(IEnumerable<string> expected, IEnumerable<string> actual)
{
    if (!expected.SequenceEqual(actual, StringComparer.Ordinal))
    {
        throw new InvalidOperationException(
            $"Expected [{string.Join(',', expected)}], got [{string.Join(',', actual)}].");
    }
}

static void EqualWorklist(JsonElement expected, IEnumerable<WorklistEntry> actual)
{
    var expectedRows = expected.EnumerateArray().ToArray();
    var actualRows = actual.ToArray();
    if (expectedRows.Length != actualRows.Length)
    {
        throw new InvalidOperationException(
            $"Expected {expectedRows.Length} worklist rows, got {actualRows.Length}.");
    }
    for (var index = 0; index < actualRows.Length; index++)
    {
        var expectedRow = expectedRows[index];
        var actualRow = actualRows[index];
        EqualString(expectedRow.GetProperty("package_name").GetString(), actualRow.PackageName);
        if (expectedRow.GetProperty("enabled").GetBoolean() != actualRow.Enabled
            || expectedRow.GetProperty("order").GetInt32() != actualRow.Order
            || NullableInt(expectedRow.GetProperty("priority_index")) != actualRow.PriorityIndex)
        {
            throw new InvalidOperationException($"Worklist mismatch at index {index}.");
        }
    }
}

static int? NullableInt(JsonElement value) =>
    value.ValueKind == JsonValueKind.Null ? null : value.GetInt32();

static async Task RunInfrastructureSmokeAsync()
{
    var root = Path.Combine(Path.GetTempPath(), "ets2mm-contract-" + Guid.NewGuid().ToString("N"));
    var profiles = Path.Combine(root, "profiles");
    var profileFolder = Path.Combine(profiles, "test_profile");
    var mods = Path.Combine(root, "mod");
    var backups = Path.Combine(root, "backups");
    Directory.CreateDirectory(profileFolder);
    Directory.CreateDirectory(mods);
    var profileSii = Path.Combine(profileFolder, "profile.sii");
    var profileText = "SiiNunit\n{\nprofile : _nameless.test {\n profile_name: \"Driver\"\n company_name: \"Company\"\n active_mods: 1\n active_mods[0]: \"base_mod\"\n}\n}\n";
    File.WriteAllBytes(profileSii, CreateScsC(profileText));
    File.WriteAllText(Path.Combine(mods, "map_pack.scs"), "sample");
    try
    {
        var backup = new FileBackupStore(backups);
        var repository = new FileProfileRepository(profiles, null, null, backup);
        var profile = repository.ListProfiles().Single();
        EqualSequence(["base_mod"], repository.ReadActiveMods(profile));
        repository.ReplaceActiveMods(profile, ["base_mod", "traffic_mod"], true);
        EqualSequence(["base_mod", "traffic_mod"], repository.ReadActiveMods(profile));
        if (!Directory.EnumerateFiles(backups, "*.zip").Any()) throw new InvalidOperationException("Profile backup was not created.");

        var scanner = new FileSystemModScanner(mods, null);
        var scan = await scanner.ScanAsync(null, CancellationToken.None);
        if (scan.Mods.Count != 1 || scan.Mods[0].ModId != "map_pack") throw new InvalidOperationException("Filesystem scanner contract failed.");
        var index = new SqliteModIndex(Path.Combine(root, "cache", "mods.db"));
        index.Upsert(scan);
        if (index.Query().Count != 1) throw new InvalidOperationException("SQLite index contract failed.");
        index.Replace(new ModScanResult([], [], 0, 0, ScanStatus.Completed, null));
        if (index.Query().Count != 0) throw new InvalidOperationException("SQLite full-scan replacement contract failed.");
        index.Replace(scan);

        await RunSaveContractAsync(root, backup);
        await RunIncrementalScanContractAsync(root);
    }
    finally
    {
        try { Directory.Delete(root, true); } catch { }
    }
}

static async Task RunIncrementalScanContractAsync(string root)
{
    var mods = Path.Combine(root, "incremental-mods");
    Directory.CreateDirectory(mods);
    var first = Path.Combine(mods, "first_mod");
    Directory.CreateDirectory(first);
    File.WriteAllText(Path.Combine(first, "manifest.sii"),
        "SiiNunit\n{\nmod_package : first_mod {\n display_name: \"First Mod\"\n}\n}\n");
    var second = Path.Combine(mods, "second_mod.scs");
    File.WriteAllText(second, "not-a-zip");

    var index = new SqliteModIndex(Path.Combine(root, "incremental-cache", "mods.db"));
    var scanner = new IncrementalModScanner(mods, null, index.Query);
    var progressMessages = new List<string>();
    var progress = new ImmediateProgress<ProgressEvent>(eventInfo => progressMessages.Add(eventInfo.Message));

    var initial = await scanner.ScanAsync(progress, CancellationToken.None);
    if (initial.Mods.Count != 2 || !initial.Mods.Any(row => row.DisplayName == "First Mod"))
        throw new InvalidOperationException("Incremental initial scan contract failed.");
    var initialSync = index.SyncSnapshot(initial);
    if (initialSync.Added != 2 || initialSync.Updated != 0 || initialSync.Removed != 0)
        throw new InvalidOperationException("Incremental initial persistence contract failed.");

    progressMessages.Clear();
    var cached = await scanner.ScanAsync(progress, CancellationToken.None);
    if (cached.Mods.Count != 2 || !progressMessages.Any(message => message.Contains("0 changed", StringComparison.Ordinal)))
        throw new InvalidOperationException("Incremental unchanged scan did not reuse cache.");
    var cachedSync = index.SyncSnapshot(cached);
    if (cachedSync.Added != 0 || cachedSync.Updated != 0 || cachedSync.Removed != 0)
        throw new InvalidOperationException("Incremental unchanged persistence rewrote cache.");

    var added = Path.Combine(mods, "added_mod.scs");
    File.WriteAllText(added, "new");
    var withAdded = await scanner.ScanAsync(null, CancellationToken.None);
    if (withAdded.Mods.Count != 3 || !withAdded.NewModIds.Contains("added_mod", StringComparer.OrdinalIgnoreCase))
        throw new InvalidOperationException("Incremental add contract failed.");
    var addSync = index.SyncSnapshot(withAdded);
    if (addSync.Added != 1 || addSync.Updated != 0 || addSync.Removed != 0)
        throw new InvalidOperationException("Incremental add persistence contract failed.");

    File.Delete(second);
    var withDeleted = await scanner.ScanAsync(null, CancellationToken.None);
    if (withDeleted.Mods.Count != 2 || withDeleted.Mods.Any(row => row.ModId == "second_mod"))
        throw new InvalidOperationException("Incremental delete contract failed.");
    var deleteSync = index.SyncSnapshot(withDeleted);
    if (deleteSync.Added != 0 || deleteSync.Updated != 0 || deleteSync.Removed != 1)
        throw new InvalidOperationException("Incremental delete persistence contract failed.");

    var firstManifest = Path.Combine(first, "manifest.sii");
    File.WriteAllText(firstManifest,
        "SiiNunit\n{\nmod_package : first_mod {\n display_name: \"First Mod Updated\"\n}\n}\n");
    var withModified = await scanner.ScanAsync(null, CancellationToken.None);
    if (withModified.Mods.First(row => row.ModId == "first_mod").DisplayName != "First Mod Updated")
        throw new InvalidOperationException("Incremental modified package contract failed.");
    var updateSync = index.SyncSnapshot(withModified);
    if (updateSync.Added != 0 || updateSync.Updated != 1 || updateSync.Removed != 0)
        throw new InvalidOperationException("Incremental modified persistence contract failed.");
}

static async Task RunAdapterContractsAsync()
{
    var root = Path.Combine(Path.GetTempPath(), "ets2mm-adapters-" + Guid.NewGuid().ToString("N"));
    Directory.CreateDirectory(root);
    try
    {
        var categoryRoot = Path.Combine(root, "category-store");
        var categories = new JsonCategoryService(categoryRoot);
        if (!categories.CreateFolder("Maps").Success) throw new InvalidOperationException("Category create contract failed.");
        if (!categories.SetCategory("mod-a", "Maps").Success) throw new InvalidOperationException("Category assignment contract failed.");
        var snapshot = categories.Snapshot();
        if (!snapshot.Folders.SequenceEqual(["Maps"]) || snapshot.Stats["Maps"] != 1) throw new InvalidOperationException("Category persistence contract failed.");
        if (!categories.RenameFolder("Maps", "Cities").Success || categories.GetCategory("mod-a") != "Cities") throw new InvalidOperationException("Category rename contract failed.");
        if (!categories.DeleteFolder("Cities").Success || categories.GetCategory("mod-a") != "") throw new InvalidOperationException("Category delete contract failed.");

        var package = Path.Combine(root, "city-package");
        Directory.CreateDirectory(Path.Combine(package, "def", "nested"));
        File.WriteAllText(Path.Combine(package, "def", "nested", "city_berlin.sii"),
            "SiiNunit\n{\ncity_data : city.berlin {\n city_name: \"Berlin\"\n short_city_name: \"BER\"\n country: \"germany\"\n}\n}\n");
        var cityMod = new ModRecord("mod-a", "city_mod", package, "directory", "City Mod", 0, 0);
        var cityResults = await new CityLookupService().RebuildAndSearchAsync([cityMod], "ber", CancellationToken.None);
        if (cityResults.Count != 1 || cityResults[0].CityName != "Berlin" || cityResults[0].Sources.Count != 1) throw new InvalidOperationException("Nested city lookup contract failed.");

        var archivePath = Path.Combine(root, "package.zip");
        using (var archive = ZipFile.Open(archivePath, ZipArchiveMode.Create))
        using (var writer = new StreamWriter(archive.CreateEntry("def/manifest.sii").Open(), Encoding.UTF8)) writer.Write("package_name: \"zip_mod\"\n");
        using (var archive = ZipFile.Open(archivePath, ZipArchiveMode.Update))
        using (var writer = new StreamWriter(archive.CreateEntry("def/../../escaped.txt").Open(), Encoding.UTF8)) writer.Write("must not escape");
        var extraction = await new ExternalArchiveService(Path.Combine(root, "tools")).ExtractManifestAsync(archivePath, CancellationToken.None);
        if (!extraction.Success || extraction.Text is null || !extraction.Text.Contains("zip_mod", StringComparison.Ordinal)) throw new InvalidOperationException("ZIP extraction contract failed.");
        var treeDestination = Path.Combine(root, "tree-output");
        var tree = await new ExternalArchiveService(Path.Combine(root, "tools")).ExtractTreeAsync(
            archivePath, treeDestination, ["def", "locale/zh_cn"], CancellationToken.None);
        if (!tree.Success || !File.Exists(Path.Combine(treeDestination, "def", "manifest.sii")))
            throw new InvalidOperationException("ZIP tree extraction contract failed.");
        if (File.Exists(Path.Combine(root, "escaped.txt")))
            throw new InvalidOperationException("ZIP tree extraction allowed path traversal.");
        var traversalRoot = await new ExternalArchiveService(Path.Combine(root, "tools")).ExtractTreeAsync(
            archivePath, treeDestination, ["../def"], CancellationToken.None);
        if (traversalRoot.Success)
            throw new InvalidOperationException("Archive extraction accepted a traversal root.");
        var toolRoot = Path.Combine(root, "tools");
        Directory.CreateDirectory(toolRoot);
        File.WriteAllBytes(Path.Combine(toolRoot, "sxc64.exe"), [0]);
        if (!new ExternalArchiveService(toolRoot).Availability.Any) throw new InvalidOperationException("SXC availability contract failed.");

        var opaqueRoot = Path.Combine(root, "opaque-root");
        Directory.CreateDirectory(opaqueRoot);
        var opaquePath = Path.Combine(opaqueRoot, "opaque.scs");
        File.WriteAllBytes(opaquePath, "SCS#opaque"u8.ToArray());
        var opaqueScan = await new FileSystemModScanner(opaqueRoot, null, new StubArchiveService(
            "SiiNunit\n{\nmod_package : opaque_mod {\n display_name: \"Opaque Mod\"\n}\n}\n")).ScanAsync(null, CancellationToken.None);
        if (opaqueScan.Mods.Count != 1 || opaqueScan.Mods[0].PackageName != "opaque_mod" || opaqueScan.Mods[0].DisplayName != "Opaque Mod") throw new InvalidOperationException("External manifest scanner contract failed.");

        var opaqueLocalizationPath = Path.Combine(opaqueRoot, "opaque-l10n.scs");
        File.WriteAllBytes(opaqueLocalizationPath, "SCS#l10n"u8.ToArray());
        var externalTreeArchive = new StubArchiveService(
            "manifest",
            (destination, _) =>
            {
                var localeDirectory = Path.Combine(destination, "locale", "zh_cn");
                Directory.CreateDirectory(localeDirectory);
                File.WriteAllText(Path.Combine(localeDirectory, "generated.sii"),
                    "SiiNunit { localization_db : .external { key[]: \"external.city\" val[]: \"外部城市\" } }");
            });
        var externalL10n = new FileLocalizationService(Path.Combine(root, "external-l10n.json"), externalTreeArchive);
        var externalL10nScan = await externalL10n.ScanAsync([opaqueLocalizationPath], null, CancellationToken.None);
        if (externalL10nScan.Entries.Count != 1 || externalL10nScan.Entries[0].Value != "外部城市")
            throw new InvalidOperationException("External localization tree scan contract failed.");

        var translations = Path.Combine(root, "i18n");
        Directory.CreateDirectory(translations);
        File.WriteAllText(Path.Combine(translations, "en_US.json"), "{\"hello\":\"Hello\"}");
        var translation = new JsonTranslationService(translations);
        if (!translation.Languages.Contains("en_US", StringComparer.OrdinalIgnoreCase) || translation.Translate("en_US", "hello") != "Hello") throw new InvalidOperationException("Translation contract failed.");

        var l10nPackage = Path.Combine(root, "l10n-package");
        var l10nLocale = Path.Combine(l10nPackage, "locale", "zh_cn");
        Directory.CreateDirectory(l10nLocale);
        File.WriteAllText(Path.Combine(l10nLocale, "localization.sii"),
            "SiiNunit\n{\nlocalization_db : .localization\n{\n key[]: \"city.berlin\"\n val[]: \"柏林\"\n key[]: \"city.empty\"\n val[]: \"\"\n}\n}\n");
        var dictPath = Path.Combine(root, "l10n_dict.json");
        var l10nIndex = new SqliteLocalizationIndex(Path.Combine(root, "cache", "localization.db"));
        var l10n = new FileLocalizationService(dictPath, null, l10nIndex);
        var l10nScan = await l10n.ScanAsync([l10nPackage], null, CancellationToken.None);
        if (l10nScan.Entries.Count != 2 || l10nScan.Entries[0].Status != "native" || l10nScan.Entries[1].Status != "missing_value")
            throw new InvalidOperationException("Localization array scan contract failed.");
        var localizationProgress = new List<string>();
        var cachedL10nScan = await l10n.ScanAsync(
            [l10nPackage],
            new ImmediateProgress<ProgressEvent>(eventValue => localizationProgress.Add(eventValue.Message)),
            CancellationToken.None);
        if (cachedL10nScan.Entries.Count != 2 || !localizationProgress.Any(message => message.Contains("Using cached localization", StringComparison.Ordinal)))
            throw new InvalidOperationException("Localization unchanged scan did not reuse cache.");
        File.WriteAllText(Path.Combine(root, "custom.csv"), "city.empty,空城市\n");
        var imported = l10n.ImportDictionary(Path.Combine(root, "custom.csv"), true);
        if (imported.Imported != 1 || l10n.ReadDictionary()["city.empty"] != "空城市") throw new InvalidOperationException("Localization dictionary import contract failed.");
        localizationProgress.Clear();
        l10nScan = await l10n.ScanAsync(
            [l10nPackage],
            new ImmediateProgress<ProgressEvent>(eventValue => localizationProgress.Add(eventValue.Message)),
            CancellationToken.None);
        if (l10nScan.Entries[1].Status != "local" || l10nScan.Entries[1].Value != "空城市") throw new InvalidOperationException("Localization dictionary resolution contract failed.");
        if (!localizationProgress.Any(message => message.Contains("Using cached localization", StringComparison.Ordinal)))
            throw new InvalidOperationException("Localization dictionary change rescanned package data.");

        var changedLocalizationText = "SiiNunit\n{\nlocalization_db : .localization\n{\n key[]: \"city.berlin\"\n val[]: \"柏林更新\"\n key[]: \"city.empty\"\n val[]: \"\"\n}\n}\n";
        File.WriteAllText(Path.Combine(l10nLocale, "localization.sii"), changedLocalizationText);
        localizationProgress.Clear();
        var changedL10nScan = await l10n.ScanAsync(
            [l10nPackage],
            new ImmediateProgress<ProgressEvent>(eventValue => localizationProgress.Add(eventValue.Message)),
            CancellationToken.None);
        if (changedL10nScan.Entries[0].Value != "柏林更新"
            || !localizationProgress.Any(message => message.Contains("Scanning localization", StringComparison.Ordinal)))
            throw new InvalidOperationException("Localization modified package invalidation failed.");

        var addedL10nPackage = Path.Combine(root, "l10n-added-package");
        var addedL10nLocale = Path.Combine(addedL10nPackage, "locale", "zh_cn");
        Directory.CreateDirectory(addedL10nLocale);
        File.WriteAllText(Path.Combine(addedL10nLocale, "added.sii"),
            "SiiNunit { localization_db : .added { key[]: \"city.added\" val[]: \"新增城市\" } }");
        var addedL10nScan = await l10n.ScanAsync([l10nPackage, addedL10nPackage], null, CancellationToken.None);
        if (addedL10nScan.Entries.All(entry => entry.Key != "city.added"))
            throw new InvalidOperationException("Localization added package contract failed.");

        var removedL10nScan = await l10n.ScanAsync([l10nPackage], null, CancellationToken.None);
        if (removedL10nScan.Entries.Any(entry => entry.Key == "city.added"))
            throw new InvalidOperationException("Localization removed package contract failed.");
        l10n.SetTranslation("city.manual", "手动翻译");
        var exportPath = Path.Combine(root, "generated.scs");
        var exported = await l10n.ExportAsync(l10nScan.Entries.Append(new LocalizationEntry("city.manual", "手动翻译", "", "test", "city", "local")), exportPath, "zh_cn", "Contract L10n", CancellationToken.None);
        if (!exported.Success || exported.EntriesWritten != 3 || !File.Exists(exportPath)) throw new InvalidOperationException("Localization export contract failed.");
        using (var exportedZip = ZipFile.OpenRead(exportPath))
        {
            var generated = exportedZip.GetEntry("locale/zh_cn/generated.sii");
            if (generated is null || !ReadZipText(generated).Contains("city.manual", StringComparison.Ordinal)) throw new InvalidOperationException("Localization generated SII contract failed.");
        }
        var defPackage = Path.Combine(root, "l10n-def-package.zip");
        using (var defZip = ZipFile.Open(defPackage, ZipArchiveMode.Create))
        {
            using var defWriter = new StreamWriter(defZip.CreateEntry("def/city.sii").Open(), Encoding.UTF8);
            defWriter.Write("SiiNunit { city_data : city.missing { city_name: \"Missing City\" } }");
        }
        var defScan = await l10n.ScanAsync([defPackage], null, CancellationToken.None);
        var missingDefinition = defScan.Entries.SingleOrDefault(entry => entry.UnitName == "city.missing");
        if (missingDefinition is null || missingDefinition.DefLocaleKeyPresent || missingDefinition.LocaleKey != "Missing City")
            throw new InvalidOperationException("Localization definition metadata contract failed.");
        var defExportPath = Path.Combine(root, "generated-def.scs");
        var defExport = await l10n.ExportAsync([missingDefinition with { Value = "缺失城市", Status = "local" }], defExportPath, "zh_cn", "Definition L10n", CancellationToken.None);
        if (!defExport.Success) throw new InvalidOperationException("Localization definition export contract failed.");
        using (var defExportZip = ZipFile.OpenRead(defExportPath))
        {
            var overrideEntry = defExportZip.GetEntry("def/city/generated.sii");
            if (overrideEntry is null || !ReadZipText(overrideEntry).Contains("city_name_localized: \"@@Missing City@@\"", StringComparison.Ordinal))
                throw new InvalidOperationException("Localization definition override contract failed.");
        }

        var highPackage = Path.Combine(root, "high-priority.zip");
        using (var highZip = ZipFile.Open(highPackage, ZipArchiveMode.Create))
        using (var highWriter = new StreamWriter(highZip.CreateEntry("locale/zh_cn/shared.sii").Open(), Encoding.UTF8))
            highWriter.Write("SiiNunit { localization_db : .high { key[]: \"priority.shared\" val[]: \"HIGH\" } }");
        var lowPackage = Path.Combine(root, "low-priority.zip");
        using (var lowZip = ZipFile.Open(lowPackage, ZipArchiveMode.Create))
        using (var lowWriter = new StreamWriter(lowZip.CreateEntry("locale/zh_cn/shared.sii").Open(), Encoding.UTF8))
            lowWriter.Write("SiiNunit { localization_db : .low { key[]: \"priority.shared\" val[]: \"LOW\" } }");
        var priorityScan = await l10n.ScanAsync([highPackage, lowPackage], null, CancellationToken.None);
        if (priorityScan.Entries.Count != 1 || priorityScan.Entries[0].Value != "HIGH")
            throw new InvalidOperationException("Localization priority merge contract failed.");

        var blockedHigh = Path.Combine(root, "blocked-high.zip");
        using (var blockedZip = ZipFile.Open(blockedHigh, ZipArchiveMode.Create))
        using (var blockedWriter = new StreamWriter(blockedZip.CreateEntry("locale/zh_cn/shared.sii").Open(), Encoding.UTF8))
            blockedWriter.Write("SiiNunit { localization_db : .blocked { key[]: \"priority.blocked\" val[]: \"\" } }");
        var blockedLow = Path.Combine(root, "blocked-low.zip");
        using (var blockedZip = ZipFile.Open(blockedLow, ZipArchiveMode.Create))
        using (var blockedWriter = new StreamWriter(blockedZip.CreateEntry("locale/zh_cn/shared.sii").Open(), Encoding.UTF8))
            blockedWriter.Write("SiiNunit { localization_db : .blocked { key[]: \"priority.blocked\" val[]: \"LOW\" } }");
        var blockedScan = await l10n.ScanAsync([blockedHigh, blockedLow], null, CancellationToken.None);
        var blocked = blockedScan.Entries.SingleOrDefault(entry => entry.Key == "priority.blocked");
        if (blocked is null || blocked.Value.Length != 0 || blocked.Status != "missing_value")
            throw new InvalidOperationException("Localization empty-value precedence contract failed.");

        var mergedPackage = Path.Combine(root, "definition-and-locale.zip");
        using (var mergedZip = ZipFile.Open(mergedPackage, ZipArchiveMode.Create))
        {
            using (var localeWriter = new StreamWriter(mergedZip.CreateEntry("locale/zh_cn/merged.sii").Open(), Encoding.UTF8))
                localeWriter.Write("SiiNunit { localization_db : .merged { key[]: \"city.meta\" val[]: \"元数据城市\" } }");
            using (var defWriter = new StreamWriter(mergedZip.CreateEntry("def/city.sii").Open(), Encoding.UTF8))
                defWriter.Write("SiiNunit { city_data : city.meta { city_name: \"Meta City\" city_name_localized: \"@@city.meta@@\" } }");
        }
        var mergedScan = await l10n.ScanAsync([mergedPackage], null, CancellationToken.None);
        var mergedEntry = mergedScan.Entries.SingleOrDefault(entry => entry.Key == "city.meta");
        if (mergedEntry is null || mergedEntry.Value != "元数据城市" || mergedEntry.UnitName != "city.meta"
            || !mergedEntry.DefLocaleKeyPresent)
            throw new InvalidOperationException("Localization definition/locale merge contract failed.");

        var session = new JsonSessionStore(Path.Combine(root, "session.json"));
        session.Save(new SessionContract("profile-a", 3));
        if (session.Load<SessionContract>() is not { ProfileId: "profile-a", SelectedIndex: 3 }) throw new InvalidOperationException("Session persistence contract failed.");

        var backupSource = Path.Combine(root, "backup-source.sii");
        var backupRoot = Path.Combine(root, "backups");
        var rollingBackup = new FileBackupStore(backupRoot, 10);
        for (var i = 0; i < 12; i++)
        {
            File.WriteAllText(backupSource, $"{i}");
            rollingBackup.Backup(backupSource, "contract");
        }
        if (Directory.GetFiles(backupRoot, "backup-source_*.zip").Length > 10) throw new InvalidOperationException("Backup retention contract failed.");

        var handler = new StubHttpHandler(_ => new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent("{\"tag_name\":\"v2.0.0\",\"assets\":[{\"name\":\"release.zip\",\"size\":12,\"browser_download_url\":\"https://example.test/release.zip\"}]}", Encoding.UTF8, "application/json")
        });
        var update = await new GitHubUpdateService(new HttpClient(handler)).CheckAsync("1.0.0", "https://github.com/owner/repo.git", CancellationToken.None);
        if (!update.IsAvailable || update.LatestVersion != "2.0.0" || update.DownloadUrl is null) throw new InvalidOperationException("Update check contract failed.");
        var invalid = await new GitHubUpdateService(new HttpClient(handler)).CheckAsync("1.0.0", "bad-repository", CancellationToken.None);
        if (invalid.IsAvailable || !invalid.Message.Contains("owner/name", StringComparison.Ordinal)) throw new InvalidOperationException("Update repository validation contract failed.");
        var updater = new GitHubUpdateService(new HttpClient(handler));
        var noUrl = await updater.DownloadAndInstallAsync(
            new UpdateInfo(true, "1.0.0", "2.0.0", null, "missing URL"), root, "ETS2ModManager.WpfClient.exe", CancellationToken.None);
        if (noUrl.Success || !noUrl.Message.Contains("No downloadable update", StringComparison.Ordinal))
            throw new InvalidOperationException("Update missing URL contract failed.");
        var invalidExecutable = await updater.DownloadAndInstallAsync(
            update, root, "nested\\ETS2ModManager.WpfClient.exe", CancellationToken.None);
        if (invalidExecutable.Success || !invalidExecutable.Message.Contains("file name without directory", StringComparison.Ordinal))
            throw new InvalidOperationException("Update executable name validation contract failed.");
        var missingInstall = await updater.DownloadAndInstallAsync(
            update, Path.Combine(root, "missing-install"), "ETS2ModManager.WpfClient.exe", CancellationToken.None);
        if (missingInstall.Success || !missingInstall.Message.Contains("Install directory does not exist", StringComparison.Ordinal))
            throw new InvalidOperationException("Update missing install directory contract failed.");
        var malformed = await updater.DownloadAndInstallAsync(
            update with { DownloadUrl = "https://example.test/malformed.zip" }, root, "ETS2ModManager.WpfClient.exe", CancellationToken.None);
        if (malformed.Success || malformed.StagedDirectory is not null || !malformed.Message.Contains("Update installation failed", StringComparison.Ordinal))
            throw new InvalidOperationException("Update malformed package contract failed.");
    }
    finally
    {
        try { Directory.Delete(root, true); } catch { }
    }
}

static void RunNativeContract()
{
    try
    {
        var client = new Ets2CoreClient();
        client.EnsureCompatible();
        var response = client.InspectBytes("BSII\x03\0\0\0"u8.ToArray());
        if (!response.Contains("\"kind\":\"bsii\"", StringComparison.Ordinal)) throw new InvalidOperationException("Rust FFI response contract failed.");
    }
    catch (DllNotFoundException)
    {
        Console.WriteLine("Rust FFI contract skipped: native DLL is not available in this test output.");
    }
}

static async Task RunSaveContractAsync(string root, IBackupStore backup)
{
    var profileFolder = Path.Combine(root, "profiles", "save-contract");
    var slotFolder = Path.Combine(profileFolder, "save", "1");
    Directory.CreateDirectory(slotFolder);
    var profile = new ProfileRef("save-contract", "local", profileFolder, Path.Combine(profileFolder, "profile.sii"), "Save Contract", "", 0);
    var gamePath = Path.Combine(slotFolder, "game.sii");
    var plain = BuildCanonicalBsii(279375, 1253729);
    File.WriteAllBytes(gamePath, ScsCCodec.Encrypt(plain));
    var service = new SaveEditorService(backup);
    WriteSaveSlot(profileFolder, "1", "Zulu Save", plain);
    WriteSaveSlot(profileFolder, "2", "Alpha Save", plain);
    WriteSaveSlot(profileFolder, "autosave", "Autosave", plain);
    WriteSaveSlot(profileFolder, "autosave_drive", "Autosave Drive", plain);
    var listed = service.ListSlots(profile);
    EqualSequence(["Alpha Save", "Zulu Save", "Autosave", "Autosave Drive"], listed.Select(slot => slot.DisplayName));
    EqualSequence(["2", "1", "autosave", "autosave_drive"], listed.Select(slot => slot.SlotId));
    var cloudFolder = Path.Combine(root, "profiles", "cloud-save-contract");
    WriteSaveSlot(cloudFolder, "1", "Remote Save", plain);
    var cloud = new ProfileRef("cloud-save-contract", "cloud", cloudFolder, Path.Combine(cloudFolder, "profile.sii"), "Cloud", "", 0);
    if (service.ListSlots(cloud).Count != 0) throw new InvalidOperationException("Cloud saves must not be listed.");

    var slot = listed.First(slot => slot.SlotId == "1");
    var snapshot = service.ReadSnapshot(slot);
    if (!snapshot.Fields.Any(x => x.FieldName == "money_account" && x.Value == "1253729")) throw new InvalidOperationException("BSII money read contract failed.");
    if (!snapshot.Fields.Any(x => x.FieldName == "experience_points" && x.Value == "279375")) throw new InvalidOperationException("BSII experience read contract failed.");
    if (!service.SetMoney(slot, 2000000).Success || !service.SetExperience(slot, 6000).Success) throw new InvalidOperationException("BSII mutation contract failed.");
    var after = service.ReadSnapshot(slot);
    if (!after.Fields.Any(x => x.FieldName == "money_account" && x.Value == "2000000")) throw new InvalidOperationException("BSII money write verification failed.");
    if (!after.Fields.Any(x => x.FieldName == "experience_points" && x.Value == "6000")) throw new InvalidOperationException("BSII experience write verification failed.");
    var levelResult = service.SetLevel(slot, 5);
    if (!levelResult.Success) throw new InvalidOperationException($"BSII level mutation contract failed: {levelResult.Message}");
    var level = service.ReadSnapshot(slot);
    if (!level.Fields.Any(x => x.FieldName == "experience_points" && x.Value == "10000")) throw new InvalidOperationException("BSII level value verification failed.");
    var garageResult = service.UnlockAllGarages(slot);
    if (!garageResult.Success) throw new InvalidOperationException($"BSII garage unlock contract failed: {garageResult.Message}");
    var garageAgain = service.UnlockAllGarages(slot);
    if (garageAgain.Success || !garageAgain.Message.Contains("already enabled", StringComparison.OrdinalIgnoreCase)) throw new InvalidOperationException("BSII garage idempotence contract failed.");
    var dealerResult = service.UnlockAllDealers(slot);
    if (dealerResult.Success || !dealerResult.Message.Contains("No compatible boolean field", StringComparison.Ordinal)) throw new InvalidOperationException("BSII dealer capability boundary contract failed.");
    await Task.CompletedTask;

    static void WriteSaveSlot(string profileRoot, string slotId, string name, byte[] payload)
    {
        var folder = Path.Combine(profileRoot, "save", slotId);
        Directory.CreateDirectory(folder);
        File.WriteAllBytes(Path.Combine(folder, "game.sii"), ScsCCodec.Encrypt(payload));
        File.WriteAllText(
            Path.Combine(folder, "info.sii"),
            $"SiiNunit\n{{\n save_info : .save {{\n  name: \"{EncodeSii(name)}\"\n }}\n}}\n",
            Encoding.UTF8);
    }

    static string EncodeSii(string value)
    {
        var bytes = Encoding.UTF8.GetBytes(value);
        return string.Concat(bytes.Select(value => $"\\x{value:X2}"));
    }
}

static byte[] BuildCanonicalBsii(uint experience, long money)
{
    static byte[] String(string value)
    {
        var bytes = System.Text.Encoding.UTF8.GetBytes(value);
        return BitConverter.GetBytes(bytes.Length).Concat(bytes).ToArray();
    }
    static byte[] Encoded(string value)
    {
        const string table = "0123456789abcdefghijklmnopqrstuvwxyz_";
        ulong number = 0;
        for (var index = 0; index < value.Length; index++) number += (ulong)(table.IndexOf(value[index]) + 1) * (ulong)Math.Pow(38, index);
        return BitConverter.GetBytes(number);
    }
    static byte[] Id(string value) => new byte[] { 1 }.Concat(Encoded(value)).ToArray();
    static byte[] Definition(uint id, string name, params (string Field, uint Type)[] fields)
    {
        var output = new List<byte>(BitConverter.GetBytes(0u).Concat(new byte[] { 1 }).Concat(BitConverter.GetBytes(id)).Concat(String(name)));
        foreach (var field in fields) output.AddRange(BitConverter.GetBytes(field.Type).Concat(String(field.Field)));
        output.AddRange(BitConverter.GetBytes(0u));
        return output.ToArray();
    }
    var data = new List<byte>("BSII"u8.ToArray().Concat(BitConverter.GetBytes(3u)));
    data.AddRange(Definition(1, "economy", ("bank", 0x39), ("experience_points", 0x27), ("garages", 0x35)));
    data.AddRange(Definition(2, "bank", ("money_account", 0x31)));
    data.AddRange(BitConverter.GetBytes(1u)); data.AddRange(Id("economy")); data.AddRange(Id("bank")); data.AddRange(BitConverter.GetBytes(experience)); data.Add(0);
    data.AddRange(BitConverter.GetBytes(2u)); data.AddRange(Id("bank")); data.AddRange(BitConverter.GetBytes(money));
    return data.ToArray();
}

static byte[] CreateScsC(string text)
{
    var key = new byte[] { 0x2A, 0x5F, 0xCB, 0x17, 0x91, 0xD2, 0x2F, 0xB6, 0x02, 0x45, 0xB3, 0xD8, 0x36, 0x9E, 0xD0, 0xB2, 0xC2, 0x73, 0x71, 0x56, 0x3F, 0xBF, 0x1F, 0x3C, 0x9E, 0xDF, 0x6B, 0x11, 0x82, 0x5A, 0x5D, 0x0A };
    var plain = System.Text.Encoding.UTF8.GetBytes(text);
    using var compressed = new MemoryStream();
    using (var zlib = new ZLibStream(compressed, CompressionLevel.SmallestSize, leaveOpen: true)) zlib.Write(plain);
    var iv = Enumerable.Range(1, 16).Select(i => (byte)i).ToArray();
    using var aes = Aes.Create(); aes.Key = key; aes.IV = iv; aes.Mode = CipherMode.CBC; aes.Padding = PaddingMode.PKCS7;
    var encrypted = aes.CreateEncryptor().TransformFinalBlock(compressed.ToArray(), 0, (int)compressed.Length);
    using var output = new MemoryStream(); output.Write("ScsC"u8); output.Write(new byte[32]); output.Write(iv); output.Write(BitConverter.GetBytes(plain.Length)); output.Write(encrypted); return output.ToArray();
}

file sealed record SessionContract(string ProfileId, int SelectedIndex);

file sealed class StubHttpHandler(Func<HttpRequestMessage, HttpResponseMessage> responder) : HttpMessageHandler
{
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) => Task.FromResult(responder(request));
}

file sealed class ImmediateProgress<T>(Action<T> callback) : IProgress<T>
{
    public void Report(T value) => callback(value);
}

file sealed class StubArchiveService(
    string manifest,
    Action<string, IReadOnlyList<string>>? treeWriter = null) : IExternalArchiveService
{
    public ExtractorAvailability Availability => new(false, false, false);
    public Task<ExtractionResult> ExtractManifestAsync(string packagePath, CancellationToken cancellationToken) => Task.FromResult(new ExtractionResult(true, manifest, Encoding.UTF8.GetBytes(manifest), "stub"));
    public Task<ExtractionResult> ExtractFileAsync(string packagePath, string entryName, CancellationToken cancellationToken) => ExtractManifestAsync(packagePath, cancellationToken);
    public Task<ExtractionResult> ExtractTreeAsync(string packagePath, string destinationDirectory, IReadOnlyList<string> roots, CancellationToken cancellationToken)
    {
        if (treeWriter is null) return Task.FromResult(new ExtractionResult(false, null, null, "stub"));
        Directory.CreateDirectory(destinationDirectory);
        treeWriter(destinationDirectory, roots);
        return Task.FromResult(new ExtractionResult(true, null, null, "stub tree"));
    }
}
