# ETS2ModManager 外部上下文

## 当前阶段

当前已完成 **阶段 6/6：完整回归与发布验收**。Tauri 2 +
React/TypeScript 是生产客户端；旧 C#/.NET 10 + WPF 与 Python/PySide6
仅作为兼容和 parity 参考，不再由 `start.bat` 启动。核心 Mod 管理、
本地存档列表、存档数值编辑和发布构建均已验收；后续只剩未纳入当前
核心范围的辅助功能 parity 或性能增强。

核心 Mod 管理重构 M10 已完成：M5 的 Mod 管理、M6 的扫描/分类/Crash DTO 边界、M7 的 Profile 生命周期 facade、M8 的跨实现 golden fixtures、M9 的 Rust/C# 迁移骨架和 M10 的功能回归/整体 Review 均已完成。Python/PySide6 与旧 WPF 客户端保留为迁移期兼容实现。

M1 已完成：Mod 身份 alias、Profile/UI 顺序转换和工作列表重建统一走 Domain 规则。

M2 已完成：`ModCatalog` 统一扫描去重、本地优先和 Workshop 重复项挂载。

BSII 只读解析和金钱/经验/等级的结构化读写已完成；磨损、燃油和解锁功能不属于当前 Mod 管理主线。

## 已验证事实

- 真实 `game.sii` 解密后为 BSII version 3。
- 当前样本包含 46 个结构定义和 35,687 个对象。
- `economy.experience_points` 是 type `0x27`，UInt32，当前值 `279375`。
- `bank.money_account` 是 type `0x31`，Int64，当前值 `1253729`。
- `vehicle.engine_wear` 是 type `0x05`，float32；车辆磨损字段可以通过结构定义和对象边界定位。
- 字段 offset/size 指向字段值载荷，不指向字段名或类型字节。
- `SaveEditorService` 已使用解析器执行金额、经验和等级操作，并缓存解析后的 BSII 文档。
- 等级没有独立持久化字段，设置等级时写入对应的 `economy.experience_points`。
- `PriorityService`、主界面 Mod 表格索引和 Crash 禁用回查已复用 `domain.mod_identity`，不再各自实现 alias 解析。
- UI 的 Mod 管理动作通过 `_TableDataMixin._get_mod_management_use_cases()` 获取 Application facade；扫描替换 `PriorityService` 后会按实例身份自动刷新 facade。
- Application facade 不依赖 Qt、Path 或文件系统；当前由 `PriorityService` 作为兼容性的 priority port，后续可替换为纯 Domain/其他语言实现。
- UI 的 active_mods 读取通过 `_TableDataMixin._get_profile_use_cases().read_active_mods()`，保存通过 `replace_active_mods()`；`ProfileService` 同时实现 repository 和 `GameState.is_running()` port。
- Local/Cloud 写入边界、游戏运行检查、备份、原子替换和写后校验仍由现有 `ProfileService` 兼容适配器执行，Application 层不复制文件格式逻辑。
- `domain.mod_priority_rules` 不依赖 Qt、Path、文件系统或 Service；Mod 元数据通过 resolver callback 注入，支持未来替换为 C#/.NET 或 Rust Core。
- `PriorityService` 的构建、启停、移动、预设和顺序转换方法保留为兼容薄包装；其主要职责收缩为 Mod alias 解析和分类索引缓存。
- `application.contracts` 定义 `ModScanResult`/`ModScanSnapshot`、`CategoryState`/`CategoryMutationResult`、`ProgressEvent`/`CancellationEvent` 和 Crash 诊断 DTO；这些类型只使用普通 Python 值，作为未来 C# / Rust 边界的迁移契约。
- `application.mod_scan_use_cases.ModScanUseCases` 将旧 `ModScanner.scan()` 的 `(mods, new_ids)` 归一化为 `ModScanResult`，支持预取消检查；快速扫描 Worker 通过 `result_dto_ready` 向 UI 发 DTO，同时保留旧 `result_ready(list, list)` 兼容信号。
- `application.category_use_cases.CategoryUseCases` 包装 `category_service`，提供分类快照、文件夹变更结果和批量归类操作；主窗口分类树、归类、文件夹管理和快速扫描分类回填已优先走 facade，旧 Service API 保留。
- `application.crash_diagnosis_use_cases.CrashDiagnosisUseCases` 包装 Crash 发现、预检和日志分析；`services.crash_service` 重新导出 `application.contracts` 中的 Crash DTO/枚举，旧导入路径继续有效；CrashCheckDialog 通过 facade 调用。
- `application.profile_lifecycle_use_cases.ProfileLifecycleUseCases` 包装 Profile 备份、复制、删除、重命名和设置复制；主窗口 Profile 菜单和 SaveEditorDialog 的 Profile 级操作通过 facade，文件格式细节仍由 Service Adapter 负责。
- `tests/golden/migration_contracts_v1.json` 固化 Mod alias、active_mods 顺序、工作列表变换和 DTO transport shape；Python 测试、未来 C# ContractTests 和 Rust 测试共用该 fixture。
- `src/core/rust` 已有 `archive_core`、`bsii_core`、`mod_scanner` 和 `ets2_core_ffi` workspace；C ABI 只暴露 byte/string buffer、DTO JSON、错误码和显式 `ets2_core_free_buffer`。
- `src/dotnet` 已有 Contracts/Domain/Application/Infrastructure/WpfClient/ModScanWorker/ContractTests 分层骨架，WPF 使用 CommunityToolkit.Mvvm，SQLite schema 使用 WAL；本轮已用临时安装的 .NET SDK 10.0.400 完成 Release 构建，C# ContractTests 已通过。
- M10 验证：Python `unittest discover` 通过 62 个测试；所有带 `__main__` 的回归脚本在 UTF-8 环境下通过，R14 为 121/121；Profile 转义字段和组合解锁成功条件已补回归测试。pytest 可收集 122 项，但 Qt 测试在 pytest 运行器下异常退出，直接脚本运行同批 UI 测试通过。
- M10 修复：Profile 重命名字段匹配支持 SII 转义引号/反斜杠；“全部解锁”只有经销商和车库两项都成功才报告成功；无 Profile 的 SaveEditorDialog UI 冒烟初始化保持可用。
- M10 修复：Profile 设置复制先写 controls、再写 active_mods，并在任一步失败时恢复旧状态，覆盖复合操作部分成功风险。
- M10 编译验证（历史环境）：Rust 1.98.1 `cargo check --workspace` 和
  `cargo fmt --check` 通过；当时 Rust 单元测试受本机缺少 MSVC
  `link.exe` 影响，尚未链接执行。当前验收已改用 GNU/MinGW 工具链并可执行。
