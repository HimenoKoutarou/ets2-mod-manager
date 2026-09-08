using System.Text.Json;
using ETS2ModManager.Contracts;
using ETS2ModManager.Domain;

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

Console.WriteLine("Migration contract v1 passed.");
return 0;

static string[] Strings(JsonElement element) => element.EnumerateArray()
    .Select(value => value.GetString() ?? string.Empty).ToArray();

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
