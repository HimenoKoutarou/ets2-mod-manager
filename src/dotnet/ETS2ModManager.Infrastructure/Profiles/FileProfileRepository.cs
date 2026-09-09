using System.Text;
using System.Text.RegularExpressions;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Diagnostics;
using ETS2ModManager.Application;
using ETS2ModManager.Contracts;
using ETS2ModManager.Infrastructure.Saves;
using ETS2ModManager.Infrastructure.Windows;

namespace ETS2ModManager.Infrastructure.Profiles;

public sealed class FileProfileRepository : IProfileRepository
{
    private static readonly Regex ActiveEntry = new(@"^\s*active_mods\s*\[\d*\]\s*:\s*""(?<value>(?:\\.|[^""])*)""\s*$", RegexOptions.Compiled);
    private static readonly Regex LengthEntry = new(@"^(?<indent>\s*)active_mods\s*:\s*\d+\s*$", RegexOptions.Compiled);
    private static readonly Regex Field = new(@"^\s*(?<name>profile_name|company_name|save_name)\s*:\s*""(?<value>(?:\\.|[^""])*)""\s*$", RegexOptions.Compiled);
    private readonly string _profiles;
    private readonly string? _steamProfiles;
    private readonly string? _cloudProfiles;
    private readonly IBackupStore _backup;
    private readonly string? _decryptExecutable;
    private readonly IGameState _gameState;

    public FileProfileRepository(string profiles, string? steamProfiles, string? cloudProfiles, IBackupStore backup, string? decryptExecutable = null, IGameState? gameState = null)
    {
        _profiles = profiles; _steamProfiles = steamProfiles; _cloudProfiles = cloudProfiles; _backup = backup; _decryptExecutable = decryptExecutable;
        _gameState = gameState ?? new WindowsGameState();
    }

    public IReadOnlyList<ProfileRef> ListProfiles()
    {
        var result = new List<ProfileRef>();
        Add(result, "cloud", _cloudProfiles);
        Add(result, "steam", _steamProfiles);
        Add(result, "local", _profiles);
        return result;
    }

