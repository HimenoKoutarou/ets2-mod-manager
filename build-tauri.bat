@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo Building ETS2 Mod Manager Tauri release...

set "RELEASE_DIR=%~dp0src\frontend\src-tauri\target\release"
set "RELEASE_EXE=%RELEASE_DIR%\ets2-mod-manager.exe"

rem Always remove the previous executable before starting a new build.
rem This prevents stale binaries from being mistaken for the new build.
taskkill /f /im ets2-mod-manager.exe >nul 2>nul
if exist "%RELEASE_EXE%" (
  del /f /q "%RELEASE_EXE%"
  if exist "%RELEASE_EXE%" (
    echo Failed to remove the previous release executable.
    exit /b 1
  )
)
if exist "%RELEASE_DIR%\bundle" (
  rmdir /s /q "%RELEASE_DIR%\bundle"
  if exist "%RELEASE_DIR%\bundle" (
    echo Failed to remove the previous release bundle.
    exit /b 1
  )
)

set "TOOLCHAIN_ROOT=%TEMP%\ets2modmanager-toolchain"
set "RUSTUP_EXE=%TOOLCHAIN_ROOT%\cargo\bin\rustup.exe"
set "CARGO_EXE=%TOOLCHAIN_ROOT%\cargo\bin\cargo.exe"
set "RUSTC_EXE=%TOOLCHAIN_ROOT%\rustup\toolchains\stable-x86_64-pc-windows-gnu\bin\rustc.exe"
set "RUSTFMT_EXE=%TOOLCHAIN_ROOT%\rustup\toolchains\stable-x86_64-pc-windows-gnu\bin\rustfmt.exe"
set "MINGW_ROOT=C:\mingw64"
set "CARGO_HOME=%TOOLCHAIN_ROOT%\cargo"

if exist "%RUSTUP_EXE%" if exist "%CARGO_EXE%" if exist "%RUSTC_EXE%" (
  set "RUSTUP_HOME=%TOOLCHAIN_ROOT%\rustup"
  set "CARGO_HOME=%TOOLCHAIN_ROOT%\cargo"
  set "RUSTUP_TOOLCHAIN=stable-x86_64-pc-windows-gnu"
  set "_REG_INCOMPLETE="
  if not exist "%CARGO_HOME%\registry\src\index.crates.io-1949cf8c6b5b557f\libsqlite3-sys-0.30.1\bindgen-bindings\bindgen_3.14.0.rs" set "_REG_INCOMPLETE=1"
  if not exist "%CARGO_HOME%\registry\src\index.crates.io-1949cf8c6b5b557f\atomic-waker-*" set "_REG_INCOMPLETE=1"
  if defined _REG_INCOMPLETE (
    if exist "%USERPROFILE%\.cargo\registry\src\index.crates.io-1949cf8c6b5b557f\atomic-waker-*" (
      echo Cached Cargo registry is incomplete; using the user Cargo registry.
      set "CARGO_HOME=%USERPROFILE%\.cargo"
    )
  )
  set "PATH=%TOOLCHAIN_ROOT%\rustup\toolchains\stable-x86_64-pc-windows-gnu\bin;%TOOLCHAIN_ROOT%\cargo\bin;%MINGW_ROOT%\bin;%PATH%"
  echo Rust toolchain: stable-x86_64-pc-windows-gnu
) else (
  where cargo >nul 2>nul
  if errorlevel 1 (
    echo Rust toolchain was not found. Install Rust or populate %TOOLCHAIN_ROOT%.
    exit /b 1
  )
  where rustc >nul 2>nul
  if errorlevel 1 (
    echo rustc was not found.
    exit /b 1
  )
)

where npm >nul 2>nul
if errorlevel 1 (
  echo npm was not found. Install Node.js LTS first.
  exit /b 1
)

where cargo >nul 2>nul
if errorlevel 1 (
  echo cargo was not found.
  exit /b 1
)
where rustc >nul 2>nul
if errorlevel 1 (
  echo rustc was not found.
  exit /b 1
)
if defined USE_CACHED_RUST if not exist "%RUSTFMT_EXE%" (
  echo rustfmt is missing from the GNU Rust toolchain.
  echo Run: "%RUSTUP_EXE%" component add rustfmt --toolchain %RUSTUP_TOOLCHAIN%
  exit /b 1
)
if defined USE_CACHED_RUST (
  if exist "%MINGW_ROOT%\bin\gcc.exe" (
    echo MinGW: %MINGW_ROOT%\bin
  ) else (
    echo MinGW gcc was not found at %MINGW_ROOT%\bin\gcc.exe.
    exit /b 1
  )
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

set "TAURI_BUILD_ARGS=--no-bundle"
if /I "%TAURI_BUNDLE%"=="1" set "TAURI_BUILD_ARGS="
echo Tauri build mode: %TAURI_BUILD_ARGS%
call npm run desktop:build -- %TAURI_BUILD_ARGS%
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
if /I "%TAURI_BUNDLE%"=="1" (
  if not exist "src\frontend\src-tauri\target\release\bundle" (
    echo Tauri bundle was requested but no bundle directory was generated.
    exit /b 1
  )
  echo   src\frontend\src-tauri\target\release\bundle\
) else (
  echo Installer bundling is skipped by default. Set TAURI_BUNDLE=1 to build NSIS.
)
endlocal
exit /b 0
