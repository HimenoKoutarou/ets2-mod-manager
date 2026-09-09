using ETS2ModManager.Contracts;

namespace ETS2ModManager.Application;

public sealed class ProfileApplicationService(
    IProfileRepository repository,
    IGameState gameState,
    IBackupStore backupStore)
{
    public IReadOnlyList<string> ReadActiveMods(ProfileRef profile) =>
        repository.ReadActiveMods(profile).ToArray();

    public void ReplaceActiveMods(ProfileRef profile, IReadOnlyList<string> activeMods, bool verify = true)
    {
        RequireWritable(profile, "modify Profile");
        repository.ReplaceActiveMods(profile, activeMods.ToArray(), verify);
    }

    public ProfileRef Copy(ProfileRef profile, string displayName, string companyName)
    {
        RequireWritable(profile, "copy Profile");
        return repository.Copy(profile, displayName, companyName);
    }

    public ProfileRef Rename(ProfileRef profile, string displayName, string companyName)
    {
        RequireWritable(profile, "rename Profile");
        return repository.Rename(profile, displayName, companyName);
    }

    public void CopySettings(ProfileRef source, ProfileRef destination, bool activeMods, bool controls)
    {
        RequireWritable(destination, "copy Profile settings");
        repository.CopySettings(source, destination, activeMods, controls);
    }

    public void Delete(ProfileRef profile, bool backupFirst = true)
    {
        RequireWritable(profile, "delete Profile");
        repository.Delete(profile, backupFirst);
    }

    public string? Backup(ProfileRef profile, string tag = "manual") =>
        backupStore.Backup(profile.ProfileSii, tag);

    private void RequireWritable(ProfileRef profile, string action)
    {
        if (!profile.IsWritable)
        {
            throw new UnauthorizedAccessException("Steam/Cloud profiles are read-only.");
        }
        if (gameState.IsRunning())
        {
            throw new InvalidOperationException($"Exit ETS2 or ATS before attempting to {action}.");
        }
    }
}