- M10 C# 修复：WPF `App` 类型引用、ContractTests 项目引用和比较函数已修正；C# Domain 已补齐 golden 中的 batch disable、move bottom 和 DTO shape 校验。

## 本阶段约束

- 不清理、回退或覆盖用户已有未提交修改。
- 只读解析和测试通过后单独提交本阶段相关文件。
- 不 push、不打包、不发布。
- Cloud/Steam Profile 只读；Profile/存档写入前必须保证游戏关闭。

## 目标技术栈决策（迁移总约束）

- 桌面应用与 UI：Tauri 2 + React/TypeScript + Vite。
- 兼容 Application/参考实现：C#/.NET 10 + WPF + MVVM（CommunityToolkit.Mvvm）。
- Domain 规则：先保持纯 Python 规则可验证，迁移目标为 C# Domain；不得依赖 Qt、Path、进程或具体文件系统。
- 高吞吐核心：Rust，优先承载 Mod 扫描、ZIP/SCS/HashFS 读取和 BSII 二进制解析，通过稳定 DTO/C ABI 与 C# 通信。
- Profile 文本读写、业务事务、备份、游戏关闭检查：C# Application + Infrastructure。
- Windows 特有能力：集中在 C# Win32 Adapter（Process、Symlink/Junction、注册表）。
- Mod/Profile 索引缓存：SQLite；用户配置继续使用 JSON。
- 迁移顺序：先纯业务规则，再 Profile/存档基础设施，再 Rust 核心，最后
  Tauri UI；Python 与 WPF 仅作为迁移期工具和回归脚本。
