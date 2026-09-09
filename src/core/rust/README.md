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
