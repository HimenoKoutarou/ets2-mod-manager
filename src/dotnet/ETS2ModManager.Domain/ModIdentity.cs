using System.Globalization;
using System.Text.RegularExpressions;

namespace ETS2ModManager.Domain;

public static partial class ModIdentity
{
    [GeneratedRegex("_(workshop|copy\\d*|local)$", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant)]
    private static partial Regex StorageSuffix();

    [GeneratedRegex("^mod_workshop_package\\.0*([0-9a-f]{1,8})$", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant)]
    private static partial Regex LegacyWorkshop();

    public static string PackagePart(string? value) =>
        (value ?? string.Empty).Split('|', 2)[0].Trim();

    public static string CanonicalKey(string? value) =>
        StorageSuffix().Replace(PackagePart(value), string.Empty).ToLowerInvariant();

    public static string LegacyWorkshopId(string? value)
    {
        var match = LegacyWorkshop().Match(PackagePart(value));
        if (!match.Success || !uint.TryParse(match.Groups[1].Value, NumberStyles.HexNumber,
                CultureInfo.InvariantCulture, out var id))
        {
            return string.Empty;
        }
        return id.ToString(CultureInfo.InvariantCulture);
    }

    public static IReadOnlyList<string> ProfileEntryAliases(string? value)
    {
        var raw = (value ?? string.Empty).Trim();
        var aliases = new HashSet<string>(StringComparer.Ordinal);
        if (raw.Length == 0)
        {
            return [];
        }
        aliases.Add(raw.ToLowerInvariant());
        var package = PackagePart(raw).ToLowerInvariant();
        aliases.Add(package);
        aliases.Add(StorageSuffix().Replace(package, string.Empty));
        var workshopId = LegacyWorkshopId(package);
        if (workshopId.Length > 0)
        {
            aliases.Add(workshopId);
        }
        var separator = raw.IndexOf('|');
        if (separator >= 0 && separator + 1 < raw.Length)
        {
            aliases.Add(raw[(separator + 1)..].Trim().ToLowerInvariant());
        }
        return aliases.Order(StringComparer.Ordinal).ToArray();
    }
}
