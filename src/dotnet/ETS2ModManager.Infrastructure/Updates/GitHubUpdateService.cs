using System.Net;
using System.Net.Http.Headers;
using System.Diagnostics;
using System.IO.Compression;
using System.Text;
using System.Text.Json;
using ETS2ModManager.Application;
using ETS2ModManager.Contracts;

namespace ETS2ModManager.Infrastructure.Updates;

public sealed class GitHubUpdateService : IUpdateService
{
    private readonly HttpClient _http;

    public GitHubUpdateService(HttpClient? httpClient = null)
    {
        _http = httpClient ?? new HttpClient();
        _http.DefaultRequestHeaders.UserAgent.Clear();
        _http.DefaultRequestHeaders.UserAgent.Add(new ProductInfoHeaderValue("ETS2ModManager", "1.0"));
        _http.DefaultRequestHeaders.Accept.Add(new MediaTypeWithQualityHeaderValue("application/vnd.github+json"));
    }

    public async Task<UpdateInfo> CheckAsync(string currentVersion, string repository, CancellationToken cancellationToken)
    {
        var repo = repository.Trim().Trim('/');
        if (repo.Contains("github.com/", StringComparison.OrdinalIgnoreCase)) repo = repo[(repo.IndexOf("github.com/", StringComparison.OrdinalIgnoreCase) + 11)..];
        repo = repo.TrimEnd('/');
        if (repo.EndsWith(".git", StringComparison.OrdinalIgnoreCase)) repo = repo[..^4];
        if (repo.Count(c => c == '/') != 1) return new UpdateInfo(false, currentVersion, null, null, "Repository must be in owner/name format.");
        try
        {
            using var response = await _http.GetAsync($"https://api.github.com/repos/{repo}/releases/latest", cancellationToken);
            if (response.StatusCode == HttpStatusCode.NotFound) return new UpdateInfo(false, currentVersion, null, null, "Release not found.");
            response.EnsureSuccessStatusCode();
            await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken);
            using var json = await JsonDocument.ParseAsync(stream, cancellationToken: cancellationToken);
            var root = json.RootElement;
            var latest = root.TryGetProperty("tag_name", out var tag) ? tag.GetString()?.TrimStart('v', 'V') : null;
            if (string.IsNullOrWhiteSpace(latest)) return new UpdateInfo(false, currentVersion, null, null, "Latest release has no version tag.");
            var assetUrl = root.TryGetProperty("assets", out var assets) ? assets.EnumerateArray()
                .Where(asset => asset.TryGetProperty("name", out var name) && name.GetString()?.EndsWith(".zip", StringComparison.OrdinalIgnoreCase) == true)
                .OrderByDescending(asset => asset.TryGetProperty("size", out var size) ? size.GetInt64() : 0)
                .Select(asset => asset.TryGetProperty("browser_download_url", out var url) ? url.GetString() : null).FirstOrDefault(url => !string.IsNullOrWhiteSpace(url)) : null;
            var available = CompareVersions(latest, currentVersion) > 0;
            return new UpdateInfo(available, currentVersion, latest, assetUrl, available ? $"Version {latest} is available." : "Current version is up to date.");
        }
        catch (OperationCanceledException) { throw; }
        catch (Exception error) { return new UpdateInfo(false, currentVersion, null, null, $"Update check failed: {error.Message}"); }
    }

    public async Task<UpdateInstallResult> DownloadAndInstallAsync(UpdateInfo update, string installDirectory, string executableName, CancellationToken cancellationToken)
    {
        if (!update.IsAvailable || string.IsNullOrWhiteSpace(update.DownloadUrl)) return new UpdateInstallResult(false, "No downloadable update is available.", null);
        if (string.IsNullOrWhiteSpace(executableName) || !string.Equals(Path.GetFileName(executableName), executableName, StringComparison.Ordinal))
            return new UpdateInstallResult(false, "Executable name must be a file name without directory components.", null);
        var root = Path.GetFullPath(installDirectory);
        if (!Directory.Exists(root)) return new UpdateInstallResult(false, "Install directory does not exist.", null);
        var tempRoot = Path.Combine(Path.GetTempPath(), "ets2mm-update-" + Guid.NewGuid().ToString("N"));
        var zip = Path.Combine(tempRoot, "release.zip");
        var staged = Path.Combine(tempRoot, "staged");
        try
        {
            Directory.CreateDirectory(staged);
            await using (var output = new FileStream(zip, FileMode.CreateNew, FileAccess.Write, FileShare.None, 64 * 1024, FileOptions.Asynchronous | FileOptions.SequentialScan))
            await using (var input = await _http.GetStreamAsync(update.DownloadUrl, cancellationToken))
            {
                await input.CopyToAsync(output, cancellationToken);
            }
            ZipFile.ExtractToDirectory(zip, staged);
            var payload = FindPayloadRoot(staged);
            if (!File.Exists(Path.Combine(payload, executableName))) return new UpdateInstallResult(false, "Downloaded archive does not contain the application executable.", staged);
            var script = Path.Combine(tempRoot, "apply-update.cmd");
            var pid = Environment.ProcessId;
            var lines = new[]
            {
                "@echo off",
                "setlocal",
                $"set \"ROOT={EscapeCmdValue(root)}\"",
                $"set \"PAYLOAD={EscapeCmdValue(payload)}\"",
                $"set \"EXE={EscapeCmdValue(executableName)}\"",
                $"set \"PID={pid}\"",
                ":wait",
                "tasklist /FI \"PID eq %PID%\" | find \"%PID%\" >nul",
                "if not errorlevel 1 (timeout /t 1 /nobreak >nul & goto wait)",
                "robocopy \"%PAYLOAD%\" \"%ROOT%\" /E /COPY:DAT /R:3 /W:1 >nul",
                "start \"\" \"%ROOT%\\%EXE%\"",
                "rmdir /s /q \"%~dp0\"",
                "endlocal"
            };
            File.WriteAllLines(script, lines, new UTF8Encoding(false));
            var startInfo = new ProcessStartInfo { FileName = "cmd.exe", UseShellExecute = false, CreateNoWindow = true, WindowStyle = ProcessWindowStyle.Hidden };
            startInfo.ArgumentList.Add("/d");
            startInfo.ArgumentList.Add("/c");
            startInfo.ArgumentList.Add(script);
            Process.Start(startInfo);
            return new UpdateInstallResult(true, "Update staged. The application will restart after it exits.", staged);
        }
        catch (OperationCanceledException)
        {
            try { Directory.Delete(tempRoot, true); } catch { }
            throw;
        }
        catch (Exception error) { try { Directory.Delete(tempRoot, true); } catch { } return new UpdateInstallResult(false, $"Update installation failed: {error.Message}", null); }
    }

    private static string FindPayloadRoot(string staging)
    {
        if (Directory.EnumerateFiles(staging, "*.exe", SearchOption.TopDirectoryOnly).Any()) return staging;
        var directories = Directory.EnumerateDirectories(staging).ToArray();
        return directories.Length == 1 ? directories[0] : staging;
    }

    private static string EscapeCmdValue(string value) => value
        .Replace("%", "%%", StringComparison.Ordinal)
        .Replace("^", "^^", StringComparison.Ordinal)
        .Replace("&", "^&", StringComparison.Ordinal)
        .Replace("|", "^|", StringComparison.Ordinal)
        .Replace("<", "^<", StringComparison.Ordinal)
        .Replace(">", "^>", StringComparison.Ordinal)
        .Replace("(", "^(", StringComparison.Ordinal)
        .Replace(")", "^)", StringComparison.Ordinal);

    private static int CompareVersions(string left, string right)
    {
        var a = ParseVersion(left); var b = ParseVersion(right); var length = Math.Max(a.Length, b.Length);
        for (var i = 0; i < length; i++)
        {
            var x = i < a.Length ? a[i] : 0; var y = i < b.Length ? b[i] : 0;
            if (x != y) return x.CompareTo(y);
        }
        return 0;
    }

    private static int[] ParseVersion(string value) => value.TrimStart('v', 'V').Split('.').Select(segment => int.TryParse(new string(segment.TakeWhile(char.IsDigit).ToArray()), out var n) ? n : 0).ToArray();
}
