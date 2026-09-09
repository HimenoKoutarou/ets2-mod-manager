@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "DOTNET_ROOT=%TEMP%\ets2modmanager-toolchain\dotnet"
if not exist "%DOTNET_ROOT%\dotnet.exe" set "DOTNET_ROOT="
if defined DOTNET_ROOT set "PATH=%DOTNET_ROOT%;%PATH%"

echo Building ETS2ModManager .NET 10 WPF release...
set "CARGO_EXE="
where cargo >nul 2>nul
if not errorlevel 1 set "CARGO_EXE=cargo"
if not defined CARGO_EXE if exist "%TEMP%\ets2modmanager-toolchain\cargo\bin\cargo.exe" set "CARGO_EXE=%TEMP%\ets2modmanager-toolchain\cargo\bin\cargo.exe"
if defined CARGO_EXE (
  if /I "%CARGO_EXE%"=="%TEMP%\ets2modmanager-toolchain\cargo\bin\cargo.exe" (
    set "RUSTUP_HOME=%TEMP%\ets2modmanager-toolchain\rustup"
    set "CARGO_HOME=%TEMP%\ets2modmanager-toolchain\cargo"
    set "RUSTUP_TOOLCHAIN=stable-x86_64-pc-windows-msvc"
  )
  echo Building Rust native core...
  "%CARGO_EXE%" build --manifest-path "src\core\rust\Cargo.toml" -p ets2_core_ffi --release --target x86_64-pc-windows-gnu
  if errorlevel 1 (
    echo Rust native core build failed.
    exit /b 1
  )
  echo Rust native core ready.
) else echo Rust toolchain not found; using any existing native DLL.
dotnet publish "src\dotnet\ETS2ModManager.WpfClient\ETS2ModManager.WpfClient.csproj" ^
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
