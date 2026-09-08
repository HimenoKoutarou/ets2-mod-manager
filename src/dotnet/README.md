# .NET 10 Migration Shell

This directory is the side-by-side replacement shell. It targets `.NET 10`
and keeps the Python/Qt application operational during migration.

- `Contracts`: language-neutral DTO definitions.
- `Domain`: Mod identity and priority rules, verified by the shared JSON golden contract.
- `Application`: use cases and infrastructure ports.
- `Infrastructure`: Windows adapters, SQLite schema, and Rust C ABI client.
- `WpfClient`: WPF/MVVM desktop shell using CommunityToolkit.Mvvm.
- `ModScanWorker`: out-of-process Rust scanner host.
- `ContractTests`: dependency-free executable test runner for shared golden fixtures.

The WPF shell is not a feature-complete replacement yet. Python remains the
shipping compatibility implementation until each golden-tested workflow is
connected to the .NET Application services.
