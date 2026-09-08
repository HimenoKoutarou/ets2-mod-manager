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

    public static IReadOnlyList<WorklistEntry> BatchToggle(
        IEnumerable<WorklistEntry> entries,
        IEnumerable<int> indices,
        string action)
    {
        var rows = entries.ToList();
        var selected = indices.ToHashSet();
        for (var index = 0; index < rows.Count; index++)
        {
            if (!selected.Contains(index))
            {
                continue;
            }

            var enabled = action switch
            {
                "enable" => true,
                "disable" => false,
                _ => !rows[index].Enabled,
            };
            rows[index] = rows[index] with { Enabled = enabled };
        }
        return Renumber(rows.Where(row => row.Enabled).Concat(rows.Where(row => !row.Enabled)));
    }

    public static IReadOnlyList<WorklistEntry> MoveBottom(
        IEnumerable<WorklistEntry> entries,
        IEnumerable<int> indices)
    {
        var rows = entries.ToList();
        var selected = indices.ToHashSet();
        var enabledPositions = rows
            .Select((row, index) => (row, index))
            .Where(item => item.row.Enabled)
            .ToList();
        var enabledRows = enabledPositions.Select(item => item.row).ToList();
        var moving = enabledRows
            .Where((_, index) => selected.Contains(enabledPositions[index].index))
            .ToList();
        var remaining = enabledRows.Except(moving).ToList();
        var ordered = remaining.Concat(moving).ToList();
        var enabledCursor = 0;
        for (var index = 0; index < rows.Count; index++)
        {
            if (rows[index].Enabled)
            {
                rows[index] = ordered[enabledCursor++];
            }
        }
        return Renumber(rows);
    }

    private static IReadOnlyList<WorklistEntry> Renumber(IEnumerable<WorklistEntry> rows)
    {
        var output = new List<WorklistEntry>();
        var order = 0;
        foreach (var row in rows)
        {
            output.Add(row.Enabled
                ? row with { Order = order, PriorityIndex = order++ }
                : row with { Order = -1, PriorityIndex = null });
        }
        return output;
    }
}
