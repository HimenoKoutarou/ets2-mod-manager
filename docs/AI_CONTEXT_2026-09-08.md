# ETS2ModManager 外部上下文

## 当前阶段

核心 Mod 管理重构 M10 已完成：M5 的 Mod 管理、M6 的扫描/分类/Crash DTO 边界、M7 的 Profile 生命周期 facade、M8 的跨实现 golden fixtures、M9 的 Rust/C# 迁移骨架和 M10 的功能回归/整体 Review 均已完成。Python/PySide6 仍是当前可运行实现；C#/.NET 10 + Rust Core 进入并行迁移阶段。

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
- M10 编译验证：Rust 1.98.1 `cargo check --workspace` 和 `cargo fmt --check` 通过；Rust 单元测试仍需要本机 MSVC `link.exe`，当前机器未安装 Visual C++ Build Tools，因此未能链接执行。
- M10 C# 修复：WPF `App` 类型引用、ContractTests 项目引用和比较函数已修正；C# Domain 已补齐 golden 中的 batch disable、move bottom 和 DTO shape 校验。

## 本阶段约束

- 不清理、回退或覆盖用户已有未提交修改。
- 只读解析和测试通过后单独提交本阶段相关文件。
- 不 push、不打包、不发布。
- Cloud/Steam Profile 只读；Profile/存档写入前必须保证游戏关闭。

## 目标技术栈决策（迁移总约束）

- 桌面应用与 Application 层：C#/.NET 10 + WPF + MVVM（CommunityToolkit.Mvvm）。
- Domain 规则：先保持纯 Python 规则可验证，迁移目标为 C# Domain；不得依赖 Qt、Path、进程或具体文件系统。
- 高吞吐核心：Rust，优先承载 Mod 扫描、ZIP/SCS/HashFS 读取和 BSII 二进制解析，通过稳定 DTO/C ABI 与 C# 通信。
- Profile 文本读写、业务事务、备份、游戏关闭检查：C# Application + Infrastructure。
- Windows 特有能力：集中在 C# Win32 Adapter（Process、Symlink/Junction、注册表）。
- Mod/Profile 索引缓存：SQLite；用户配置继续使用 JSON。
- 迁移顺序：先纯业务规则，再 Profile/存档基础设施，再 Rust 核心，最后 WPF UI；Python 仅作为迁移期工具和回归脚本。
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

- The production migration is now in stage 5/5: final parity, publish, and
  acceptance.
- `IExternalArchiveService.ExtractTreeAsync` is implemented by
  `ExternalArchiveService` and covered by contract tests. Directories and
  readable ZIP files are copied without invoking external tools; SCS#/HashFS
  uses Extractor, and AEM/encrypted ZIP uses SXC. Cancellation kills the
  process tree and temporary directories are removed.
- `FileLocalizationService` accepts the shared archive adapter, scans
  proprietary packages through extracted `def` and target-locale trees, and
  merges locale/definition records by key. The first package is treated as
  highest UI priority; a high-priority blank locale value remains blank.
- WPF `MainWindow` creates one archive adapter and shares it with the
  localization service, Mod scanner, and Tools page.
- Final verification on September 10, 2026: .NET Release build is 0 warnings
  and 0 errors; C# migration ContractTests pass; Python regression suite is
  64/64; Rust fmt/check/tests pass; `build-dotnet.bat` creates a self-contained
  `win-x64` publish; the published WPF process starts with title `ETS2 Mod
  Manager` and remains responsive.
- Do not reset or clean the dirty worktree. Generated archives, caches, and
  old Python compatibility changes remain outside the focused migration
  commits.
