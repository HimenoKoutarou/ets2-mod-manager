# Final Migration Review: 2026-09-10

## Scope

This review covers the five-stage migration plan for the ETS2 Mod Manager:

1. Behavior contracts and core Mod rules.
2. Pure domain/application services.
3. Windows, profile, save, backup, and archive infrastructure.
4. Rust archive/BSII/scanner cores and WPF composition.
5. Final functional parity, publishing, and acceptance.

All five stages are complete. The production entry point is the self-contained
.NET/WPF client; Python remains only for compatibility and regression tests.

## Final Checks

- `build-dotnet.bat`: passed; self-contained `win-x64` publish completed.
- .NET migration ContractTests: passed, including ScsC profile roundtrip,
  ZIP tree extraction, archive path traversal rejection, localization priority,
  update validation, and BSII save editing.
- Rust `cargo fmt --check`: passed.
- Rust GNU-target workspace tests: passed, 7 tests; the existing `.drectve`
  linker warning remains non-fatal.
- Python regression suite: passed, 64 tests.
- Published executable smoke test: `ETS2 Mod Manager`, responding normally.
- `start.bat`: starts only `dist-dotnet/ETS2ModManager.WpfClient.exe`.

## Boundary Fixes in Final Review

- Archive paths are normalized by segments and reject absolute paths, drive
  prefixes, and `..` traversal before writing extracted files.
- Update staging accepts only a file-name executable, uses argument-list based
  process startup, escapes batch variables, and removes temporary files when a
  download or validation step is cancelled or fails.
- Contract tests cover both boundary classes so the fixes remain regression
  protected.

## Known Non-Blocking Limitations

- A local MSVC `link.exe` is not installed in the default shell. The supported
  build path uses the GNU Rust target and succeeds through `build-dotnet.bat`.
- The optional screenshot-level WPF inspection is not part of the automated
  acceptance run; process-level startup and responsiveness were verified.
- The Rust archive slice remains dependency-light. Proprietary archive formats
  continue through the managed external-tool adapter where required.
