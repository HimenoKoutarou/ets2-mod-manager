using System.ComponentModel;
using System.Diagnostics;
using System.IO.Compression;
using System.Text;
using ETS2ModManager.Application;
using ETS2ModManager.Contracts;

namespace ETS2ModManager.Infrastructure.Archives;

public sealed class ExternalArchiveService : IExternalArchiveService
{
    private readonly string _tools;
    public ExternalArchiveService(string? toolsDirectory = null) => _tools = toolsDirectory ?? Path.Combine(AppContext.BaseDirectory, "assets", "tools");
    public ExtractorAvailability Availability
    {
        get
        {
            var legacy = File.Exists(Path.Combine(_tools, "extractor.exe"));
            var modern = File.Exists(Path.Combine(_tools, "extractor-2025-10-21.exe"));
            var sxc = File.Exists(Path.Combine(_tools, "sxc64.exe"));
            return new(legacy, modern, legacy || modern || sxc);
        }
    }

    public Task<ExtractionResult> ExtractManifestAsync(string packagePath, CancellationToken cancellationToken) => ExtractFileAsync(packagePath, "manifest.sii", cancellationToken);

    public async Task<ExtractionResult> ExtractTreeAsync(
        string packagePath,
        string destinationDirectory,
        IReadOnlyList<string> roots,
        CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(packagePath) || string.IsNullOrWhiteSpace(destinationDirectory))
            return new(false, null, null, "Package and destination are required.");
        var normalizedRoots = roots
            .Where(root => !string.IsNullOrWhiteSpace(root))
            .Select(NormalizeArchivePath)
            .Where(root => root.Length > 0)
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();
        if (normalizedRoots.Length == 0)
            return new(false, null, null, "At least one archive root is required.");

        try { Directory.CreateDirectory(destinationDirectory); }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        { return new(false, null, null, $"Destination could not be created: {error.Message}"); }

        cancellationToken.ThrowIfCancellationRequested();
        if (Directory.Exists(packagePath))
            return CopyDirectoryTree(packagePath, destinationDirectory, normalizedRoots, cancellationToken);
        if (!File.Exists(packagePath))
            return new(false, null, null, "Package was not found.");

        var copiedFromZip = TryCopyZipTree(
            packagePath, destinationDirectory, normalizedRoots, cancellationToken,
            out var zipMessage, out var readableZip);
        if (copiedFromZip || readableZip)
            return new(copiedFromZip, null, null, zipMessage);

