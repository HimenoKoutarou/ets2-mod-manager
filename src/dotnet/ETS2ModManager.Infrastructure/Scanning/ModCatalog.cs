using ETS2ModManager.Contracts;
using ETS2ModManager.Domain;

namespace ETS2ModManager.Infrastructure.Scanning;

/// <summary>Applies local-first identity rules to scanner and cache results.</summary>
internal static class ModCatalog
{
    public static IReadOnlyList<ModRecord> Deduplicate(IEnumerable<ModRecord> records)
    {
        return records
            .Where(record => !string.IsNullOrWhiteSpace(record.PackagePath))
            .GroupBy(IdentityKey, StringComparer.Ordinal)
            .Select(group => group
                .OrderBy(record => IsWorkshop(record) ? 1 : 0)
                .ThenBy(record => record.DisplayName, StringComparer.OrdinalIgnoreCase)
                .ThenBy(record => record.PackagePath, StringComparer.OrdinalIgnoreCase)
                .First())
            .OrderBy(record => record.DisplayName, StringComparer.OrdinalIgnoreCase)
            .ThenBy(record => record.PackagePath, StringComparer.OrdinalIgnoreCase)
            .ToArray();
    }

    private static string IdentityKey(ModRecord record)
    {
        var package = ModIdentity.PackagePart(record.PackageName);
        var legacy = ModIdentity.LegacyWorkshopId(package);
        if (legacy.Length > 0) return "workshop:" + legacy;
        if (IsWorkshop(record) && package.Length > 0 && package.All(char.IsDigit)) return "workshop:" + package;
        return ModIdentity.CanonicalKey(package);
    }

    private static bool IsWorkshop(ModRecord record) =>
        string.Equals(record.PackageType, "workshop", StringComparison.OrdinalIgnoreCase)
        || record.PackagePath.Contains("workshop", StringComparison.OrdinalIgnoreCase);
}
