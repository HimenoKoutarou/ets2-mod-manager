# ETS2ModManager 外部上下文

## 当前阶段

核心 Mod 管理重构 M5 已完成：Mod 管理的工作列表构建、批量启停、单项/分类排序、预设、Profile 顺序转换和 Crash 回查重建统一经过 `application.mod_management_use_cases.ModManagementUseCases`；纯工作列表变换已下沉到 `domain.mod_priority_rules`；Profile active_mods 的读取和写入统一经过 `application.profile_use_cases.ProfileUseCases`。

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

1. M6：补齐扫描、分类和 Crash 诊断的 DTO/事件边界，再评估 C#/.NET + Rust Core 的替换顺序。
2. M7：把 Profile 名称、复制/删除等非 Mod 管理写操作拆到独立 use case，避免继续扩大 `ProfileService` 兼容层。
3. M8：对 Domain/Application 输出建立跨实现 golden fixtures，为多语言迁移提供新旧结果对比。
