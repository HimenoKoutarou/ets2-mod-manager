# Final Migration Review: 2026-09-10

## Scope

This review covers the migration plan for the ETS2 Mod Manager:

1. Behavior contracts and core Mod rules.
2. Pure domain/application services.
3. Windows, profile, save, backup, and archive infrastructure.
4. Rust archive/BSII/scanner cores and desktop composition.
5. Tauri frontend integration and acceptance.

The Tauri frontend is the production entry point. The `.NET/WPF` client and
Python implementation remain compatibility references until their remaining
auxiliary feature surface is either migrated or explicitly removed.

## Final Checks

- `build-tauri.bat`: is the production build entry point.
- `build-dotnet.bat`: remains available for legacy parity builds.
- .NET migration ContractTests: passed, including ScsC profile roundtrip,
  ZIP tree extraction, archive path traversal rejection, localization priority,
  update validation, and BSII save editing.
- Rust `cargo fmt --check`: passed for the touched Rust files.
- Rust GNU-target workspace tests: passed, 10 tests; the existing `.drectve`
  linker warning remains non-fatal.
- Tauri backend unit tests: passed, 12 tests, through `test-tauri.bat` with the
  desktop/WebView2 feature disabled.
- Python regression suite: passed, 65 tests.
- Frontend production build: `npm run build` passed.
- Legacy compatibility build: `build-dotnet.bat` passed with the cached .NET
  10 SDK, GNU Rust target, and MinGW linker.
- `start.bat`: starts only `src/frontend/src-tauri/target/release/ets2-mod-manager.exe`.
- Tauri local save workflow: local-only listing by `info.sii` display name,
  named saves before `autosave*`, numeric snapshot reads, and verified
  money/experience/derived-level edits with timestamped backups.

## Boundary Fixes in Final Review

- Archive paths are normalized by segments and reject absolute paths, drive
  prefixes, and `..` traversal before writing extracted files.
- Update staging accepts only a file-name executable, uses argument-list based
  process startup, escapes batch variables, and removes temporary files when a
  download or validation step is cancelled or fails.
- Contract tests cover both boundary classes so the fixes remain regression
  protected.

## Save Editor Acceptance

- `bank.money_account` and `economy.experience_points` are resolved through
  the BSII schema rather than byte-pattern guesses.
- Writes require a unique structure/name/type/size match, reject invalid
  level or experience values, preserve plain versus ScsC storage, and verify
  the resulting value after atomic replacement.
- A failed post-write read or value check restores the pre-edit backup.
- Rust numeric-field fixture coverage verifies payload offsets and typed
  values; ScsC tests cover declared-size mismatch rejection.

## Known Non-Blocking Limitations

- A local MSVC `link.exe` is not installed in the default shell. The supported
  build path uses the GNU Rust target and succeeds through `build-dotnet.bat`.
- The optional screenshot-level desktop inspection is not part of the
  automated acceptance run; frontend build and command-level checks are used
  for acceptance.
- The Rust archive slice remains dependency-light. Proprietary archive formats
  continue through the managed external-tool adapter where required.
- The cached GNU Rust toolchain now includes `rustfmt`. Its linker emits a
  non-fatal `.drectve` warning when building the Windows Tauri target.
- `build-tauri.bat`, `test-tauri.bat`, and `build-dotnet.bat` share the cached
  GNU/MinGW toolchain when it is available; the Tauri test path additionally
  disables the desktop feature so WebView2 native loading cannot mask backend
  test results.
