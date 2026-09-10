using System.Windows;
using System.IO;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using ETS2ModManager.Application;
using ETS2ModManager.Infrastructure.Backup;
using ETS2ModManager.Infrastructure.Indexing;
using ETS2ModManager.Infrastructure.Paths;
using ETS2ModManager.Infrastructure.Profiles;
using ETS2ModManager.Infrastructure.Scanning;
using ETS2ModManager.Infrastructure.Windows;
using ETS2ModManager.Infrastructure.Diagnostics;
using ETS2ModManager.Infrastructure.Localization;
using ETS2ModManager.Infrastructure.Saves;
using ETS2ModManager.Infrastructure.Updates;
using ETS2ModManager.Infrastructure.Categories;
using ETS2ModManager.Infrastructure.Cities;
using ETS2ModManager.Infrastructure.Workshop;
using ETS2ModManager.Infrastructure.Archives;
using ETS2ModManager.Infrastructure.Presets;

namespace ETS2ModManager.WpfClient;

public partial class MainWindow : Window
{
    private Point _dragStartPoint;
    private ModRowViewModel? _draggedMod;

    public MainWindow()
    {
        InitializeComponent();
        var paths = Ets2Paths.Detect();
        var backup = new FileBackupStore();
        var decryptor = Path.Combine(AppContext.BaseDirectory, "assets", "bin", "SII_Decrypt.exe");
        var profiles = new FileProfileRepository(paths.ProfilesDirectory, paths.SteamProfilesDirectory, paths.SteamCloudDirectory, backup, decryptor);
        var index = new SqliteModIndex(paths.DatabasePath);
        var localizationIndex = new SqliteLocalizationIndex(paths.DatabasePath);
        var application = new ProfileApplicationService(profiles, new WindowsGameState(), backup);
        var gameState = new WindowsGameState();
        var save = new SaveApplicationService(new SaveEditorService(backup), gameState);
        var crash = new CrashApplicationService(new CrashDiagnosisService());
        var archiveAdapter = new ExternalArchiveService(Path.Combine(AppContext.BaseDirectory, "assets", "tools"));
        var localization = new LocalizationApplicationService(new FileLocalizationService(null, archiveAdapter, localizationIndex));
        var links = new LinkMigrationApplicationService(new LinkMigrationService());
        var updates = new UpdateApplicationService(new GitHubUpdateService());
        var launcher = new GameLaunchApplicationService(new WindowsGameLauncherService());
        var categories = new CategoryApplicationService(new JsonCategoryService());
        var cities = new CityLookupApplicationService(new CityLookupService());
        var workshop = new WorkshopMetadataApplicationService(new SteamWorkshopMetadataService());
        var archives = new ExternalArchiveApplicationService(archiveAdapter);
        var presets = new ModPresetApplicationService(new JsonModPresetService());
        var scanner = new IncrementalModScanner(paths.ModDirectory, paths.WorkshopDirectory, index.Query, archiveAdapter);
        DataContext = new MainWindowViewModel(scanner, index, profiles, application, save, crash, localization, links, updates, launcher,
            categories, cities, workshop, archives, presets, paths.DocumentsDirectory, paths.ModDirectory);
        ((MainWindowViewModel)DataContext).Ui.PropertyChanged += (_, _) => UpdateLocalizedHeaders();
        UpdateLocalizedHeaders();
    }

    private void UpdateLocalizedHeaders()
    {
        if (DataContext is not MainWindowViewModel viewModel) return;
        var ui = viewModel.Ui;
        if (ModGrid.Columns.Count >= 5)
        {
            ModGrid.Columns[0].Header = ui.Enabled;
            ModGrid.Columns[1].Header = ui.Name;
            ModGrid.Columns[2].Header = ui.Source;
            ModGrid.Columns[3].Header = ui.Package;
            ModGrid.Columns[4].Header = ui.Category;
        }
        if (LocalizationGrid.Columns.Count >= 5)
        {
            LocalizationGrid.Columns[0].Header = ui.Key;
            LocalizationGrid.Columns[1].Header = ui.Translation;
            LocalizationGrid.Columns[2].Header = ui.Status;
            LocalizationGrid.Columns[3].Header = ui.Category;
            LocalizationGrid.Columns[4].Header = ui.Package;
        }
    }

    private void ModGrid_PreviewMouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        _dragStartPoint = e.GetPosition(null);
        _draggedMod = (e.OriginalSource as DependencyObject)?.FindParent<DataGridRow>()?.Item as ModRowViewModel;
    }

    private void ModGrid_PreviewMouseMove(object sender, MouseEventArgs e)
    {
        if (e.LeftButton != MouseButtonState.Pressed || _draggedMod is null) return;
        var point = e.GetPosition(null);
        if (Math.Abs(point.X - _dragStartPoint.X) < SystemParameters.MinimumHorizontalDragDistance
            && Math.Abs(point.Y - _dragStartPoint.Y) < SystemParameters.MinimumVerticalDragDistance) return;
        DragDrop.DoDragDrop(ModGrid, _draggedMod, DragDropEffects.Move);
        _draggedMod = null;
    }

    private void ModGrid_DragOver(object sender, DragEventArgs e)
    {
        e.Effects = e.Data.GetDataPresent(typeof(ModRowViewModel)) ? DragDropEffects.Move : DragDropEffects.None;
        e.Handled = true;
    }

    private void ModGrid_Drop(object sender, DragEventArgs e)
    {
        if (!e.Data.GetDataPresent(typeof(ModRowViewModel)) || DataContext is not MainWindowViewModel viewModel) return;
        var source = e.Data.GetData(typeof(ModRowViewModel)) as ModRowViewModel;
        var target = (e.OriginalSource as DependencyObject)?.FindParent<DataGridRow>()?.Item as ModRowViewModel;
        if (source is null || target is null || ReferenceEquals(source, target)) return;
        var from = viewModel.Mods.IndexOf(source);
        var to = viewModel.Mods.IndexOf(target);
        if (from >= 0 && to >= 0) viewModel.MoveModByIndex(from, to);
        e.Handled = true;
    }
}

internal static class VisualTreeExtensions
{
    public static T? FindParent<T>(this DependencyObject? child) where T : DependencyObject
    {
        while (child is not null)
        {
            if (child is T match) return match;
            child = VisualTreeHelper.GetParent(child);
        }
        return null;
    }
}
