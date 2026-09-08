# Migration Contract V1

`tests/golden/migration_contracts_v1.json` is the shared behavioral input for
the current Python implementation and the replacement C#/Rust components.

## Rules

- The file is UTF-8 JSON and contains only strings, numbers, booleans, arrays,
  objects, and null.
- `contract_version` is mandatory. Breaking behavior requires a new versioned
  fixture; existing fixtures stay immutable for regression testing.
- Profile `active_mods` is persisted low priority to high priority. UI order is
  the reverse: high priority to low priority.
- Alias comparison is case-insensitive and removes only the known
  `_workshop`, `_local`, and `_copyN` storage suffixes.
- Legacy `mod_workshop_package.<hex>` identifiers expose their decimal Steam
  Workshop ID as an additional alias.
- DTO enum values cross process/language boundaries as lowercase strings.
- C ABI errors cross the boundary as integer error codes plus an owned UTF-8
  message. No Rust or C# object ownership crosses the boundary.

Python verifies the file in `tests/test_m8_golden_contracts.py`. The .NET and
Rust test projects load the same file through a repository-relative linked
test asset.