- Rust/C# 边界第一阶段只允许 UTF-8 字符串、路径、`byte[]`、DTO 和错误码，不暴露复杂对象所有权。

## 已完成提交

- `fe2188c`：新增只读 BSII parser、真实存档 golden 测试和外部上下文文档。
- `2976a98`：结构化金额/经验/等级读写及安全回环测试。
- `b97ee53`：核心 Mod 管理 M1 的统一身份 alias、优先级重建规则和 UI 查找复用。
- `a3bd992`：核心 Mod 管理 M3 的 Mod 操作 Application facade。
- `6d50f8e`：核心 Mod 管理 M4 的 Profile active_mods Application facade。
- 当前提交：核心 Mod 管理 M5 的纯优先级 Domain 规则和 Service 兼容包装。

## 后续方向

1. 用安装好的 .NET 10 SDK 编译 `src/dotnet/ETS2ModManager.sln`，接入真实 Profile Repository 和 Rust DLL。
2. 在 Rust 中替换 `archive_core`/`bsii_core` 的原型函数，先以 golden fixtures 对比 Python 结果，再切换默认实现。
3. 逐页迁移 WPF UI；Python/PySide6 在每个功能通过 golden/e2e 回归前继续保留。

## 2026-09-10 continuation state

- The production migration is now in stage 5/6: switch the production entry
  point to Tauri and retire the old WPF launch path. Stage 6/6 is the final
  regression and release acceptance pass.
- `IExternalArchiveService.ExtractTreeAsync` is implemented by
  `ExternalArchiveService` and covered by contract tests. Directories and
  readable ZIP files are copied without invoking external tools; SCS#/HashFS
  uses Extractor, and AEM/encrypted ZIP uses SXC. Cancellation kills the
  process tree and temporary directories are removed.
- `FileLocalizationService` accepts the shared archive adapter, scans
  proprietary packages through extracted `def` and target-locale trees, and
  merges locale/definition records by key. The first package is treated as
  highest UI priority; a high-priority blank locale value remains blank.
- Legacy WPF `MainWindow` creates one archive adapter and shares it with the
  localization service, Mod scanner, and Tools page.
- Legacy verification on September 10, 2026: .NET Release build is 0 warnings
  and 0 errors; C# migration ContractTests pass; Python regression suite is
  64/64. The Tauri frontend production build passes; `build-tauri.bat` and
  `start.bat` are the production build/launch path.
- Do not reset or clean the dirty worktree. Generated archives, caches, and
  old Python compatibility changes remain outside the focused migration
  commits.

## 2026-09-10 localization incremental-cache continuation

- The .NET localization path now persists raw per-package entries in SQLite via
  `SqliteLocalizationIndex`; the existing Mod index and localization snapshots
  share the application database but have separate tables and responsibilities.
- `FileLocalizationService` no longer stores resolved dictionary/Baseline/UFL
  values in the package snapshot. It stores raw locale/definition components,
  then reapplies the current resolution and priority merge on every scan.
- Cache identity includes normalized package path, target locale, package type,
  package fingerprint, and parser scan version. Directory fingerprints include
  relative file names, sizes, and timestamps so nested add/delete/modify cases
  invalidate correctly; archive fingerprints use file size and last-write time.
- Package snapshots are written only after all requested packages finish reading.
  Cancellation therefore cannot publish a partial localization cache.
- WPF now constructs the shared localization index against `paths.DatabasePath`.
  A cache hit reports `Using cached localization`; dictionary, Baseline, and UFL
  changes reuse raw package data and only rerun the merge layer.
- Contract coverage now includes unchanged cache hits, dictionary-only remerge,
  modified package invalidation, added package inclusion, and removed package
  exclusion. ContractTests build and run successfully with the temporary .NET
  10.0.400 SDK (0 warnings, 0 errors; `Migration contract v1 passed.`).
- Final continuation verification on September 10, 2026: Python unittest
  discovery remains 64/64; WPF and ContractTests Release builds are 0 warnings
  and 0 errors. Rust `cargo check --workspace` passes. The historical MSVC
  linker limitation is covered by the cached GNU/MinGW toolchain; no Rust test
  assertion is blocked.

