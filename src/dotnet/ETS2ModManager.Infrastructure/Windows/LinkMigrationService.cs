using System.Diagnostics;
using System.Runtime.InteropServices;
using ETS2ModManager.Application;
using ETS2ModManager.Contracts;

namespace ETS2ModManager.Infrastructure.Windows;

public sealed class LinkMigrationService : ILinkMigrationService
{
    private static LinkMigrationResult Fail(string operation, string source, string target, string message) => new(false, operation, source, target, message);
    public LinkMigrationResult Migrate(string sourceDirectory, string targetDirectory)
    {
        var source = Normalize(sourceDirectory);
        var target = Normalize(targetDirectory);
        if (!Directory.Exists(source)) return Fail("migrate", source, target, "Source directory does not exist.");
        if (IsReparsePoint(source)) return Fail("migrate", source, target, "Source directory is already a link.");
        if (string.Equals(source, target, StringComparison.OrdinalIgnoreCase)
            || IsDescendant(target, source) || IsDescendant(source, target))
            return Fail("migrate", source, target, "Source and target directories cannot contain one another.");
        if (Directory.Exists(target) && Directory.EnumerateFileSystemEntries(target).Any()) return Fail("migrate", source, target, "Target directory must be empty or absent.");

        var staging = target + ".staging-" + Guid.NewGuid().ToString("N");
        var sourceBackup = source + ".source-" + Guid.NewGuid().ToString("N");
        var targetCreated = false;
        var sourceMoved = false;
        var linkCreated = false;
        try
        {
            CopyDirectory(source, staging);
            Directory.Move(staging, target);
            targetCreated = true;
            Directory.Move(source, sourceBackup);
            sourceMoved = true;
            linkCreated = CreateJunction(target, source) || CreateSymlink(target, source);
            if (!linkCreated)
            {
                throw new IOException("Unable to create Junction or symbolic link.");
            }
            try { Directory.Delete(sourceBackup, true); }
            catch (Exception cleanupError)
            {
                return new LinkMigrationResult(true, "migrate", source, target,
                    $"Mod directory migrated and linked; temporary backup was retained at {sourceBackup}: {cleanupError.Message}");
            }
            return new LinkMigrationResult(true, "migrate", source, target, "Mod directory migrated and linked.");
        }
        catch (Exception error)
        {
            TryDeleteDirectory(staging);
            if (linkCreated && Directory.Exists(source) && IsReparsePoint(source))
            {
                try { RemoveLink(source); } catch { }
            }
            if (sourceMoved && Directory.Exists(sourceBackup) && !Directory.Exists(source))
            {
                try { Directory.Move(sourceBackup, source); } catch { }
            }
            if (targetCreated && Directory.Exists(target)) TryDeleteDirectory(target);
            var state = Directory.Exists(source) ? "source restored" : "source is missing; backup=" + sourceBackup;
            if (Directory.Exists(target)) state += "; target remains";
            return Fail("migrate", source, target, $"Migration failed: {error.Message} ({state}).");
        }
    }

    public LinkMigrationResult Restore(string sourceDirectory, string targetDirectory)
    {
        var source = Normalize(sourceDirectory);
        var target = Normalize(targetDirectory);
        if (string.Equals(source, target, StringComparison.OrdinalIgnoreCase)
            || IsDescendant(target, source) || IsDescendant(source, target))
            return Fail("restore", source, target, "Source and target directories cannot contain one another.");
        if (!Directory.Exists(source) || !IsReparsePoint(source)) return Fail("restore", source, target, "Source is not a Junction or symbolic link.");
        if (!Directory.Exists(target)) return Fail("restore", source, target, "Target directory does not exist.");
        var staging = source + ".restore-" + Guid.NewGuid().ToString("N");
        var removed = false;
        try
        {
            CopyDirectory(target, staging);
            RemoveLink(source);
            removed = true;
            Directory.Move(staging, source);
            return new LinkMigrationResult(true, "restore", source, target, "Mod directory restored to its original path.");
        }
        catch (Exception error)
        {
            TryDeleteDirectory(staging);
            if (removed && !Directory.Exists(source))
            {
                try
                {
                    if (!CreateJunction(target, source) && !CreateSymlink(target, source))
                        return Fail("restore", source, target, $"Restore failed and link recreation failed: {error.Message}");
                }
                catch (Exception recreateError)
                {
                    return Fail("restore", source, target, $"Restore failed; link recreation failed: {recreateError.Message}");
                }
            }
            return Fail("restore", source, target, $"Restore failed: {error.Message}");
        }
    }

    private static bool CreateJunction(string target, string link)
    {
        if (!OperatingSystem.IsWindows()) return false;
        var result = Run("cmd.exe", $"/c mklink /J \"{link}\" \"{target}\"");
        return result == 0 && Directory.Exists(link);
    }

    private static bool CreateSymlink(string target, string link)
    {
        try
        {
            Directory.CreateSymbolicLink(link, target);
            return Directory.Exists(link);
        }
        catch (IOException) { return false; }
        catch (UnauthorizedAccessException) { return false; }
    }

    private static void RemoveLink(string link)
    {
        try { Directory.Delete(link, false); }
        catch (IOException) { Run("cmd.exe", $"/c rmdir \"{link}\""); }
    }

    private static bool IsReparsePoint(string path) => (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0;

    private static void CopyDirectory(string source, string target)
    {
        Directory.CreateDirectory(target);
        foreach (var path in Directory.EnumerateFileSystemEntries(source))
        {
            if (IsReparsePoint(path)) throw new IOException($"Nested reparse point is not supported: {path}");
            var destination = Path.Combine(target, Path.GetFileName(path));
            if (Directory.Exists(path)) CopyDirectory(path, destination);
            else File.Copy(path, destination, true);
        }
    }

    private static string Normalize(string value)
    {
        var full = Path.GetFullPath(value);
        return Path.TrimEndingDirectorySeparator(full);
    }

    private static bool IsDescendant(string child, string parent) =>
        child.StartsWith(parent + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);
    private static void TryDeleteDirectory(string path) { try { if (Directory.Exists(path)) Directory.Delete(path, true); } catch { } }
    private static int Run(string fileName, string arguments)
    {
        using var process = Process.Start(new ProcessStartInfo { FileName = fileName, Arguments = arguments, UseShellExecute = false, CreateNoWindow = true, WindowStyle = ProcessWindowStyle.Hidden });
        if (process is null) return -1;
        process.WaitForExit(15_000);
        if (process.HasExited) return process.ExitCode;
        try { process.Kill(entireProcessTree: true); } catch { }
        return -1;
    }
}
