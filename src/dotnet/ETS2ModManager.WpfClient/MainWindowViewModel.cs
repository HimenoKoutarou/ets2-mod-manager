using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using ETS2ModManager.Contracts;

namespace ETS2ModManager.WpfClient;

public partial class MainWindowViewModel : ObservableObject
{
    public ObservableCollection<ModRecord> Mods { get; } = [];

    [ObservableProperty]
    private string status = "Migration shell ready";

    [ObservableProperty]
    private bool isScanning;

    [RelayCommand(CanExecute = nameof(CanStartScan))]
    private async Task StartScanAsync()
    {
        IsScanning = true;
        StartScanCommand.NotifyCanExecuteChanged();
        try
        {
            Status = "ModScanWorker integration is ready for the Rust scanner.";
            await Task.CompletedTask;
        }
        finally
        {
            IsScanning = false;
            StartScanCommand.NotifyCanExecuteChanged();
        }
    }

    private bool CanStartScan() => !IsScanning;
}
