# Migration Status: 2026-09-09

## Production direction

The production desktop client is now the Tauri 2 application in
`src/frontend` (React/TypeScript UI, Rust command layer, SQLite persistence).
The `.NET 10/WPF` client and Python/PySide6 implementation remain as
compatibility and parity references while their remaining feature surface is
retired incrementally. `start.bat` and `build-tauri.bat` target Tauri; use
`build-dotnet.bat` only when reproducing the legacy WPF build.

## Completed stages

- M11: C# infrastructure is executable. Profile discovery/read/write, atomic
  replacement, backup zip creation, SQLite WAL index, and filesystem scanning
  are covered by the .NET migration smoke contract.
- M12: Rust archive helpers, manifest extraction, filesystem scan primitives,
  BSII schema/object walking, and C ABI JSON endpoints are implemented.
- M13: The legacy WPF/MVVM client was wired to the application layer. Profile
  selection, enabled Mod rows, priority conversion, Save Profile, progress,
  and Rust-first hybrid scanning are connected.
- M14: ScsC profile reads use the known AES/zlib container format; startup and
  self-contained publishing prefer the .NET client. Python remains only as a
  compatibility fallback for features not yet exposed by the WPF client.

## Verification on September 9, 2026

- .NET 10 solution Release build: 0 warnings, 0 errors.
- Migration ContractTests: passed, including encrypted ScsC profile read,
  active_mods write verification, backup creation, scanner, and SQLite roundtrip.
- Rust `cargo fmt --check`: passed.
- Rust `cargo check --workspace`: passed without third-party archive dependencies.
- Existing Python regression suite remains untouched and available for the
  legacy feature surface.

## Final migration verification (September 9, 2026)

- M15: structured BSII save editing, profile copy/rename/settings, crash
  diagnosis, localization scan, link migration, update check, and game launch
  adapters are implemented behind Application ports.
- M16: Legacy WPF tabs expose Mod management, save editing, localization scanning,
  crash diagnosis, migration, and update workflows.
- M17: Rust FFI builds on `x86_64-pc-windows-gnu` when MSVC `link.exe` is not
  installed; the native DLL is copied into the self-contained publish output.
- M18: completed the remaining legacy-client capability migration. WPF now
  injects all Application services, exposes update installation, extractor
  entry extraction, link restore, categories, cities, Workshop metadata and
  localization workflows. Complete scans atomically replace the SQLite index,
  and Rust scan results receive managed manifest/extractor metadata fallback.
- The Tauri frontend now owns the production Mod workflow. Python and WPF
  remain compatibility-only and are no longer production runtime dependencies.
- Published assets include the Rust FFI DLL, `SII_Decrypt.exe`, the three
  translation files, and all three external extractor executables.
- Verification: .NET Release build passed with 0 warnings/errors;
  ContractTests passed (including adapter contracts); Rust fmt/check passed;
  native Rust FFI build and GNU-target Rust unit tests passed; Python
  regression suite passed 64/64; self-contained `win-x64` publish completed
  successfully.

## Known boundary

The Rust archive core currently reads directory manifests and ZIP stored
entries. Deflated ZIP and proprietary HashFS payload extraction remain outside
the dependency-free Rust slice; the hybrid scanner deliberately falls back to
managed ZIP/extractor adapters so this boundary does not block core Mod
management. The migration is functionally complete, while that native
optimization remains an optional future performance slice.

## Final verification on September 10, 2026

- M19: external archive tree extraction is implemented for directories,
  readable ZIP archives, HashFS/SCS# through the bundled Extractor, and
  AEM/encrypted ZIP through SXC. External process cancellation terminates the
  complete process tree and temporary extraction directories are cleaned up.
- M20: localization scanning uses the shared archive adapter for proprietary
  packages and merges locale values and definition metadata by key while
  preserving high-to-low UI priority and authoritative blank values.
- M21: Legacy WPF composition shares one archive adapter between Mod scanning,
  localization, and Tools extraction. The published client starts without the
  Python runtime and responds with the expected main window title.
- Final legacy evidence on September 10, 2026: .NET Release build 0
  warnings/0 errors; migration ContractTests passed; Python regression suite
  64/64; the Tauri frontend build passed; Rust source checks remain subject to
  the local toolchain cache being complete. The old `build-dotnet.bat` release
  remains reproducible for parity work.

## Tauri migration checkpoints

- T1: React/Vite shell, three-language UI, Profile selection, Mod list,
  enable/disable, drag sorting, presets, local saves, diagnostics and
  localization snapshots are connected through Tauri commands.
- T2: SQLite Mod and localization indexes persist add/update/remove and
  unchanged-package cache hits across restarts.
- T3: Tauri is the production entry point. WPF is retained only as a
  compatibility reference until save mutation, full localization export,
  complete crash analysis, Workshop, city search, tools and updater parity are
  explicitly reimplemented or removed from scope.

## Stage 6 acceptance continuation (September 10, 2026)

- `build-tauri.bat` now defaults to `tauri build --no-bundle`, which produces
  the release executable without requiring an online NSIS download. Set
  `TAURI_BUNDLE=1` to request the NSIS installer explicitly.
- Tauri Rust path discovery now covers Steam registry values,
  `steamapps/libraryfolders.vdf`, OneDrive Documents, Workshop libraries,
  Steam Cloud profiles, and game executable lookup; all discovered Workshop
  content roots are scanned.
- The SQLite Mod index now stores a content fingerprint for directory packages,
  so nested add/delete/rename changes invalidate cached manifest metadata even
  when total size and latest mtime are unchanged.
- Tauri UI priority movement is constrained to enabled Mods. Disabled Mods stay
  after the active block, and profile writes continue to convert UI high-to-low
  order to the game's low-to-high `active_mods` order.
- Current acceptance evidence: Python 65/65, frontend build passed, Rust
  `cargo check --locked` passed, and the default Tauri build generated the
  release exe. NSIS bundling remains an explicit packaging step because the
  host may not have the installer cache or network access.
