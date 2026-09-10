# Rust Core

`ets2_core_ffi` exposes UTF-8/byte buffers, DTO-shaped JSON, and integer error
codes. The core now includes manifest extraction for directories and stored ZIP
entries, filesystem package enumeration, and a bounds-checked BSII schema/object
walker. The .NET client uses it opportunistically and falls back to its managed
scanner when a native DLL is unavailable.

Build on a machine with Rust installed:

```powershell
cargo test --manifest-path F:/ETS2ModManager/src/core/rust/Cargo.toml
cargo build --manifest-path F:/ETS2ModManager/src/core/rust/Cargo.toml -p ets2_core_ffi --release
```

On Windows hosts without the MSVC linker, the project can be built with the
GNU target and still loaded by the managed fallback client:

```powershell
rustup target add x86_64-pc-windows-gnu
cargo build --manifest-path F:/ETS2ModManager/src/core/rust/Cargo.toml -p ets2_core_ffi --release --target x86_64-pc-windows-gnu
```

The repository's cached Windows toolchain uses GNU Rust plus MinGW. Configure
it in PowerShell before running the same commands:

```powershell
$root = "$env:TEMP\ets2modmanager-toolchain"
$env:RUSTUP_HOME = "$root\rustup"
$env:CARGO_HOME = "$root\cargo"
$env:RUSTUP_TOOLCHAIN = "stable-x86_64-pc-windows-gnu"
$env:PATH = "$root\cargo\bin;$root\rustup\toolchains\stable-x86_64-pc-windows-gnu\bin;C:\mingw64\bin;$env:PATH"

rustfmt --version
cargo fmt --manifest-path F:/ETS2ModManager/src/core/rust/Cargo.toml --all -- --check
cargo test --manifest-path F:/ETS2ModManager/src/core/rust/Cargo.toml --locked
```

For the Tauri backend, use `F:/ETS2ModManager/test-tauri.bat`. It runs
formatting and backend unit tests with the `desktop` feature disabled, so the
tests do not require the WebView2 loader DLL. The production path remains
`F:/ETS2ModManager/build-tauri.bat`, which enables the desktop feature and
builds the full Tauri application.
