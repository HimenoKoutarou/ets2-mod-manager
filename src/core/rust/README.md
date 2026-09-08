# Rust Core Prototype

`ets2_core_ffi` is the first C ABI slice for the planned Rust high-throughput
core. It intentionally exposes only UTF-8/byte buffers, DTO-shaped JSON, and
integer error codes. The current prototype classifies archive headers, reads a
BSII version, and counts package paths; it does not replace the Python parser
yet.

Build on a machine with Rust installed:

```powershell
cargo test --manifest-path F:/ETS2ModManager/src/core/rust/Cargo.toml
cargo build --manifest-path F:/ETS2ModManager/src/core/rust/Cargo.toml -p ets2_core_ffi --release
```
