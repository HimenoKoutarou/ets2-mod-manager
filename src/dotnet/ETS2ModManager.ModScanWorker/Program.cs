using ETS2ModManager.Infrastructure.Rust;
using ETS2ModManager.Infrastructure.Scanning;
using System.Text.Json;

if (args.Length == 0)
{
    Console.Error.WriteLine("Usage: ETS2ModManager.ModScanWorker --scan <local-mod-dir> [workshop-dir] | <package-path> [...]");
    return 2;
}

try
{
    if (string.Equals(args[0], "--scan", StringComparison.OrdinalIgnoreCase))
    {
        var scanner = new HybridModScanner(args.ElementAtOrDefault(1), args.ElementAtOrDefault(2));
        var result = await scanner.ScanAsync(null, CancellationToken.None);
        Console.WriteLine(JsonSerializer.Serialize(result));
        return result.Status == ETS2ModManager.Contracts.ScanStatus.Completed ? 0 : 1;
    }
    var core = new Ets2CoreClient();
    core.EnsureCompatible();
    Console.WriteLine(core.CountPackages(args));
    return 0;
}
catch (Exception error)
{
    Console.Error.WriteLine(error.Message);
    return 1;
}
