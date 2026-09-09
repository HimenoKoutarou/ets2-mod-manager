using System.Diagnostics;
using Microsoft.Win32;
using ETS2ModManager.Application;
using ETS2ModManager.Contracts;

namespace ETS2ModManager.Infrastructure.Windows;

[System.Runtime.Versioning.SupportedOSPlatform("windows")]
public static class GameExecutableLocator
{
    public static string? Find()
    {
        var roots = new List<string>();
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(@"Software\Valve\Steam");
            if (key?.GetValue("SteamPath") is string steam && !string.IsNullOrWhiteSpace(steam)) roots.Add(steam);
        }
        catch { }
        foreach (var drive in new[] { "C:\\", "D:\\", "E:\\", "F:\\", "G:\\" })
        {
            roots.Add(Path.Combine(drive, "Program Files (x86)", "Steam"));
            roots.Add(Path.Combine(drive, "Steam"));
            roots.Add(Path.Combine(drive, "SteamLibrary"));
        }
        foreach (var root in roots.Distinct(StringComparer.OrdinalIgnoreCase))
        {
            var common = Path.Combine(root, "steamapps", "common");
            foreach (var name in new[] { "Euro Truck Simulator 2", "American Truck Simulator" })
            {
                var directory = Path.Combine(common, name);
                foreach (var exe in new[] { "eurotrucks2.exe", "amtrucks.exe" })
                {
                    var candidate = Path.Combine(directory, "bin", "win_x64", exe);
                    if (File.Exists(candidate)) return candidate;
                    candidate = Path.Combine(directory, "bin", "win_x86", exe);
                    if (File.Exists(candidate)) return candidate;
                }
            }
        }
        return null;
    }
}

public sealed class WindowsGameLauncherService : IGameLauncherService
{
    public async Task<GameLaunchResult> LaunchAndWaitAsync(string executable, string documentsDirectory, CancellationToken cancellationToken)
    {
        if (!File.Exists(executable)) return new GameLaunchResult(false, false, null, null, null, "Game executable was not found.");
        Directory.CreateDirectory(documentsDirectory);
        var crashPath = Path.Combine(documentsDirectory, "game.crash.txt");
        var logPath = Path.Combine(documentsDirectory, "game.log.txt");
        var before = File.Exists(crashPath) ? File.GetLastWriteTimeUtc(crashPath) : DateTime.MinValue;
        try
        {
            using var process = Process.Start(new ProcessStartInfo { FileName = executable, WorkingDirectory = Path.GetDirectoryName(executable), UseShellExecute = true });
            if (process is null) return new GameLaunchResult(false, false, null, null, null, "Unable to start the game process.");
            try { await process.WaitForExitAsync(cancellationToken); }
            catch (OperationCanceledException)
            {
                try { if (!process.HasExited) process.Kill(entireProcessTree: true); } catch { }
                throw;
            }
            await Task.Delay(500, cancellationToken);
            var hasCrash = File.Exists(crashPath) && File.GetLastWriteTimeUtc(crashPath) > before;
            return new GameLaunchResult(true, hasCrash, File.Exists(crashPath) ? crashPath : null, File.Exists(logPath) ? logPath : null, process.ExitCode, hasCrash ? "Game exited with a new crash report." : "Game exited.");
        }
        catch (OperationCanceledException) { throw; }
        catch (Exception error) { return new GameLaunchResult(false, false, null, null, null, $"Game launch failed: {error.Message}"); }
    }
}
