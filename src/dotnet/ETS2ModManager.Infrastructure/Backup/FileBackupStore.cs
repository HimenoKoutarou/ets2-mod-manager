using System.IO.Compression;
using ETS2ModManager.Application;

namespace ETS2ModManager.Infrastructure.Backup;

public sealed class FileBackupStore : IBackupStore
{
    private readonly string _backupRoot;
    private readonly int _maxBackups;

    public FileBackupStore(string? backupRoot = null, int maxBackups = 10)
    {
        _backupRoot = backupRoot ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ETS2ModManager", "backups");
        _maxBackups = Math.Max(1, maxBackups);
    }

    public string? Backup(string source, string tag)
    {
        if (string.IsNullOrWhiteSpace(source) || !File.Exists(source)) return null;
        Directory.CreateDirectory(_backupRoot);
        var safeTag = string.IsNullOrWhiteSpace(tag) ? "manual" : string.Join("_", tag.Split(Path.GetInvalidFileNameChars()));
        var file = Path.Combine(_backupRoot, $"{Path.GetFileNameWithoutExtension(source)}_{DateTime.UtcNow:yyyyMMdd-HHmmssfff}_{safeTag}.zip");
        var temp = file + ".tmp";
        try
        {
            using (var archive = ZipFile.Open(temp, ZipArchiveMode.Create))
            {
                var entry = archive.CreateEntry(Path.GetFileName(source), CompressionLevel.Fastest);
                using var input = File.OpenRead(source);
                using var output = entry.Open();
                input.CopyTo(output);
            }
            File.Move(temp, file, true);
            Prune(Path.GetFileNameWithoutExtension(source));
            return file;
        }
        catch
        {
            try { if (File.Exists(temp)) File.Delete(temp); } catch { }
            throw;
        }
    }

    private void Prune(string sourceStem)
    {
        var prefix = sourceStem + "_";
        var files = Directory.EnumerateFiles(_backupRoot, prefix + "*.zip")
            .OrderByDescending(File.GetLastWriteTimeUtc)
            .ToArray();
        foreach (var old in files.Skip(_maxBackups))
        {
            try { File.Delete(old); } catch { }
        }
    }
}
