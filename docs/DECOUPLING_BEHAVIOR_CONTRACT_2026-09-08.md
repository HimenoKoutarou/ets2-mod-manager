# ETS2 Mod Manager 解耦行为契约

更新时间：2026-09-08  
用途：作为 Python/Qt 迁移到 C#/.NET + Rust Core 前的功能基线。

这份文档冻结“必须保持的外部行为”，不是新的实现方案。迁移期间允许替换语言、框架和内部结构，但每条契约都必须由测试或真实样本验证。

## 1. 优先级与 active_mods

| ID | 契约 | 验收标准 |
|---|---|---|
| PRI-001 | `profile.sii` 的 `active_mods[]` 使用游戏加载顺序 | 第 0 项先加载，后面的项覆盖前面的同路径内容 |
| PRI-002 | UI 工作列表使用高优先级在上 | UI 第 0 项必须对应 Profile 列表的最后一项 |
| PRI-003 | 保存时必须把 UI 顺序反转回 Profile 顺序 | `profile -> UI -> profile` 顺序往返不变 |
| PRI-004 | Workshop、旧版 Workshop、`package|title`、`_local`、`_copyN` 等别名必须解析为同一个 Mod | 不能因为显示名或后缀不同而产生重复行或“找不到 Mod” |
| PRI-005 | 未启用 Mod 不得影响已启用 Mod 的相对顺序 | 启用、禁用、排序后 disabled 区仍与 enabled 区分离 |

## 2. Profile 读写

| ID | 契约 | 验收标准 |
|---|---|---|
| PROF-001 | Local Profile 可读写，Steam/Cloud Profile 只读 | 所有写入口都在服务边界拒绝非 local |
| PROF-002 | 修改 Profile 前游戏必须完全退出 | UI、Application Service、Repository 直调都必须执行同一检查 |
| PROF-003 | 写入前创建备份，写入使用临时文件后原子替换 | 写入中断不能留下半个 `profile.sii` |
| PROF-004 | 写入后缓存必须失效或更新 | 重新读取必须看到新 active_mods、名称和计数 |
| PROF-005 | Profile 名称、公司名、active_mods 的转义必须保持 SII 兼容 | 中文、引号、反斜杠和空列表都必须往返一致 |

## 3. 存档编辑

| ID | 契约 | 验收标准 |
|---|---|---|
| SAVE-001 | 只允许编辑 Local Profile | Cloud/Steam 只能查看 |
| SAVE-002 | 游戏运行时禁止任何存档写入 | 金钱、经验、等级、维修、加油、解锁、改名、复制设置、复制槽位都拒绝 |
| SAVE-003 | 每次写入前备份并原子替换 `game.sii` | 加密文件损坏或写入失败时原文件仍可恢复 |
| SAVE-004 | 等级修改必须同时更新等级字段和对应 XP | 操作成功后重新读取的等级和 XP 都符合目标值 |
| SAVE-005 | BSII 字段解析必须依据类型和边界 | 不能通过猜测相邻字节修改 schema 或其他字段 |
| SAVE-006 | 单次复合操作必须具备一致性 | 例如复制 active_mods 和 controls 时，不允许只成功一半却报告整体成功 |

## 4. 崩溃诊断与启动监控

| ID | 契约 | 验收标准 |
|---|---|---|
| CRASH-001 | 预检必须先规范化 Profile Mod alias | `package|title`、Workshop 旧键和扫描结果能正确匹配 |
| CRASH-002 | 非零退出码不能单独判定崩溃 | 必须结合新生成/更新的 crash log 或明确崩溃证据 |
| CRASH-003 | 诊断优先级必须与 UI 优先级一致 | 最高优先级 Mod 显示为 0，不能因存档顺序反转而错位 |
| CRASH-004 | Profile roundtrip 检查必须使用统一的 Profile 路径字段 | 不允许因字段名不一致而静默跳过检查 |
| CRASH-005 | 启动监控关闭时不能遗留失控的游戏进程或后台线程 | 对话框关闭后 Worker 和进程状态可确认 |

## 5. 本地化扫描

| ID | 契约 | 验收标准 |
|---|---|---|
| L10N-001 | 输入 Mod 使用 UI 的高到低优先级顺序 | 实际合并时按低到高扫描，让高优先级覆盖低优先级 |
| L10N-002 | 游戏本体/DLC 是最低基底 | Mod 定义覆盖游戏本体定义，不能反向覆盖 |
| L10N-003 | 扫描取消不会写入半成品缓存 | 取消后下次扫描不能命中不完整结果 |
| L10N-004 | 未填写翻译不得导出空 locale value | 空值项目必须保持缺失状态 |
| L10N-005 | Mod 汉化扫描必须持久化原始包快照 | 首次完整读取；后续仅对新增、删除、指纹变化的 Mod 补查，词典/Baseline/UFL 变化只重算合并 |

## 6. Symlink/Junction 与文件迁移

| ID | 契约 | 验收标准 |
|---|---|---|
| SYM-001 | source 与 target 不能相同，也不能互为祖先/后代 | 在创建目录前拒绝嵌套路径 |
| SYM-002 | 迁移失败必须恢复操作前可用状态 | 原目录、target 文件、link 状态都可解释，不能只恢复空目录 |
| SYM-003 | 相同文件去重也必须纳入事务记录 | 后续步骤失败时不能出现源文件消失、回滚不完整 |
| SYM-004 | Link 创建失败必须明确报告实际状态 | 不能提示“数据已恢复”但文件仍停留在 target |

## 7. 解耦后的强制依赖规则

```text
presentation -> application -> domain
                              ^
                              |
                       ports <- infrastructure
```

- `domain` 只能依赖标准库和纯数据结构。
- `application` 不能导入 Qt、`Path`、`subprocess` 或具体外部工具。
- `infrastructure` 实现文件系统、注册表、Steam、进程、压缩包和 Win32 适配器。
- `presentation` 只调用 Application Use Case，不直接修改 Profile 或存档文件。
- 所有长任务必须可取消，并通过 DTO/事件向 UI 报告进度。

## 8. 当前基线状态

### 已有通过项

- 优先级顺序往返测试。
- Local/Cloud 写入边界测试。
- 原子写入和备份基础测试。
- SII 解析、汉化覆盖顺序、分类操作和 Symlink 回滚注入测试。

### 解耦前必须先修复或明确标记的已知缺口

- Crash 预检没有规范化 `package|title` alias。
- 存档编辑部分写入口没有统一的游戏运行检查。
- 等级字段解析当前为空实现。
- “全部解锁”成功条件不准确，经销商功能当前不可用。
- Crash roundtrip 检查读取了错误的 Profile 字段。
- Symlink 嵌套路径和失败回滚仍有数据状态问题。

## 9. 迁移验收门槛

每个功能迁移到新技术栈时必须同时满足：

1. 原有行为契约测试通过。
2. 真实 Profile、真实 Mod 包和真实日志样本通过。
3. 失败路径测试通过，尤其是备份、取消、进程运行和跨卷移动。
4. UI 不再直接调用文件系统或外部进程。
5. 新旧实现可在同一批样本上对比输出，差异必须有记录。
