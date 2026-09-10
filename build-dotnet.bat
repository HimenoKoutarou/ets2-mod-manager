@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo Building ETS2ModManager .NET 10 WPF release...

set "TOOLCHAIN_ROOT=%TEMP%\ets2modmanager-toolchain"
set "DOTNET_ROOT=%TOOLCHAIN_ROOT%\dotnet"
set "DOTNET_EXE=dotnet"
set "RUSTUP_EXE=%TOOLCHAIN_ROOT%\cargo\bin\rustup.exe"
set "CARGO_EXE=%TOOLCHAIN_ROOT%\cargo\bin\cargo.exe"
set "RUSTC_EXE=%TOOLCHAIN_ROOT%\rustup\toolchains\stable-x86_64-pc-windows-gnu\bin\rustc.exe"
set "RUSTFMT_EXE=%TOOLCHAIN_ROOT%\rustup\toolchains\stable-x86_64-pc-windows-gnu\bin\rustfmt.exe"
set "MINGW_ROOT=C:\mingw64"

if exist "%DOTNET_ROOT%\dotnet.exe" (
  set "DOTNET_EXE=%DOTNET_ROOT%\dotnet.exe"
  set "PATH=%DOTNET_ROOT%;%PATH%"
)

if exist "%RUSTUP_EXE%" if exist "%CARGO_EXE%" if exist "%RUSTC_EXE%" if exist "%RUSTFMT_EXE%" if exist "%MINGW_ROOT%\bin\gcc.exe" (
  set "RUSTUP_HOME=%TOOLCHAIN_ROOT%\rustup"
  set "CARGO_HOME=%TOOLCHAIN_ROOT%\cargo"
  set "RUSTUP_TOOLCHAIN=stable-x86_64-pc-windows-gnu"
  set "PATH=%TOOLCHAIN_ROOT%\rustup\toolchains\stable-x86_64-pc-windows-gnu\bin;%TOOLCHAIN_ROOT%\cargo\bin;%MINGW_ROOT%\bin;%PATH%"
  set "USE_CACHED_RUST=1"
)

if not defined USE_CACHED_RUST (
  set "CARGO_EXE=cargo"
  where cargo >nul 2>nul
  if errorlevel 1 set "CARGO_EXE="
  where rustc >nul 2>nul
  if errorlevel 1 set "CARGO_EXE="
  if not defined CARGO_EXE (
    echo Rust toolchain was not found. Install Rust or populate %TOOLCHAIN_ROOT%.
    exit /b 1
  )
  echo Rust toolchain: system default
) else (
  echo Rust toolchain: stable-x86_64-pc-windows-gnu
  echo MinGW: %MINGW_ROOT%\bin
)

"%DOTNET_EXE%" --version >nul 2>nul
if errorlevel 1 (
  echo .NET SDK was not found. Install .NET 10 SDK or populate %DOTNET_ROOT%.
  exit /b 1
)

echo Building Rust native core...
"%CARGO_EXE%" build --manifest-path "src\core\rust\Cargo.toml" -p ets2_core_ffi --release --target x86_64-pc-windows-gnu
if errorlevel 1 (
  echo Rust native core build failed.
  exit /b 1
)
echo Rust native core ready.

"%DOTNET_EXE%" publish "src\dotnet\ETS2ModManager.WpfClient\ETS2ModManager.WpfClient.csproj" ^
  --configuration Release --runtime win-x64 --self-contained true ^
  --output "dist-dotnet" /p:PublishSingleFile=false
if errorlevel 1 (
  echo Build failed.
  exit /b 1
)
if exist "src\core\rust\target\x86_64-pc-windows-gnu\release\ets2_core_ffi.dll" copy /Y "src\core\rust\target\x86_64-pc-windows-gnu\release\ets2_core_ffi.dll" "dist-dotnet\ets2_core_ffi.dll" >nul
if exist "src\core\rust\target\release\ets2_core_ffi.dll" copy /Y "src\core\rust\target\release\ets2_core_ffi.dll" "dist-dotnet\ets2_core_ffi.dll" >nul
echo Published to dist-dotnet\
endlocal
