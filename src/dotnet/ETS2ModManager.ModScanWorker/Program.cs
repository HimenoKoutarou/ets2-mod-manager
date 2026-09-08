using ETS2ModManager.Infrastructure.Rust;

if (args.Length == 0)
{
    Console.Error.WriteLine("Usage: ETS2ModManager.ModScanWorker <package-path> [...]");
    return 2;
}

try
{
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
