# .NET 10 Migration Application

This directory contains the legacy `.NET 10` compatibility client. The
production desktop client is now the Tauri 2 application under `src/frontend`;
this directory remains available for parity checks and features that have not
yet been deleted from the migration reference implementation.

- `Contracts`: language-neutral DTO definitions.
- `Domain`: Mod identity and priority rules, verified by the shared JSON golden contract.
- `Application`: use cases and infrastructure ports.
- `Infrastructure`: Windows adapters, SQLite schema, and Rust C ABI client.
- `WpfClient`: legacy WPF/MVVM desktop shell using CommunityToolkit.Mvvm.
- `ModScanWorker`: out-of-process Rust scanner host.
- `ContractTests`: dependency-free executable test runner for shared golden fixtures.

The WPF client mirrors the Mod workflow: profile discovery, active_mods ordering,
Rust-first scanning with managed archive/extractor fallback, SQLite cache
persistence, backup-before-write, plaintext/ScsC profile reads, save editing,
crash diagnostics, localization, Workshop metadata, categories, city lookup,
directory migration, and update installation. Python remains available only as
a compatibility tool and test fixture for the legacy feature surface.

Build the legacy compatibility client from the repository root with
`build-dotnet.bat`. Production builds use `build-tauri.bat`, and `start.bat`
launches the Tauri executable from `src/frontend/src-tauri/target/release`.
