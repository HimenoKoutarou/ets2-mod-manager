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

    public static IReadOnlyList<WorklistEntry> MoveUp(
        IEnumerable<WorklistEntry> entries,
        IEnumerable<int> indices,
        int steps = 1)
    {
        var rows = entries.ToList();
        var selected = indices.ToHashSet();
        var enabledPositions = rows
            .Select((row, index) => (row, index))
            .Where(item => item.row.Enabled)
            .Select((item, enabledIndex) => (item.row, item.index, enabledIndex))
            .ToList();
        var moving = enabledPositions.Where(item => selected.Contains(item.index)).Select(item => item.enabledIndex).ToHashSet();
        for (var step = 0; step < Math.Max(0, steps); step++)
        {
            for (var i = 1; i < enabledPositions.Count; i++)
            {
                if (moving.Contains(i) && !moving.Contains(i - 1))
                {
                    (enabledPositions[i - 1], enabledPositions[i]) = (enabledPositions[i], enabledPositions[i - 1]);
                    moving.Remove(i);
                    moving.Add(i - 1);
                }
            }
        }
        WriteEnabledRows(rows, enabledPositions.Select(item => item.row).ToList());
        return Renumber(rows);
    }

    public static IReadOnlyList<WorklistEntry> MoveDown(
        IEnumerable<WorklistEntry> entries,
        IEnumerable<int> indices,
        int steps = 1)
    {
        var rows = entries.ToList();
        var selected = indices.ToHashSet();
        var enabledPositions = rows
            .Select((row, index) => (row, index))
            .Where(item => item.row.Enabled)
            .Select((item, enabledIndex) => (item.row, item.index, enabledIndex))
            .ToList();
        var moving = enabledPositions.Where(item => selected.Contains(item.index)).Select(item => item.enabledIndex).ToHashSet();
        for (var step = 0; step < Math.Max(0, steps); step++)
        {
            for (var i = enabledPositions.Count - 2; i >= 0; i--)
            {
                if (moving.Contains(i) && !moving.Contains(i + 1))
                {
                    (enabledPositions[i], enabledPositions[i + 1]) = (enabledPositions[i + 1], enabledPositions[i]);
                    moving.Remove(i);
                    moving.Add(i + 1);
                }
            }
        }
        WriteEnabledRows(rows, enabledPositions.Select(item => item.row).ToList());
        return Renumber(rows);
    }

    public static IReadOnlyList<WorklistEntry> MoveTop(
        IEnumerable<WorklistEntry> entries,
        IEnumerable<int> indices)
    {
        var rows = entries.ToList();
        var selected = indices.ToHashSet();
        var enabled = rows
            .Select((row, index) => (row, index))
            .Where(item => item.row.Enabled)
            .ToList();
        var moving = enabled.Where(item => selected.Contains(item.index)).Select(item => item.row).ToList();
        var movingKeys = moving.ToHashSet();
        var remaining = enabled.Select(item => item.row).Where(row => !movingKeys.Contains(row)).ToList();
        WriteEnabledRows(rows, moving.Concat(remaining).ToList());
        return Renumber(rows);
    }

    private static void WriteEnabledRows(List<WorklistEntry> rows, IReadOnlyList<WorklistEntry> enabledRows)
    {
        var cursor = 0;
        for (var i = 0; i < rows.Count; i++)
        {
            if (rows[i].Enabled) rows[i] = enabledRows[cursor++];
        }
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