## 2026-09-10 Tauri entry-point continuation

- `start.bat` now launches `src/frontend/src-tauri/target/release/ets2-mod-manager.exe`.
- `build-tauri.bat` runs the React build and `npm run desktop:build`, using the
  temporary Rust/MinGW toolchain when it is present.
- The old `build-dotnet.bat` and `src/dotnet` tree are retained only for
  compatibility and parity testing; they are not production launch paths.
- Localization package order now follows the current Profile UI priority before
  merging cached entries, preventing alphabetical cache order from overriding
  active_mods priority.

## 2026-09-10 stage 6 acceptance continuation

- Tauri release compilation is now the default `build-tauri.bat` behavior. It
  uses `tauri build --no-bundle` so an unavailable NSIS download cannot make a
  successful desktop compile look like a code failure. Set `TAURI_BUNDLE=1`
  when an NSIS installer is explicitly required.
- Rust path discovery now checks Steam registry values, parses
  `steamapps/libraryfolders.vdf`, finds OneDrive Documents fallbacks, and
  deduplicates discovered libraries before locating Workshop, Cloud, and game
  executable paths. Every discovered Workshop content root is scanned, not
  only the first library.
- The persistent Mod index now stores a directory content fingerprint
  (relative file names, sizes, and timestamps). Directory add/delete/rename
  changes are detected even when aggregate size and latest directory timestamp
  do not change.
- Mod ordering in the Tauri UI now follows the core contract: only enabled
  Mods participate in priority movement, disabled Mods remain after the active
  block, and UI priority is reversed only at profile write time.
- Acceptance verification after these changes: Python unittest discovery
  65/65, frontend production build passed, Rust `cargo check --locked`
  passed, and `build-tauri.bat` generated
  `src/frontend/src-tauri/target/release/ets2-mod-manager.exe`. The existing
  GNU linker `.drectve` warning remains non-fatal.

## 2026-09-10 save editor acceptance continuation

- The Tauri save editor is now complete for the current scoped fields:
  `bank.money_account`, `economy.experience_points`, and derived level.
- `bsii_core::find_numeric_fields` walks the schema and returns exact payload
  offsets for UInt32/Int64 fields. A constructed BSII fixture verifies values,
  structure names, type IDs, sizes, and offsets.
- Tauri save mutation rejects ambiguous or mismatched fields, validates level
  1-200 and UInt32 experience bounds, preserves plain vs ScsC storage, backs
  up before writing, atomically replaces the file, and reads the result back
  for verification.
- ScsC decoding now checks the header's expected decompressed size. The
  frontend uses independent drafts for the three numeric inputs, clears drafts
  when the selected save changes, highlights the selected save, and ignores
  stale async reads after a rapid selection change.
- The fixture backend has real fixture URIs and mutable in-memory values so
  browser previews exercise the same interaction path as the Tauri backend.
- Final verification: `npm run build` passed; Rust workspace tests passed
  (10 tests); Tauri `cargo check --locked` passed; Python tests passed 65/65;
  and `cmd /c build-tauri.bat` produced
  `src/frontend/src-tauri/target/release/ets2-mod-manager.exe`.
- The cached GNU toolchain now includes `rustfmt`; its linker `.drectve`
  warning is non-fatal.

## 2026-09-10 toolchain completion

- The cached GNU Rust toolchain at `%TEMP%\ets2modmanager-toolchain` now has
  `cargo`, `rustc`, `rustfmt`, `rust-mingw`, and the `x86_64-pc-windows-gnu`
  standard library. MinGW is expected at `C:\mingw64`.
- `build-tauri.bat` validates `rustup.exe` from
  `%TEMP%\ets2modmanager-toolchain\cargo\bin`, sets `RUSTUP_HOME`,
  `CARGO_HOME`, `RUSTUP_TOOLCHAIN`, and prepends the GNU and MinGW paths.
- Tauri's `desktop` feature is isolated from backend tests. `test-tauri.bat`
  runs `cargo fmt --check` and 12 Tauri backend tests with
  `--no-default-features`, so WebView2Loader is not loaded by the test
  process. The default feature path remains the full Tauri/Wry desktop build.
