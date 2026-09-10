@echo off
chcp 65001 >nul
cd /d "%~dp0"

:: Production entry point: Tauri desktop client.
set "TAURI_EXE=src\frontend\src-tauri\target\release\ets2-mod-manager.exe"
if not exist "%TAURI_EXE%" (
    echo Tauri release is missing. Run build-tauri.bat first.
    exit /b 1
)
start "" "%TAURI_EXE%"
exit /b 0
