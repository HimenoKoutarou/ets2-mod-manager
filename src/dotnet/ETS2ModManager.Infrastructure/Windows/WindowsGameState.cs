using System.Diagnostics;
using ETS2ModManager.Application;

namespace ETS2ModManager.Infrastructure.Windows;

public sealed class WindowsGameState : IGameState
{
    private static readonly string[] ProcessNames = ["eurotrucks2", "amtrucks"];

    public bool IsRunning() => ProcessNames.Any(name => Process.GetProcessesByName(name).Length > 0);
}
