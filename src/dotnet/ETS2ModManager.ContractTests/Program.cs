using System.Text.Json;
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
    Equal(item.GetProperty("canonical_key").GetString(), ModIdentity.CanonicalKey(input));
    Equal(item.GetProperty("legacy_workshop_id").GetString(), ModIdentity.LegacyWorkshopId(input));
    var expectedAliases = item.GetProperty("aliases").EnumerateArray()
        .Select(value => value.GetString() ?? string.Empty).ToArray();
    Equal(expectedAliases, ModIdentity.ProfileEntryAliases(input));
}

var priority = root.GetProperty("priority_case");
var active = Strings(priority.GetProperty("profile_active_mods"));
var packages = Strings(priority.GetProperty("all_package_names"));
var worklist = PriorityRules.BuildWorklist(active, packages);
Equal(Strings(priority.GetProperty("ui_active_mods")),
    worklist.Where(row => row.Enabled).Select(row => row.PackageName));
Equal(Strings(priority.GetProperty("roundtrip_profile_active_mods")),
    PriorityRules.WorklistToProfileActive(worklist));

Console.WriteLine("Migration contract v1 passed.");
return 0;

static string[] Strings(JsonElement element) => element.EnumerateArray()
    .Select(value => value.GetString() ?? string.Empty).ToArray();

static void Equal(string? expected, string? actual)
{
    if (!string.Equals(expected, actual, StringComparison.Ordinal))
    {
        throw new InvalidOperationException($"Expected '{expected}', got '{actual}'.");
    }
}

static void Equal(IEnumerable<string> expected, IEnumerable<string> actual)
{
    if (!expected.SequenceEqual(actual, StringComparer.Ordinal))
    {
        throw new InvalidOperationException(
            $"Expected [{string.Join(',', expected)}], got [{string.Join(',', actual)}].");
    }
}
