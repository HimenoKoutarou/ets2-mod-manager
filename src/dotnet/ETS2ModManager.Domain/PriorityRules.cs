namespace ETS2ModManager.Domain;

public sealed record WorklistEntry(
    string PackageName,
    bool Enabled,
    int Order,
    int? PriorityIndex);

public static class PriorityRules
{
    public static IReadOnlyList<string> ProfileToUiOrder(IEnumerable<string> entries) =>
        entries.Reverse().ToArray();

    public static IReadOnlyList<string> UiToProfileOrder(IEnumerable<string> entries) =>
        entries.Reverse().ToArray();

    public static IReadOnlyList<WorklistEntry> BuildWorklist(
        IEnumerable<string> profileActiveMods,
        IEnumerable<string> allPackageNames)
    {
        var rows = new List<WorklistEntry>();
        var represented = new HashSet<string>(StringComparer.Ordinal);
        var order = 0;
        foreach (var package in ProfileToUiOrder(profileActiveMods))
        {
            var key = ModIdentity.CanonicalKey(package);
            if (key.Length > 0 && represented.Add(key))
            {
                rows.Add(new WorklistEntry(package.Trim(), true, order, order));
                order++;
            }
        }
        foreach (var package in allPackageNames.Where(x => !string.IsNullOrWhiteSpace(x))
                     .Select(x => x.Trim()).Distinct(StringComparer.Ordinal).Order(StringComparer.Ordinal))
        {
            var key = ModIdentity.CanonicalKey(package);
            if (key.Length > 0 && represented.Add(key))
            {
                rows.Add(new WorklistEntry(package, false, -1, null));
            }
        }
        return rows;
    }

    public static IReadOnlyList<string> WorklistToProfileActive(IEnumerable<WorklistEntry> rows) =>
        UiToProfileOrder(rows.Where(row => row.Enabled).Select(row => row.PackageName));
}