- Toolchain verification on September 10, 2026: Tauri backend tests 12/12,
  Rust workspace tests 10/10, Python tests 65/65, frontend build passed,
  Tauri `cargo check --locked` passed, and the release executable was
  regenerated successfully. The existing GNU linker `.drectve` warning is
  non-fatal.

## 2026-09-12 startup progress

- User wants an EXE after each build, plus commits; do not generate an installer
  unless explicitly requested. Release entry point suppresses the console.
- The former 52% was a frontend constant, not actual scan progress. Initialization
  now runs on `tauri::async_runtime::spawn_blocking` and emits
  `ets2-scan-progress` to the initializer before directory traversal or manifest
  extraction. Payload: phase, completed count, stage total, Mod name and path.
- `InitializerWindow.tsx` displays the current package, full scrollable path,
  elapsed time for the current operation and stage-based counts. Unknown totals,
  database commit and main-window loading are indeterminate, not fake percentages.
- SQLite incremental index semantics are unchanged. Regression tests exercise
  closing/reopening the database, unchanged-cache reuse, changed files, removal,
  pre-read notifications and failed writes without completion.
- Main window repeats its readiness handshake to avoid missed events and only
  shows after its persisted data load succeeds. Scan errors remain visible with
  retry. React StrictMode cannot start duplicate native initialization jobs.
- Frontend smoke test: `src/frontend/tests/startup-smoke.mjs`. It uses Playwright
  (optional PLAYWRIGHT_MODULE_PATH / PLAYWRIGHT_CHANNEL environment variables),
  runs a temporary Vite server, mocks native IPC, and covers three locales,
  narrow/normal layouts, progress/path/counts, ticking elapsed time, retry and
  late main readiness. Screenshots are in ignored frontend/build/startup-smoke.
- At build time the user's old EXE was running and locked WebView2Loader.dll.
  Do not kill it or overwrite its loaded files. The alternate build uses
  CARGO_TARGET_DIR=F:\ETS2ModManager\src\frontend\src-tauri\target\startup-progress;
  new output is that directory's release/ets2-mod-manager.exe.
- Verification: 19 backend tests passed; frontend TypeScript/production build
  passed; Playwright mocked-IPC smoke passed in zh-CN/en-US/ru-RU; alternate
  Release EXE built successfully with WebView2Loader.dll and PE GUI subsystem 2.
  The real user's Mod library was not rescanned by the tests. The batch file's
  final echo still uses its default path; use the actual Cargo output above.

## 2026-09-12 per-row Mod previews

- Mod list rows now use `ModThumbnail`: IntersectionObserver requests artwork for
  visible/nearby rows without requiring selection. Preview takes priority over
  the icon; broken previews fall back to icons, then to the existing placeholder.
  Thumbnails stay 34x34 and move with their Mod during sorting.
- `modMedia.ts` deduplicates in-flight requests and limits native image loading
  to two concurrent requests. The detail panel shares this loader. Missing or
  failed images cannot trigger an automatic request loop in the current catalog.
- Native media commands now use a blocking worker instead of the UI thread.
  SQLite `mod_media_cache` retains image data URLs / preview URL metadata by
  indexed package path, size, timestamp and directory fingerprint. Unchanged
  positive results are reused; negative results expire after 24h. Index cleanup
  removes cache records for removed Mods. Remote image URLs still need browser
  network/cache access; missing artwork remains a placeholder.
- ZIP media lookup reads the manifest entry directly instead of reading the
  whole ZIP. Directory manifests are parsed for custom icon filenames.
- Verification so far: 21 Rust tests passed; TypeScript passed; Playwright
  `tests/mod-media-smoke.mjs` passed visible/scroll loading, concurrency, duplicate
  prevention, preview priority, fallback, missing art and priority reordering.
- Previous default and startup-progress EXEs remain running on the user's
  machine. New build target: src/frontend/src-tauri/target/mod-thumbnails/release.
  Do not tell the user that the older output paths have been updated.
- Final verification: production frontend and Release EXE built successfully;
  the startup smoke test passed after a browser-navigation timeout on its first
  run. Tests use isolated fixtures/mocked IPC, not the user's real Mod library.

