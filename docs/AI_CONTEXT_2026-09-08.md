# ETS2ModManager 外部上下文

## 当前阶段

核心 Mod 管理重构 M3 已完成：Mod 管理的工作列表构建、批量启停、单项/分类排序、预设、Profile 顺序转换和 Crash 回查重建统一经过 `application.mod_management_use_cases.ModManagementUseCases`。

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

## 本阶段约束

- 不清理、回退或覆盖用户已有未提交修改。
- 只读解析和测试通过后单独提交本阶段相关文件。
- 不 push、不打包、不发布。
- Cloud/Steam Profile 只读；Profile/存档写入前必须保证游戏关闭。

## 已完成提交

- `fe2188c`：新增只读 BSII parser、真实存档 golden 测试和外部上下文文档。
- `2976a98`：结构化金额/经验/等级读写及安全回环测试。
- `b97ee53`：核心 Mod 管理 M1 的统一身份 alias、优先级重建规则和 UI 查找复用。
- 当前提交：核心 Mod 管理 M2 的 `ModCatalog` 扫描去重、强身份索引和扫描器集成。

## 后续方向

1. M4：把 Profile active_mods 读写接入 Application repository/port，UI 保存入口只提交 DTO，不直接依赖 `ProfileService`。
2. M5：将 `PriorityService` 的纯排序算法下沉到 Domain，Application 仅编排命令和结果。
3. M6：补齐扫描、分类和 Crash 诊断的 DTO/事件边界，再评估 C#/.NET + Rust Core 的替换顺序。