        var magic = DetectMagic(packagePath);
        var extracted = magic switch
        {
            ArchiveMagic.ScsHashFs => await ExtractHashFsTreeAsync(packagePath, destinationDirectory, normalizedRoots, cancellationToken),
            ArchiveMagic.Aem or ArchiveMagic.Zip => await ExtractSxcTreeAsync(packagePath, destinationDirectory, normalizedRoots, cancellationToken),
            _ => new ExtractionResult(false, null, null, "Unsupported archive format.")
        };
        return extracted;
    }

    public async Task<ExtractionResult> ExtractFileAsync(string packagePath, string entryName, CancellationToken cancellationToken)
    {
        if (Directory.Exists(packagePath))
        {
            var file = Directory.EnumerateFiles(packagePath, Path.GetFileName(entryName), SearchOption.AllDirectories).FirstOrDefault();
            if (file is null) return new(false, null, null, "Entry was not found.");
            var bytes = await File.ReadAllBytesAsync(file, cancellationToken);
            return new(true, Encoding.UTF8.GetString(bytes), bytes, "Entry extracted.");
        }
        var readableZip = false;
        try
        {
            using var archive = ZipFile.OpenRead(packagePath);
            readableZip = true;
            var entry = archive.Entries.FirstOrDefault(x => string.Equals(x.FullName.Replace('\\', '/').TrimStart('/'), entryName.TrimStart('/'), StringComparison.OrdinalIgnoreCase) || string.Equals(x.Name, Path.GetFileName(entryName), StringComparison.OrdinalIgnoreCase));
            if (entry is not null)
            {
                await using var stream = entry.Open(); using var memory = new MemoryStream(); await stream.CopyToAsync(memory, cancellationToken); var bytes = memory.ToArray();
                return new(true, Encoding.UTF8.GetString(bytes), bytes, "Entry extracted.");
            }
        }
        catch (InvalidDataException) { }
        catch (IOException) { }
        if (readableZip) return new(false, null, null, "Entry was not found in the readable ZIP archive.");
        var temp = Path.Combine(Path.GetTempPath(), "ets2mm-extract-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(temp);
        try
        {
            var tree = await ExtractTreeAsync(packagePath, temp, [entryName], cancellationToken);
            if (!tree.Success) return tree;
            var normalizedEntry = NormalizeArchivePath(entryName);
            var extracted = Directory.EnumerateFiles(temp, "*", SearchOption.AllDirectories)
                .FirstOrDefault(file => string.Equals(
                    NormalizeArchivePath(Path.GetRelativePath(temp, file)), normalizedEntry,
                    StringComparison.OrdinalIgnoreCase)
                    || string.Equals(Path.GetFileName(file), Path.GetFileName(entryName), StringComparison.OrdinalIgnoreCase));
            if (extracted is null) return new(false, null, null, "Extractor did not produce the requested entry.");
            var bytes = await File.ReadAllBytesAsync(extracted, cancellationToken); return new(true, Encoding.UTF8.GetString(bytes), bytes, "Entry extracted by external tool.");
        }
        finally { try { Directory.Delete(temp, true); } catch { } }
    }

    private static ExtractionResult CopyDirectoryTree(
        string sourceDirectory,
        string destinationDirectory,
        IReadOnlyList<string> roots,
        CancellationToken cancellationToken)
    {
        var copied = 0;
        try
        {
            foreach (var file in Directory.EnumerateFiles(sourceDirectory, "*", SearchOption.AllDirectories))
            {
                cancellationToken.ThrowIfCancellationRequested();
                var relative = NormalizeArchivePath(Path.GetRelativePath(sourceDirectory, file));
                if (!MatchesRoot(relative, roots)) continue;
                var destination = Path.Combine(destinationDirectory, relative.Replace('/', Path.DirectorySeparatorChar));
                var parent = Path.GetDirectoryName(destination);
                if (!string.IsNullOrWhiteSpace(parent)) Directory.CreateDirectory(parent);
                File.Copy(file, destination, true);
                copied++;
            }
        }
        catch (OperationCanceledException) { throw; }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        { return new(false, null, null, $"Directory extraction failed: {error.Message}"); }
        return copied > 0
            ? new(true, null, null, $"Copied {copied} archive files.")
            : new(false, null, null, "No requested files were found.");
    }

    private static bool TryCopyZipTree(
        string packagePath,
        string destinationDirectory,
        IReadOnlyList<string> roots,
        CancellationToken cancellationToken,
        out string message,
        out bool readableZip)
    {
        var copied = 0;
        readableZip = false;
        try
        {
            using var archive = ZipFile.OpenRead(packagePath);
            readableZip = true;
            foreach (var entry in archive.Entries)
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (string.IsNullOrEmpty(entry.Name)) continue;
                var relative = NormalizeArchivePath(entry.FullName);
                if (!MatchesRoot(relative, roots)) continue;
                var destination = Path.Combine(destinationDirectory, relative.Replace('/', Path.DirectorySeparatorChar));
                var parent = Path.GetDirectoryName(destination);
                if (!string.IsNullOrWhiteSpace(parent)) Directory.CreateDirectory(parent);
                using var input = entry.Open();
                using var output = new FileStream(destination, FileMode.Create, FileAccess.Write, FileShare.None);
                input.CopyTo(output);
                copied++;
            }
            message = copied > 0 ? $"Extracted {copied} archive files." : "No requested files were found.";
            return copied > 0;
        }
        catch (OperationCanceledException) { throw; }
        catch (InvalidDataException) { message = "The package is not a readable ZIP archive."; return false; }
        catch (IOException error) { message = $"ZIP extraction failed: {error.Message}"; return false; }
    }

    private async Task<ExtractionResult> ExtractHashFsTreeAsync(
        string packagePath,
        string destinationDirectory,
        IReadOnlyList<string> roots,
        CancellationToken cancellationToken)
    {
        var tools = new[] { "extractor-2025-10-21.exe", "extractor.exe" }
            .Select(name => Path.Combine(_tools, name)).Where(File.Exists).ToArray();
        if (tools.Length == 0) return new(false, null, null, "No HashFS extractor is installed.");
        var partial = string.Join(',', roots.Select(root => "/" + root));
        foreach (var tool in tools)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var result = await RunToolAsync(tool, [packagePath, "--deep", $"--partial={partial}", "-d", destinationDirectory, "-s"], cancellationToken);
            if (result.Success && HasFiles(destinationDirectory))
                return new(true, null, null, $"Extracted localization tree with {Path.GetFileName(tool)}.");
            if (HasFiles(destinationDirectory))
                return new(true, null, null, $"Extractor produced files with {Path.GetFileName(tool)}.");
        }
        return new(false, null, null, "HashFS extractor did not produce the requested files.");
    }

    private async Task<ExtractionResult> ExtractSxcTreeAsync(
        string packagePath,
        string destinationDirectory,
        IReadOnlyList<string> roots,
        CancellationToken cancellationToken)
    {
        var tool = Path.Combine(_tools, "sxc64.exe");
        if (!File.Exists(tool)) return new(false, null, null, "SXC extractor is not installed.");
        foreach (var root in roots)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var result = await RunToolAsync(tool, [packagePath, "-o", destinationDirectory, "-f", "/" + root, "-q"], cancellationToken);
            if (!result.Success && !HasFiles(destinationDirectory)) continue;
        }
        return HasFiles(destinationDirectory)
            ? new(true, null, null, "Extracted localization tree with sxc64.exe.")
            : new(false, null, null, "SXC extractor did not produce the requested files.");
    }

    private static async Task<(bool Success, int ExitCode)> RunToolAsync(
        string executable,
        IReadOnlyList<string> arguments,
        CancellationToken cancellationToken)
    {
        using var process = new Process();
        process.StartInfo = new ProcessStartInfo
        {
            FileName = executable,
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        foreach (var argument in arguments) process.StartInfo.ArgumentList.Add(argument);
        try
        {
            if (!process.Start()) return (false, -1);
            var stdout = process.StandardOutput.ReadToEndAsync();
            var stderr = process.StandardError.ReadToEndAsync();
            try
            {
                await process.WaitForExitAsync(cancellationToken);
                await Task.WhenAll(stdout, stderr);
            }
            catch (OperationCanceledException)
            {
                try { if (!process.HasExited) process.Kill(entireProcessTree: true); } catch { }
                try { await process.WaitForExitAsync(); } catch { }
                try { await Task.WhenAll(stdout, stderr); } catch { }
                throw;
            }
            return (process.ExitCode == 0, process.ExitCode);
        }
        catch (Win32Exception) { return (false, -1); }
        catch (InvalidOperationException) { return (false, -1); }
    }

    private enum ArchiveMagic { Unknown, Zip, ScsHashFs, Aem }

    private static ArchiveMagic DetectMagic(string path)
    {
        try
        {
            Span<byte> header = stackalloc byte[4];
            using var stream = File.OpenRead(path);
            if (stream.Read(header) < 4) return ArchiveMagic.Unknown;
            if (header.SequenceEqual("SCS#"u8)) return ArchiveMagic.ScsHashFs;
            if (header.SequenceEqual("AEM!"u8)) return ArchiveMagic.Aem;
            if (header[0] == (byte)'P' && header[1] == (byte)'K') return ArchiveMagic.Zip;
        }
        catch (IOException) { }
        return ArchiveMagic.Unknown;
    }

    private static bool HasFiles(string directory)
    {
        try { return Directory.EnumerateFiles(directory, "*", SearchOption.AllDirectories).Any(); }
        catch (IOException) { return false; }
        catch (UnauthorizedAccessException) { return false; }
    }

    private static bool MatchesRoot(string path, IReadOnlyList<string> roots) =>
        roots.Any(root =>
        {
            // A filename such as manifest.sii is used by ExtractFileAsync and
            // may live below a package directory. Directory roots (def,
            // locale/zh_cn, ...) remain path-aware and recursive.
            if (!root.Contains('/', StringComparison.Ordinal)
                && Path.GetExtension(root).Length > 0)
                return string.Equals(path, root, StringComparison.OrdinalIgnoreCase)
                    || string.Equals(Path.GetFileName(path), root, StringComparison.OrdinalIgnoreCase);
            return string.Equals(path, root, StringComparison.OrdinalIgnoreCase)
                || path.StartsWith(root + "/", StringComparison.OrdinalIgnoreCase);
        });

    private static string NormalizeArchivePath(string value)
    {
        var normalized = (value ?? string.Empty).Replace('\\', '/').Trim().TrimStart('/');
        while (normalized.StartsWith("./", StringComparison.Ordinal)) normalized = normalized[2..];
        return normalized;
    }
}