## 2026-09-12 restored Mod directory relocation

- User reported that the legacy change-Mod-path feature was missing. Confirmed
  that this means copying/migrating local Mods and redirecting the game's `mod`
  directory with a Junction, not merely changing a scanner setting.
- Added the top-bar Mod directory dialog, localized in Chinese, English and
  Russian: actual/game path, native folder chooser, relocation, restore, broken
  link repair and interrupted-operation recovery. Commands require confirmation.
  Unsaved Mod order/enabled changes block migration. Busy operations prevent
  modal/window closure and report the current file plus byte progress.
- Native implementation: `src/frontend/src-tauri/src/mod_directory.rs`. Uses
  junction 2.0.0 and rfd 0.15.4, background workers, exclusive OS file locks,
  a persisted operation journal, SHA-1 copy verification and source-change
  detection. No Python, PowerShell or cmd subprocess for directory operations.
  Game-process checks run hidden and fail closed; checks occur before copying
  and immediately before switching directories.
- Scope/constraints: local Mods only; Workshop locations remain unchanged.
  Relocation targets must be empty or absent with an existing parent. Nested
  reparse points and overlapping source/target paths are rejected. Originals
  are retained as backups (shown in the success result), not automatically
  deleted; this is explicitly disclosed in confirmation text. Restore likewise
  retains the former target's contents. No automatic space reclamation.
- Persistence: actual location comes from the filesystem link, not a transient
  frontend setting. SQLite package/media/localization paths are remapped in a
  transaction, with rollback support. File AND directory timestamps are kept.
  Relocation/restore reload the cached catalog without rescanning; repairing a
  link performs an incremental scan because it may point to different Mods.
- Scan, media and localization work take shared directory locks; new application
  instances cannot launch the game or migrate during an exclusive operation.
  Interrupted initialization opens the cached workspace so recovery is reachable.
  Older already-running EXEs do not participate in these new locks.
- Verification: 29 Rust tests passed, including real Windows Junction
  migrate/relocate/restore/repair, overlap/conflict/nested-link refusal, rollback,
  interrupted recovery, cache remapping and zero changed/inspected packages
  after relocation. Tests use unique temporary fixtures, never user Mods.
  Cross-volume hardware faults and forced machine power loss were not exercised.
- TypeScript and production frontend passed. Playwright mocked-IPC directory
  smoke passed all three languages, folder selection, confirmation, progress,
  persistence, restore/repair/recovery, native failure reporting and dirty-Mod
  protection. Existing preview/reorder and initializer smoke tests also passed.
- Release EXE built at 2026-09-12 17:48:
  `src/frontend/src-tauri/target/mod-thumbnails/release/ets2-mod-manager.exe`
  (28,260,015 bytes), alongside WebView2Loader.dll. No installer was built.
  The user still had old default-target EXEs running; those were not terminated.
  `start.bat` and the final build script echo still reference the old default
  target. Give the explicit mod-thumbnails executable path when handing off.

## 2026-09-12 restored categories and batch Mod management

- User reported missing custom categorization and batch enable/disable/movement.
  Compared the legacy category service, category tree handlers and
  `domain/mod_priority_rules.py` before restoring the behavior.
- Native category storage now lives in SQLite, separate from the disposable
  package index: category_folder, category_assignment, category_import.
  Background `category_list` and `category_mutate` commands support create,
  rename, delete and atomic bulk assignment. Deleting a category only makes its
  Mods uncategorized; no Mod files or Profile enable flags are changed.
- Imports legacy assets/cache/user_folders.json and known_mods.json, plus the
  .NET LocalAppData/ETS2ModManager/categories.json format, once. Deleted legacy
  folders are not resurrected from stale known_mods records. JSON sources are
  read only. Invalid legacy JSON produces a sidebar warning without blocking
  the workspace or marking the import complete.
- Assignments use stable case-normalized Mod identity, accepting legacy archive
  extensions and Workshop package hex aliases. Copy suffixes are preserved.
  Rescans, directory relocation and temporarily removed packages do not erase
  manual assignments. Snapshot responses map assignments back to current IDs.
