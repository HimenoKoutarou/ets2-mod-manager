# .NET 10 Migration Application

This directory contains the production desktop client. It targets `.NET 10`
and uses Python/Qt only for legacy compatibility and regression coverage.

- `Contracts`: language-neutral DTO definitions.
- `Domain`: Mod identity and priority rules, verified by the shared JSON golden contract.
- `Application`: use cases and infrastructure ports.
- `Infrastructure`: Windows adapters, SQLite schema, and Rust C ABI client.
- `WpfClient`: WPF/MVVM desktop shell using CommunityToolkit.Mvvm.
- `ModScanWorker`: out-of-process Rust scanner host.
- `ContractTests`: dependency-free executable test runner for shared golden fixtures.

The WPF client owns the Mod workflow: profile discovery, active_mods ordering,
Rust-first scanning with managed archive/extractor fallback, SQLite cache
persistence, backup-before-write, plaintext/ScsC profile reads, save editing,
crash diagnostics, localization, Workshop metadata, categories, city lookup,
directory migration, and update installation. Python remains available only as
a compatibility tool and test fixture for the legacy feature surface.

Build a self-contained Windows release from the repository root with
`build-dotnet.bat`; `start.bat` launches the published .NET client from
`dist-dotnet`.