    private void Add(List<ProfileRef> result, string location, string? root)
    {
        if (string.IsNullOrWhiteSpace(root) || !Directory.Exists(root)) return;
        try
        {
            foreach (var folder in Directory.EnumerateDirectories(root).OrderBy(x => x, StringComparer.OrdinalIgnoreCase))
            {
                var sii = Path.Combine(folder, "profile.sii");
                if (!File.Exists(sii)) continue;
                var fields = ReadFields(sii);
                var active = ReadEntries(sii);
                var id = Path.GetFileName(folder);
                result.Add(new ProfileRef(id, location, folder, sii,
                    fields.GetValueOrDefault("profile_name", id), fields.GetValueOrDefault("company_name", ""), active.Count));
            }
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }

    public IReadOnlyList<string> ReadActiveMods(ProfileRef profile) => ReadEntries(profile.ProfileSii);

    public void ReplaceActiveMods(ProfileRef profile, IReadOnlyList<string> activeMods, bool verify)
    {
        EnsureWritable(profile, "modify Profile");
        var original = ReadBytesForWrite(profile.ProfileSii, out var source);
        if (!source.Contains("SiiNunit", StringComparison.Ordinal) || !source.Contains("profile", StringComparison.Ordinal))
            throw new InvalidDataException("The profile is encrypted or is not a plaintext SII file; refusing to overwrite it.");
        var lines = source.Replace("\r\n", "\n").Split('\n').ToList();
        var positions = lines.Select((line, index) => (line, index)).Where(x => ActiveEntry.IsMatch(x.line)).Select(x => x.index).ToList();
        var length = lines.FindIndex(line => LengthEntry.IsMatch(line));
        var indent = positions.Count == 0 ? "    " : Regex.Match(lines[positions[0]], @"^\s*").Value;
        var values = activeMods.Select(Escape).ToArray();
        if (length >= 0) lines[length] = $"{indent}active_mods: {values.Length}";
        else if (positions.Count > 0) { lines.Insert(positions[0], $"{indent}active_mods: {values.Length}"); positions = positions.Select(x => x + 1).ToList(); }
        if (positions.Count == 0 && values.Length > 0)
        {
            var at = lines.FindIndex(x => x.Trim() == "}"); if (at < 0) at = lines.Count;
            lines.InsertRange(at, values.Select((value, i) => $"{indent}active_mods[{i}]: \"{value}\""));
        }
        else
        {
            for (var i = 0; i < Math.Max(positions.Count, values.Length); i++)
            {
                if (i < positions.Count && i < values.Length) lines[positions[i]] = $"{indent}active_mods[{i}]: \"{values[i]}\"";
                else if (i < positions.Count) lines[positions[i]] = "";
                else lines.Insert(positions[^1] + 1 + i - positions.Count, $"{indent}active_mods[{i}]: \"{values[i]}\"");
            }
        }
        _backup.Backup(profile.ProfileSii, "pre-write");
        AtomicWrite(profile.ProfileSii, EncodeForWrite(original, string.Join("\n", lines)));
        if (verify && !ReadActiveMods(profile).SequenceEqual(activeMods, StringComparer.Ordinal)) throw new IOException("active_mods write verification failed");
    }

    public ProfileRef Copy(ProfileRef profile, string displayName, string companyName)
    {
        EnsureWritable(profile, "copy Profile");
        var parent = Path.GetDirectoryName(profile.Folder) ?? throw new IOException("Profile parent is missing");
        var id = profile.ProfileId;
        var n = 1; string target;
        do { target = Path.Combine(parent, $"{id[..Math.Min(24, id.Length)]}_copy{n++}"); } while (Directory.Exists(target));
        var staging = target + ".staging";
        _backup.Backup(profile.ProfileSii, "pre-copy");
        DirectoryCopy(profile.Folder, staging);
        try { Directory.Move(staging, target); } catch { try { Directory.Delete(staging, true); } catch { } throw; }
        var copied = Path.Combine(target, "profile.sii");
        if (!string.IsNullOrWhiteSpace(displayName) || !string.IsNullOrWhiteSpace(companyName))
        {
            var original = ReadBytesForWrite(copied, out var text);
            if (!string.IsNullOrWhiteSpace(displayName)) text = Regex.Replace(text, @"(?m)^(\s*profile_name\s*:\s*)"".*""$", match => $"{match.Groups[1].Value}\"{Escape(displayName)}\"");
            if (!string.IsNullOrWhiteSpace(companyName)) text = Regex.Replace(text, @"(?m)^(\s*company_name\s*:\s*)"".*""$", match => $"{match.Groups[1].Value}\"{Escape(companyName)}\"");
            AtomicWrite(copied, EncodeForWrite(original, text));
        }
        var fields = ReadFields(copied); return new ProfileRef(Path.GetFileName(target), profile.Location, target, copied,
            fields.GetValueOrDefault("profile_name", Path.GetFileName(target)), fields.GetValueOrDefault("company_name", ""), ReadEntries(copied).Count);
    }

    public ProfileRef Rename(ProfileRef profile, string displayName, string companyName)
    {
        EnsureWritable(profile, "rename Profile");
        var original = ReadBytesForWrite(profile.ProfileSii, out var text);
        if (!string.IsNullOrWhiteSpace(displayName))
            text = Regex.Replace(text, @"(?m)^(\s*profile_name\s*:\s*)""(?:\\.|[^""\\])*""\s*$", match => $"{match.Groups[1].Value}\"{Escape(displayName)}\"");
        if (!string.IsNullOrWhiteSpace(companyName))
            text = Regex.Replace(text, @"(?m)^(\s*company_name\s*:\s*)""(?:\\.|[^""\\])*""\s*$", match => $"{match.Groups[1].Value}\"{Escape(companyName)}\"");
        _backup.Backup(profile.ProfileSii, "pre-rename");
        AtomicWrite(profile.ProfileSii, EncodeForWrite(original, text));
        var fields = ReadFields(profile.ProfileSii);
        return new ProfileRef(profile.ProfileId, profile.Location, profile.Folder, profile.ProfileSii,
            fields.GetValueOrDefault("profile_name", profile.DisplayName), fields.GetValueOrDefault("company_name", profile.CompanyName), ReadEntries(profile.ProfileSii).Count);
    }

    public void CopySettings(ProfileRef source, ProfileRef destination, bool activeMods, bool controls)
    {
        EnsureWritable(destination, "copy Profile settings");
        var previousMods = activeMods ? ReadActiveMods(destination).ToArray() : null;
        var sourceMods = activeMods ? ReadActiveMods(source).ToArray() : null;
        var sourceControls = Path.Combine(source.Folder, "controls.sii");
        var destinationControls = Path.Combine(destination.Folder, "controls.sii");
        var copyControls = controls && File.Exists(sourceControls);
        var hadControls = File.Exists(destinationControls);
        var previousControls = hadControls ? File.ReadAllBytes(destinationControls) : null;
        try
        {
            if (copyControls)
            {
                if (hadControls) _backup.Backup(destinationControls, "pre-copy-controls");
                AtomicWriteBytes(destinationControls, File.ReadAllBytes(sourceControls));
            }
            if (activeMods) ReplaceActiveMods(destination, sourceMods!, verify: true);
        }
        catch
        {
            if (activeMods && previousMods is not null)
            {
                try { ReplaceActiveMods(destination, previousMods, verify: true); } catch { }
            }
            if (copyControls)
            {
                try
                {
                    if (hadControls && previousControls is not null) AtomicWriteBytes(destinationControls, previousControls);
                    else File.Delete(destinationControls);
                }
                catch { }
            }
            throw;
        }
    }

    public void Delete(ProfileRef profile, bool backupFirst)
    {
        EnsureWritable(profile, "delete Profile");
        if (backupFirst) _backup.Backup(profile.ProfileSii, "pre-delete");
        Directory.Delete(profile.Folder, true);
    }

    private void EnsureWritable(ProfileRef profile, string action)
    {
        if (!profile.IsWritable)
            throw new UnauthorizedAccessException("Steam/Cloud profiles are read-only.");
        if (_gameState.IsRunning())
            throw new InvalidOperationException($"Exit ETS2 or ATS before attempting to {action}.");
    }

    private byte[] ReadBytesForWrite(string path, out string text)
    {
        var original = File.ReadAllBytes(path);
        if (original.AsSpan().IndexOf("SiiNunit"u8) >= 0)
        {
            text = Decode(original);
            return original;
        }
        if (ScsCCodec.IsScsC(original))
        {
            text = Decode(ScsCCodec.Decrypt(original));
            return original;
        }
        throw new InvalidDataException("The profile uses an unsupported encrypted format; refusing to overwrite it.");
    }

    private static byte[] EncodeForWrite(byte[] original, string text) =>
        ScsCCodec.IsScsC(original)
            ? ScsCCodec.Encrypt(Encoding.UTF8.GetBytes(text.TrimStart('\uFEFF')))
            : Encoding.UTF8.GetBytes(text);

    private List<string> ReadEntries(string path)
    {
        try { return ReadText(path).Split('\n').Select(x => ActiveEntry.Match(x)).Where(m => m.Success).Select(m => Unescape(m.Groups["value"].Value)).ToList(); }
        catch { return []; }
    }

    private Dictionary<string, string> ReadFields(string path)
    {
        try { return ReadText(path).Split('\n').Select(x => Field.Match(x)).Where(m => m.Success).ToDictionary(m => m.Groups["name"].Value, m => Unescape(m.Groups["value"].Value), StringComparer.Ordinal); }
        catch { return new(StringComparer.Ordinal); }
    }

    private string ReadText(string path)
    {
        var bytes = File.ReadAllBytes(path);
        if (bytes.AsSpan().IndexOf("SiiNunit"u8) >= 0) return Decode(bytes);
        if (bytes.Length >= 56 && bytes.AsSpan(0, 4).SequenceEqual("ScsC"u8))
        {
            try
            {
                using var aes = Aes.Create();
                aes.Key = [0x2A, 0x5F, 0xCB, 0x17, 0x91, 0xD2, 0x2F, 0xB6, 0x02, 0x45, 0xB3, 0xD8, 0x36, 0x9E, 0xD0, 0xB2, 0xC2, 0x73, 0x71, 0x56, 0x3F, 0xBF, 0x1F, 0x3C, 0x9E, 0xDF, 0x6B, 0x11, 0x82, 0x5A, 0x5D, 0x0A];
                aes.IV = bytes[36..52]; aes.Mode = CipherMode.CBC; aes.Padding = PaddingMode.PKCS7;
                var decrypted = aes.CreateDecryptor().TransformFinalBlock(bytes, 56, bytes.Length - 56);
                using var input = new MemoryStream(decrypted);
                using var zlib = new ZLibStream(input, CompressionMode.Decompress);
                using var output = new MemoryStream(); zlib.CopyTo(output);
                return Decode(output.ToArray());
            }
            catch (CryptographicException) { }
            catch (InvalidDataException) { }
        }
        if (!string.IsNullOrWhiteSpace(_decryptExecutable) && File.Exists(_decryptExecutable))
        {
            var external = TryExternalDecrypt(path);
            if (external is not null) return Decode(external);
        }
        return Decode(bytes);
    }

    private byte[]? TryExternalDecrypt(string path)
    {
        var tempRoot = Path.Combine(Path.GetTempPath(), "ets2mm-decrypt-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(tempRoot);
        try
        {
            var input = Path.Combine(tempRoot, Path.GetFileName(path));
            var output = Path.Combine(tempRoot, Path.GetFileNameWithoutExtension(path) + ".dec");
            File.Copy(path, input);
            using var process = Process.Start(new ProcessStartInfo
            {
                FileName = _decryptExecutable,
                Arguments = $"\"{input}\" \"{output}\"",
                WorkingDirectory = tempRoot,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardError = true,
                RedirectStandardOutput = true,
            });
            if (process is null || !process.WaitForExit(30_000) || process.ExitCode != 0) return null;
            foreach (var candidate in Directory.EnumerateFiles(tempRoot))
            {
                var data = File.ReadAllBytes(candidate);
                if (data.AsSpan().IndexOf("SiiNunit"u8) >= 0) return data;
            }
            return null;
        }
        catch (Exception) { return null; }
        finally
        {
            try { Directory.Delete(tempRoot, true); } catch { }
        }
    }

    private static string Decode(byte[] bytes) => new UTF8Encoding(false, false).GetString(bytes).TrimStart('\uFEFF');
    private static string Escape(string value) => value.Replace("\\", "\\\\").Replace("\"", "\\\"");
    private static string Unescape(string value) => value.Replace("\\\"", "\"").Replace("\\\\", "\\");
    private static void AtomicWrite(string path, byte[] data)
    {
        var temp = path + ".tmp-" + Guid.NewGuid().ToString("N");
        try
        {
            using (var stream = new FileStream(temp, FileMode.CreateNew, FileAccess.Write, FileShare.None, 64 * 1024, FileOptions.WriteThrough))
            {
                stream.Write(data);
                stream.Flush(true);
            }
            File.Move(temp, path, true);
        }
        finally { try { File.Delete(temp); } catch { } }
    }

    private static void AtomicWriteBytes(string path, byte[] data)
    {
        var temp = path + ".tmp-" + Guid.NewGuid().ToString("N");
        try
        {
            File.WriteAllBytes(temp, data);
            File.Move(temp, path, true);
        }
        finally { try { File.Delete(temp); } catch { } }
    }
    private static void DirectoryCopy(string source, string target)
    {
        Directory.CreateDirectory(target);
        foreach (var file in Directory.EnumerateFiles(source)) File.Copy(file, Path.Combine(target, Path.GetFileName(file)));
        foreach (var dir in Directory.EnumerateDirectories(source)) DirectoryCopy(dir, Path.Combine(target, Path.GetFileName(dir)));
    }
}
