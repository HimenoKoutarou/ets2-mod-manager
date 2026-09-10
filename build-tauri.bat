@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo Building ETS2 Mod Manager Tauri release...

set "TOOLCHAIN_ROOT=%TEMP%\ets2modmanager-toolchain"
if exist "%TOOLCHAIN_ROOT%\rustup\toolchains\stable-x86_64-pc-windows-gnu\bin\rustc.exe" (
  set "RUSTUP_HOME=%TOOLCHAIN_ROOT%\rustup"
  set "CARGO_HOME=%TOOLCHAIN_ROOT%\cargo"
  set "RUSTUP_TOOLCHAIN=stable-x86_64-pc-windows-gnu"
  set "PATH=%TOOLCHAIN_ROOT%\rustup\toolchains\stable-x86_64-pc-windows-gnu\bin;%TOOLCHAIN_ROOT%\cargo\bin;C:\mingw64\bin;%PATH%"
)

where npm >nul 2>nul
if errorlevel 1 (
  echo npm was not found. Install Node.js LTS first.
  exit /b 1
)

pushd "src\frontend"
if not exist "node_modules" (
  echo Installing frontend dependencies...
  call npm ci --no-audit --no-fund
  if errorlevel 1 (
    popd
    echo npm ci failed.
    exit /b 1
  )
)

call npm run desktop:build
set "BUILD_EXIT=%ERRORLEVEL%"
popd

if not "%BUILD_EXIT%"=="0" (
  echo Tauri build failed.
  exit /b %BUILD_EXIT%
)

if not exist "src\frontend\src-tauri\target\release\ets2-mod-manager.exe" (
  echo Tauri build completed without the expected executable.
  exit /b 1
)

echo Tauri release ready:
echo   src\frontend\src-tauri\target\release\ets2-mod-manager.exe
endlocal
exit /b 0