- Frontend: CategorySidebar provides folder CRUD, counts, mixed-state category
  enable checkboxes, right-click/action menus and drop-to-category assignment.
  Added a selection checkbox column, Ctrl/Meta selection, Shift range selection,
  select-filtered-list and clear-selection controls.
- Batch controls explicitly choose Selected Mods / Current filter / Whole
  category. Scope counts are visible. Enable, disable, invert and category
  assignment act only on that scope. Category-scope actions intentionally include
  category members hidden by the current search or enabled-only view.
- `modBatch.ts` ports the legacy stable block movement contract: selected enabled
  Mods keep relative order when moved up/down/top/bottom or dragged before another
  row. Disabled Mods are not priority-moved. Steps support 1/10/50/100.
  Enabled/order edits remain drafts until Save Profile; categories persist
  separately and cannot clear unsaved sorting. Read-only Profiles block batch
  enable/order edits. Scanning and category mutations are mutually excluded in
  the UI store to avoid stale scan results replacing category feedback.
- New controls and errors have Chinese/English/Russian copy. Screenshots exposed
  a narrow-window Russian header overlap; widened the enabled column and added
  a text-boundary assertion. All-selection validation and category grouping use
  single-pass sets/maps rather than repeated full-catalog searches.
- Verification: 34 Rust tests passed; `tests/mod-batch-rules.mjs` passed explicit
  boundaries and 1,024 subset movement cases. New Playwright
  `tests/mod-categories-smoke.mjs` covers CRUD, multi/range selection, drag
  classification, restart persistence, stable sorting, reversed active_mods
  serialization, scoped toggles, hidden category members, failed writes,
  read-only protection and three languages. Existing preview/reorder, directory
  migration and startup smoke suites passed with the new category IPC mocked.
  Rust tests use temporary SQLite fixtures and frontend tests mock native IPC;
  the user's real Profile, Mod files and old category files were not modified.
- Native layer committed separately as 78bc502. Release builds continue to use
  `src/frontend/src-tauri/target/mod-thumbnails/release/ets2-mod-manager.exe`,
  with WebView2Loader.dll beside it; no installer. Older default-target EXEs are
  still running and were not killed. The default start.bat remains an old target.

## 2026-09-12 startup incremental CRUD scan

- Startup initialization now uses `startup_incremental_scan` instead of always
  deep-reading every package. It enumerates only direct children of the local
  Mod root and Workshop roots, reading path/type/size/modified time.
- Existing packages whose shallow header is unchanged are loaded directly from
  `mod_package_v2`; new, removed, or changed entries are reconciled through the
  normal index transaction. Manual `mod_scan` remains a complete deep scan.
- Added persistent `mod_package_header_state(path,size,modified_ms,is_directory)`
  storage. Header state is updated transactionally with the package index and
  stale rows are removed with deleted packages.
- Directory package headers deliberately store root-directory metadata, while
  the package index may continue to store the recursive directory signature.
  This prevents unchanged directory Mods from being rescanned on every launch.
- The initializer continues to report `cache`, `cached`, `local`, `workshop`,
  `metadata`, `persist` and `complete` phases, including the concrete package
  name/path for deep reads.

## 2026-09-13 media fallback and fixed details panel

- Mod media resolution now falls back to the bundled external extractor for
  non-ZIP SCS/HashFS packages. The extractor listing is filtered for image
  extensions, prioritizes icon/preview/thumb/cover/logo/banner names, extracts
  up to 24 candidates in one call, and returns the first valid image as a data
  URL. Child extractor processes are hidden on Windows.
- Media cache schema remains backward compatible: the original seven-column
  `mod_media_cache` table is unchanged. `mod_media_cache_meta` stores a media
  resolver version so older negative cache entries are retried after resolver
  improvements. Stale metadata is removed and directory relocation remaps it.
- The desktop shell is now fixed to the viewport (`height: 100vh`,
  `overflow: hidden`). The Mod table, sidebar and right details panel scroll
  independently; scrolling the Mod list no longer moves the details panel out
  of view.
- Verification on 2026-09-13: Rust backend 34 tests passed, TypeScript build
  and Vite production build passed. The bundled extractor successfully listed
  `/imagen.jpg` from a real local HashFS SCS package.
