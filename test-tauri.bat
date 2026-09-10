@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo Running ETS2 Mod Manager Tauri backend tests...

set "TOOLCHAIN_ROOT=%TEMP%\ets2modmanager-toolchain"
set "RUSTUP_EXE=%TOOLCHAIN_ROOT%\cargo\bin\rustup.exe"
set "CARGO_EXE=%TOOLCHAIN_ROOT%\cargo\bin\cargo.exe"
set "RUSTC_EXE=%TOOLCHAIN_ROOT%\rustup\toolchains\stable-x86_64-pc-windows-gnu\bin\rustc.exe"
set "RUSTFMT_EXE=%TOOLCHAIN_ROOT%\rustup\toolchains\stable-x86_64-pc-windows-gnu\bin\rustfmt.exe"
set "MINGW_ROOT=C:\mingw64"

if not exist "%RUSTUP_EXE%" (
  echo rustup was not found at %RUSTUP_EXE%.
  exit /b 1
)
if not exist "%CARGO_EXE%" (
  echo cargo was not found at %CARGO_EXE%.
  exit /b 1
)
if not exist "%RUSTC_EXE%" (
  echo GNU rustc was not found at %RUSTC_EXE%.
  exit /b 1
)
if not exist "%RUSTFMT_EXE%" (
  echo GNU rustfmt was not found at %RUSTFMT_EXE%.
  echo Run: "%RUSTUP_EXE%" component add rustfmt --toolchain stable-x86_64-pc-windows-gnu
  exit /b 1
)
if not exist "%MINGW_ROOT%\bin\gcc.exe" (
  echo MinGW gcc was not found at %MINGW_ROOT%\bin\gcc.exe.
  exit /b 1
)

set "RUSTUP_HOME=%TOOLCHAIN_ROOT%\rustup"
set "CARGO_HOME=%TOOLCHAIN_ROOT%\cargo"
set "RUSTUP_TOOLCHAIN=stable-x86_64-pc-windows-gnu"
set "PATH=%TOOLCHAIN_ROOT%\rustup\toolchains\stable-x86_64-pc-windows-gnu\bin;%TOOLCHAIN_ROOT%\cargo\bin;%MINGW_ROOT%\bin;%PATH%"

echo Checking Tauri formatting...
"%CARGO_EXE%" fmt --manifest-path "src\frontend\src-tauri\Cargo.toml" -- --check
if errorlevel 1 exit /b 1

echo Running Tauri backend unit tests without the WebView2 runtime...
"%CARGO_EXE%" test --lib --manifest-path "src\frontend\src-tauri\Cargo.toml" --no-default-features --offline --locked
if errorlevel 1 exit /b 1

echo Tauri backend tests passed.
endlocal
exit /b 0
