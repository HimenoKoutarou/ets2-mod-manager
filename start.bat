@echo off
chcp 65001 >nul
cd /d "%~dp0"

:: Production entry point: migrated .NET/WPF client only.
if not exist "dist-dotnet\ETS2ModManager.WpfClient.exe" (
    echo .NET release is missing. Run build-dotnet.bat first.
    exit /b 1
)
start "" "dist-dotnet\ETS2ModManager.WpfClient.exe"
exit /b 0
