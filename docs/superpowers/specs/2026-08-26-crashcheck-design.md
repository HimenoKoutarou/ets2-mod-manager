# Round7 新功能设计规范：Mod 加载预检 & Crashlog 崩溃定位

> 起草日期：2026-08-26
> 适用代码库分支：main
> 对应代码范围：`src/services/crash_service.py`（新建）、`src/ui/crash_check_dialog.py`（新建）、`src/ui/_mw_mixins/_toolbar_mixin.py`（增量修改、约 30-40 行）、`src/ui/_mw_mixins/_table_data_mixin.py`（可选追加 `select_mod_row` 辅助方法，若已有则复用）

---

## 1. 背景与动机（Background）

ETS2 / ATS 真实启动一次全 mod 加载通常需要 **30 分钟以上**，且真实加载过程中一旦崩：

1. 用户没有中间态反馈——30 分钟过去后只看到闪退窗口 + `game.crash.txt` 纯机器码；
2. `game.crash.txt` 只有 DLL 模块列表和寄存器堆栈，**不直接列出 mod 名**，用户只能手动二分禁用定位（每次 30 分钟，十几轮小时级）；
3. 现有 mod 管理器（ETS2 Mod Manager v1.1.2-）仅能管理 active_mods[] 顺序，但无法提前指出"当前排列会闪退"。

目标是在**不开游戏、秒级~30 秒级完成**的前提下，让用户获得"接近真实启动加载"的崩溃检出率，并在已经崩溃后能把 crashlog 机器信息映射到具体 mod 列表行。

---

## 2. 目标 / 非目标（Goals / Non-Goals）

### 2.1 目标（Goals）

- **G1**：在不启动 eurotrucks2.exe 的前提下，按当前 profile 的 active_mods[] 优先级顺序（priority_index=0 最高）模拟游戏加载，识别"确定会让游戏闪退"的 mod 组合并分级告警（RED 必崩 / YELLOW 警告 / GREEN OK），300 mod 量级下 L2 深度层 **< 30 秒**完成。
- **G2**：在用户已经遭遇闪退的场景下，读取 `game.crash.txt + 同时间戳 game.log.txt`，输出**嫌疑 mod 列表（按嫌疑度 S > A > B 排序）**，每条带原日志证据，点击可直接定位主窗口对应 mod 行高亮。
- **G3**：日志目录自动发现——默认枚举 `{MyDocuments}\Euro Truck Simulator 2` 与 `{MyDocuments}\American Truck Simulator` 两个兄弟文件夹，按 `game.crash.txt` 最后修改时间最新者自动预分析；找不到再让用户手动选择 `game.crash.txt`（选中后自动联动同目录的 `game.log.txt`）。
- **G4**：用户可从预检结果一键 "禁用所有 RED 项并保存 profile"（二次确认）；从 crashlog 嫌疑列表对单个嫌疑执行 "跳到 mod / 禁用 / 下移到最底" 三快捷动作。

### 2.2 非目标（Non-Goals）

- **NG1**：不真实启动 eurotrucks2.exe / amtrucks.exe 做动态验证（30 分钟实机成本过高；启动相关 watchdog 链路作为未来可选扩展，当前不入 V1）。
- **NG2**：不做游戏运行时 mod 冲突分析（例如「两个 mod 都改同一交通密度参数，但语法合法」这类语义冲突——需要真正 def 解析引擎，不在本轮范围）。
- **NG3**：不修复崩溃本身，只定位并提供禁用/下移建议。
- **NG4**：不覆盖运行中崩溃（进存档 10 分钟后崩），仅覆盖"加载期崩溃"（定义：game.log 中没有 `[sys] Process manager shutdown.` 正常收尾行，且最后一个完整活动区间位于 mod mount 阶段或 mount 后 < 2 min 的 sii/resource 加载期内）。

---

## 3. 功能 A：Mod 加载预检引擎（L0~L3 四层静态分级）

功能入口：`crash_service.precheck_active_mods(profile, max_depth=PrecheckDepth.L2, use_l3_history=True) -> PrecheckReport`

### 3.1 数据模型

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List

class Severity(str, Enum):
    RED = "red"          # 必崩：真实加载必然闪退
    YELLOW = "yellow"    # 警告：大概率运行时异常 / 功能缺失，但可能不闪退
    GREEN = "green"      # OK：此层检查通过

class PrecheckDepth(str, Enum):
    L0_FAST = "L0"       # <1s
    L1_MED  = "L1"       # <5s
    L2_DEEP = "L2"       # <30s   默认 max_depth
    L3_HIST = "L3"       # <2s，可与 L0~L2 组合叠加

@dataclass
class PrecheckIssue:
    mod_id: str                      # 映射到 Mod.mod_id（或空串=全档级问题，如 profile 字节不一致）
    mod_display_name: str            # 展示名，未找到则留包名
    priority_index: Optional[int]    # active_mods[i] 序号；若 mod 未启用但被 active_mods 中某个项作为硬依赖缺失，则 None
    severity: Severity               # RED / YELLOW / GREEN
    layer: PrecheckDepth             # 由哪一层打出
    check_code: str                  # 如 "L0-2"（见 3.2 条编号），用于去重与测试断言
    evidence: str                    # 证据字符串（缺哪个文件 / 错误原文），尽量可直接 UI 展示
    suggestion: str                  # 建议动作（禁用 / 下移优先级 / 更新 mod / 损坏 SCS 重新下载 等）
    extra: dict = field(default_factory=dict)  # 内部用扩展字段（如缺失路径列表）

@dataclass
class PrecheckReport:
    profile_id: str
    scanned_mods: int                 # 本次扫描到 active_mods 中多少项
    total_issues: int
    red_count: int
    yellow_count: int
    issues: List[PrecheckIssue]
    elapsed_ms: int                  # 总耗时（毫秒，用于 UI 展示"深度模拟已完成，耗时 X 秒"）
```

### 3.2 四层规则（每层对应 check_code）

#### L0 快筛（L0_FAST，<1s，文件级）

| 编号 | 检查 | 结果 Severity | 说明 |
|---|---|---|---|
| L0-1 | active_mods[i] 对应 source 文件 / steam_workshop 编号目录 / 解包目录 **不存在** | RED | 直接用现有 Mod.resolve_source_exists()（若无则封装：对 Mod.kind==FILE 查文件存在，WORKSHOP 查 `{ETS2_DOCS}/steam_workshop_content/227300/{ws_id}` 或 ATS `270880/{ws_id}`，UNPACKED 查 `mod/{name}/`） |
| L0-2 | `.scs` 包头校验：能否作为 zipfile 打开并读出 namelist（不解内容） | RED | 捕获 `zipfile.BadZipFile`、`struct.error`、`EOFError`、`OSError (truncated)`；不校验 entry 数据 CRC（留到 L1） |
| L0-3 | manifest.sii 语法错 + 控制字符污染 | RED | 用现有 sii_parser 解析 manifest.sii；若解析抛异常 或 解析前文本中出现 `[\x00-\x08\x0B\x0C\x0E-\x1F]` 控制字符（允许 `\r\n\t`）= RED |
| L0-4 | 同 mod_id 重复项：active_mods[] 中出现 ≥2 个不同 package 但 `Mod.mod_id` 相同 | RED | mod_id 是 SCS 加载唯一键，重复时行为未定义且经常崩 |
| L0-5 | 解包目录型 mod 路径名非法字符：`[\:*?"<>|]` 或长于 Windows MAX_PATH-12 | RED | 解包型 mod 的子路径过深会在 hashfs 映射阶段被 ETS2 直接拒绝 |

#### L1 中检（L1_MED，<5s，hashfs 索引 + 元数据级）

复用 ScsArchiveReader 的 entry 枚举（如已实现 `iter_entries()` 或 `namelist()` 接口则直接用；无则加 `ScsArchiveReader.list_entries() -> list[str]` 只读索引不读内容）。

| 编号 | 检查 | Severity | 说明 |
|---|---|---|---|
| L1-1 | 每个 entry 名路径校验：不能出现 `../`、绝对盘符 `[A-Z]:/`、NUL 字节、非 UTF-8 字节 | RED | hashfs 规范不支持 |
| L1-2 | `compatible_versions[]` 与当前游戏 Build 版本比较 | RED / YELLOW | Build 版本从 `game.log.txt` 第 3 行 `Build: 1.52.0.6s` 提取 `1.52` 主版本；若 manifest 声明 compatible_versions 且主版本不在集合内 → RED；若仅 package_version 新旧跨代（1.49 vs 1.52 已知 break change）→ YELLOW |
| L1-3 | manifest 声明的 `icon.png` / `description.txt` 等必需字段在 entry 中不存在 | YELLOW | 显示问题，一般不崩 |
| L1-4 | 解包目录型 mod 内部嵌套的子 `.scs` 递归执行 L0 全部 + L1-1/1-3 | RED / YELLOW | 复用现有 mod_scanner._enrich_nested_fallback 的扫描思路，改为直接走当前 crash_service 递归 |
| L1-5 | `.scs` entry CRC 校验（随机抽查前 20 个 entry） | RED | 用于检测磁盘坏块导致的"文件列表能读但内容损坏"——不需要全量（慢），抽查 20 个覆盖 hashfs 索引区附近即可 |

#### L2 深度模拟（L2_DEEP，<30s，VFS 覆盖图 + 交叉引用存在性检查）

**这一层是把 30 分钟真实加载里的"头 5 秒 mod 注册 + 引用解析"用静态方式复现，覆盖 90% 以上"加载期闪退"。**

L2 算法步骤：
1. 初始化 `VFS: dict[str, tuple[int, str]]` = `{虚拟路径 -> (priority_index, 来源包名)}`；`priority_index` 越小越优先，写入时若 `priority_index < existing[0]` 才覆盖，否则跳过。
2. 按 active_mods 顺序（0→N，priority=0 最高）逐个 mod，提取所有 entry 名路径写入 VFS（entry 名自动标准化为 `/def/vehicle/xxx.sii` 小写反斜杠转正斜杠形式）。
3. 对每个 mod 的 manifest.sii 中声明的 `unit[]` 列表（或 manifest 直接读 sii 得到的 `unit_names` 集合），取每个 unit 目标路径（def 路径）、以及 unit 内 `@include` 目标、以及 `.sii` 内容中以字符串形式出现的 `.tobj` / `.mat` / `.pmg` / `.pmd` 相对引用（正则粗取，不做完整 sii 语义解析），构造引用链 `R`。
4. 对引用链 `R` 中每一项 `r`，在 VFS 中查"最终存活版本"（priority 最低的那个写入者）：
   - 若 `r` 根本不存在于 VFS → L2-1 命中（RED，给出"缺哪个文件、在哪个引用链路里"）
   - 若 `r` 存在但 "最终存活版本不是来自于期望所在 mod"，且该路径名已知是被其它 mod 覆盖（YELLOW 提醒："路径 /def/vehicle/traffic_storage.sii 被高优 mod A 覆盖，原 mod B 中该版本被遮蔽"——一般不崩但会造成"mod B 没生效"类问题）→ L2-2（YELLOW）。
5. 加密 roundtrip 一致性（profile.sii 写回后字节长度是否 ≈ 原始，或 hash 相等）→ 复用现有 profile_service.encrypt_profile_bytes 的返回值，失败则 L2-3（RED）。

L2 的 **300 mod 性能上限保证**：
- L2 最大 entry 集合上限：如某 1 mod 含 entry 数 > 200,000（极端地图包），仅取前 200,000 条（ETS2 单包物理上限本身就是 262144，取 200k 保留头部 def/automat/material 足够做交叉引用）。
- 对 300 个 mod 做 VFS 写入总复杂度 O(Σ entry) ≈ 300 × 5万 = 1500万次 dict 写入——在纯 CPython 3.13 下约 1~2 秒，远低于 30s 上限。
- 引用链解析本身不读 entry 内容（除了 manifest.sii 已读过），用正则对 entry 名字符串扫 `.tobj`/`.mat`/`.pmg`/`.pmd` 作为粗引用——时间可忽略。
- 若单轮 L2 超过 **30 秒**，内部 `time.monotonic()` 超时自动中断并返回"L2 超时，结果仅含 L0+L1"的 YELLOW 提示（L2-4，YELLOW），让 UI 能降级。

| 编号 | 检查 | Severity |
|---|---|---|
| L2-1 | 交叉引用缺失：unit / include / tobj / mat / pmg / pmd 中至少一项在 VFS 不存在 | RED |
| L2-2 | 低优 mod 路径被高优 mod 遮蔽覆盖（仅针对 manifest 声明的 unit 主 def） | YELLOW |
| L2-3 | profile.sii 加密 roundtrip 字节数与原始不一致 | RED |
| L2-4 | L2 执行超时 >30s，已降级 | YELLOW |

#### L3 历史日志免费动态（L3_HIST，<2s，不启动游戏，叠加结果）

**复用用户上次真实启动 30 分钟产生的 game.log.txt 结果做免费验证——零额外成本，却能覆盖 L2 无法覆盖的"运行时解析错误"。**

| 编号 | 检查 | Severity |
|---|---|---|
| L3-1 | 最近一次 game.log session（从 `[app] starting eurotrucks2` / `[app] starting amtrucks` 到最后，含未正常收尾 session）中出现 `[sii] invalid unit`、`could not load unit`、`[resource] missing_file`、`unit not found` 这 4 类致命错误 → 通过错误行里的 `in package 'XXX.scs'` / 错误行紧邻最近一个 `[hashfs] XXX.scs:` 成功行反推对应 mod → 在对应 mod 上叠 RED 告警 | RED |
| L3-2 | 最近一次启动**正常收尾**（出现 `[sys] Process manager shutdown.`）+ profile.mods 组合与当前完全相同 → 返回 GREEN "此前启动过完全相同组合，成功进入主菜单"（非必崩保证，但用户非常需要知道"我这次和上次成功比有没有动过 mod"） | GREEN（正面信息，不纳入 RED/YELLOW 计数） |
| L3-3 | 最近一次启动正常收尾但组合不同 → 返回差异摘要（"新增 N 个，删除 M 个，顺序调整 K 处"） | YELLOW（信息级） |

### 3.3 去重与合并

同一个 mod 若在多层均命中 RED（例如 L0-1 文件不存在 + L2-1 引用缺失），合并为一条 issue：
- severity 取最高（RED > YELLOW > GREEN，GREEN 不纳入告警列表仅纳入统计）
- check_code 取最早命中层 + 最小编号
- evidence 合并 "\n- " 为多证据列表

同一个 mod 的相同 check_code 命中多次去重 1 条。

---

## 4. 功能 B：Crashlog 解析 & 嫌疑 mod 定位

功能入口：`crash_service.analyze_crashlog(crash_txt_path, game_log_path=None, profile=None) -> CrashAnalyzeResult`

若 `game_log_path is None`：自动取 `crash_txt_path` 同目录下 `game.log.txt`；若不存在则尝试 `ETS2_DOCS/game.log.txt` 与 `ATS_DOCS/game.log.txt` 两默认目录找最近修改时间者。

### 4.1 数据模型

```python
from dataclasses import dataclass
from enum import Enum

class CrashSuspicion(str, Enum):
    S = "S"    # 确定嫌疑：日志明确写 in package 'xxx.scs' 或直接报 mod id
    A = "A"    # 高嫌疑：崩溃发生在该 mod 正在 mount 或刚 mount 完的错误区间
    B = "B"    # 一般嫌疑：崩前最后 N 个成功 mount 中（N=5）

@dataclass
class CrashSuspectMod:
    rank: int
    suspicion: CrashSuspicion
    mod_id: str
    mod_display_name: str
    priority_index: Optional[int]
    evidence_lines: List[str]      # 原日志原文（2~5 行）
    evidence_line_range: tuple[int, int]  # 在原 game.log.txt 中的行号范围

@dataclass
class CrashAnalyzeResult:
    crash_time: str                # 从 Crash log created on 字段提取，未解析则 ""
    build_version: str             # 如 "1.52.0.6s"，未提取则 ""
    exception_code: str            # 如 "C0000005 ACCESS_VIOLATION"
    fault_module_category: str     # "game_binary"（eurotrucks2/amtrucks 主模块=mod 锅高概率）/ "third_party_injector"（输入法/音效注入）/ "unknown"
    suspects: List[CrashSuspectMod]
    failed_to_match: int           # 有 S 级证据但没匹配上 mod 的次数（展示"日志显示 X.scs 有错，但当前 mod 列表未找到"）
    raw_tail_lines: List[str]      # 崩溃 session 尾部最后 30 行原文（供 UI 展示"崩溃发生时的日志"面板）
```

### 4.2 解析流程

#### Step 1：崩溃定性（game.crash.txt 头 80 行）

- `Crash log created on: (.*)` → `crash_time`
- `Build: (.*)` → `build_version`
- `Exception code: (.*)` → `exception_code`
- `Fault address: .* (.*\.dll|.*\.exe)` → 抓模块路径：
  - 主模块：`eurotrucks2.exe` / `amtrucks.exe` → `fault_module_category = "game_binary"`（90% 是 mod 锅）
  - 第三方注入模块：`SogouPY` / `Nahimic` / `RTSSHooks` / `OBS` / `fraps` 等白名单 → `fault_module_category = "third_party_injector"`（提示用户关闭注入，非 mod）
  - 其它 → `"unknown"`

#### Step 2：对齐 game.log 尾部 session（最关键）

**核心观察**：ETS2 崩溃时 game.log.txt **不会写**正常收尾行 `[sys] Process manager shutdown.`，因此：

1. 从 game.log.txt 最后一行往上找最近一个"启动标记"：
   - 正则：`^\s*\(.*\)\s*\[app\] starting (eurotrucks2|amtrucks)`（兼容版本差异，备选 `[sys] Command line` 也行）
2. 从该行到文件末尾视作 `CRASH_SESSION`：
   - 若 CRASH_SESSION 内出现 `[sys] Process manager shutdown.` → **该 session 其实是正常退出，非崩溃**——继续向上找上一个启动标记（最多找 3 个；都正常收尾则返回空 suspects + 提示"这份 game.log 对应 session 均正常退出，未记录崩溃"）。
3. 若 CRASH_SESSION 正常（无 shutdown）且长度 < 20 行 → 日志过于残破，降级为 "未匹配 session"。

#### Step 3：构造"真实加载顺序表"

对 CRASH_SESSION 区间内所有行：
- 正则 `\[hashfs\]\s+([^:]+)\.scs:\s+Created and validated` 命中按出现顺序编号 `real_mount_order: list[tuple[order_int, package_name_without_scs]]`
- 结果用 Map：`{package_name_without_scs -> order_int}` 作为 `package_name 反查 mod` 的中间桥梁

#### Step 4：提取致命错误 → S 级嫌疑

在 CRASH_SESSION 尾部最后 600 行（崩溃发生前最后 ~5 分钟活动）中，按优先级依次用正则抓 4 类行：

| 规则 | 正则 | 命中后动作 |
|---|---|---|
| B-1 | `in package\s+'([^']+\.scs)'` | 直接把包名 → 对应 mod（先精确匹配 file_name，再匹配 display_title 中的包名子串，再用 active_mods 对应 package_name）→ **S 级**，记录该行 + 上下文 2 行 |
| B-2 | `\[sii\]\s+invalid unit\s+'([^']+)'` | 取 unit 路径，在 L2 风格 VFS 中查"该 unit 来自哪个包" → S 级（需先跑 3.3 L2 简版 VFS 构建，仅 unit/def 前缀）|
| B-3 | `\[resource\]\s+missing_file\s+([^\s]+)` | 取缺失文件，对 CRASH_SESSION 中该错误行上 10 行找最近 `[hashfs] XXX.scs: Created` → 包名 → S 级 |
| B-4 | `could not load unit\s+([^\s]+)` / `unit not found\s+([^\s]+)` | 同上 B-3 规则取紧邻上一个 Created → S 级 |

#### Step 5：崩前区间 → A/B 级嫌疑（S 级不足 5 条时补）

- 在 CRASH_SESSION 尾部最后 100 行，向上找**最后一个成功的 `[hashfs] XXX.scs: Created and validated`** = "最后成功挂载 mod"（A 级第 1）。
- 其后还有 "正在 attempt mount 的区间"：若有 `[hashfs] XXX.scs: Created` 开始 但未写完 validated（= 崩发生在该文件 hashfs 校验过程中）→ A 级第 2。
- 最后 5 个成功 mount 的 mod：依次 B 级（按"距离崩溃行越近越靠前"）。
- 合并后按 S > A > B 排序，同级按真实加载顺序越靠后越先（= 越接近崩溃点嫌疑越大）。

#### Step 6：mod 匹配失败兜底（failed_to_match 累计）

- 若 S 级的 package_name 在当前 profile 全 mod 列表（file_name / display_name / ws_id）里找不到 → `failed_to_match += 1`，**仍保留该 suspect**（mod_id="", display_name="未知 mod: <package_name>"，UI 上灰色呈现并提示"当前 mod 列表未包含此包，可能已被卸载或来源不是本管理器"）。

---

## 5. UI 规格

### 5.1 独立 CrashCheckDialog（QDialog，1000 × 680 默认）

顶部 QTabWidget：
- **Tab 1「🔍 加载预检」**
  - 上部控件：
    - 下拉 "扫描深度"：L0 快筛 / L1 中检（默认）/ L2 深度模拟；Checkbox "☑ 叠加最近一次启动日志验证（L3）"（默认勾选）
    - 按钮「开始扫描」（QPushButton，主色）、「停止」（扫描中启用）
    - 标签：`已扫描 N 项 · RED × R · YELLOW × Y · 耗时 T 秒`
  - 主体：QTableView / QTableWidget，Column：
    - `🎯`（Severity 色块：RED=#D7263D 圆角 / YELLOW=#F46036 圆角 / GREEN=#1B998B 圆角）
    - `Mod 名称`（mod_display_name，hover tooltip=evidence 全文）
    - `优先级 #`（priority_index；全档级问题显示「档级」二字）
    - `层级`（L0 / L1 / L2 / L3 徽章）
    - `检查`（check_code + suggestion 短版）
    - `动作`（单元格内 2 个按钮：「跳到 mod」「禁用」）
  - 底部：
    - 复选框 `仅显示 RED / YELLOW（隐藏 GREEN）`（默认勾选）
    - 红色主按钮 `一键禁用所有 RED 项并保存 Profile`（QMessageBox 二次确认："将禁用 N 个 RED 告警 mod 并覆盖写入 profile，确认？"）
    - 关闭按钮

- **Tab 2「📄 Crashlog 解析」**
  - 上部控件：
    - QLineEdit「game.crash.txt 路径」+ 按钮「浏览…」（QFileDialog.getOpenFileName，过滤 `game.crash.txt (*.txt);;All (*.*)`）
    - 按钮「🔎 自动扫描 Documents（ETS2/ATS）」（默认打开对话框时自动触发一次，把最新 game.crash.txt 填进来并立即解析）
    - 标签：崩溃时间 · Build · 异常码 · 模块分类
  - 主体（垂直拆分 QSplitter）：
    - 上 55%：嫌疑列表 QTableWidget，Column：
      - `#`（rank）
      - `嫌疑度`（S 红底白字 / A 橙底黑字 / B 黄底黑字 徽章）
      - `Mod 名称`（点击高亮同 L1）
      - `优先级 #`
      - `动作`（「跳到 mod」「禁用」「下移最底」3 按钮）
    - 下 45%：QPlainTextEdit（只读）显示 `CrashSuspectMod.evidence_lines`（点击某嫌疑行切换）；另提供 Tab 切"崩溃尾部 30 行原文"查看 raw_tail_lines。
  - 底部：关闭按钮。

### 5.2 入口（_ToolbarMixin）

- 菜单栏 Tools → 追加 2 条动作（separator 分隔）：
  - `🔍 Mod 加载预检…` → 触发 `_open_crash_dialog(initial_tab=PRECHECK)`
  - `📄 解析 Crashlog…` → 触发 `_open_crash_dialog(initial_tab=CRASHLOG)`
- 工具栏可选：在城市反查按钮后追加一个 `🛡️` 图标按钮（tooltip「崩溃排查：预检 & Crashlog」），点了打开对话框默认左 Tab。
- 生命周期：`CrashCheckDialog(parent=self)`；`self._crash_dialog_ref = weakref.ref(dlg)` 防二次打开（二次 show 并 raise_）。

### 5.3 信号与主窗口联动

CrashCheckDialog 自定义 Signal：
```python
# crash_check_dialog.py
from PySide6.QtCore import Signal, QObject

class CrashCheckSignals(QObject):
    locate_mod_requested = Signal(str)    # 参数 mod_id
    disable_mods_requested = Signal(list) # 参数 list[mod_id]，带批量；保存 profile 动作仍由 Mixin 决定
    move_to_bottom_requested = Signal(str)# 参数 mod_id

class CrashCheckDialog(QDialog):
    signals = CrashCheckSignals()
```

_ToolbarMixin 中 `_open_crash_dialog` 连线：
```python
dlg.signals.locate_mod_requested.connect(self._locate_mod_in_table)
dlg.signals.disable_mods_requested.connect(self._disable_mods_and_save)
dlg.signals.move_to_bottom_requested.connect(self._move_mod_to_bottom)
```
其中 `_locate_mod_in_table(mod_id)` 调 `_TableDataMixin` 中现有 select 逻辑（若缺则补一个 `select_mod_row(mod_id: str) -> bool`：找到行 → setCurrentCell → scrollToItem → 高亮闪烁 QTimer 3 次）。

---

## 6. 公共 API 规格（services/crash_service.py）

```python
# services/crash_service.py
# Mod 加载预检 + Crashlog 解析公共入口。所有方法均为纯同步 CPU 密集型，UI 端需放入 QThread 执行。
from __future__ import annotations
from .models import Mod   # 复用现有 models.Mod
from typing import Optional

# 文档目录自动发现
def discover_default_game_dirs() -> dict[str, Path]:
    # 返回 {"ets2": Path, "ats": Path} 对应 Documents 下两个目录（不存在则值为 None）。
    ...

def discover_latest_crash_pair() -> dict[str, Optional[Path]]:
    # 自动找最新的 (game.crash.txt, game.log.txt) 对儿：返回 {"crash":..., "log":..., "source":"ets2"|"ats"|None}；都不存在则路径值 None。
    ...

# 功能 A：预检
def precheck_active_mods(
    profile,                 # 复用现有 Profile 数据类（含 active_mods[] / profile_id / mods）
    all_mods: list[Mod],
    max_depth: PrecheckDepth = PrecheckDepth.L2_DEEP,
    use_l3_history: bool = True,
    ets2_docs_dir: Optional[Path] = None,  # 默认从 discover_default_game_dirs 拿
    ats_docs_dir: Optional[Path] = None,
    cancel_flag = None,     # QAtomicInt 或 threading.Event；L2 循环中每 1000 entry 检查，True 则中止并返回已得部分
) -> PrecheckReport:
    ...

# 功能 B：Crashlog 分析
def analyze_crashlog(
    crash_txt_path: str | Path,
    game_log_path: str | Path | None = None,
    profile = None,                   # 若给了 profile，则反查 mod 匹配 mod_id / display / priority；否则仅输出包名未匹配灰
    all_mods: list[Mod] | None = None,
) -> CrashAnalyzeResult:
    ...
```

### 6.1 实现依赖

- L0/L1：复用 `core.mod_scanner.ScsArchiveReader` 的 `list_entries()`；若 `ScsArchiveReader` 无该接口，新增 `ScsArchiveReader.list_entries() -> list[str]`（只读 hashfs 索引 ZIP central directory，不读 entry 数据）。
- L2 VFS：无外部依赖，纯 CPython dict 实现。
- L3：解析 `game.log.txt` 时按行流式扫描（for line in open），单文件 1 GB 上限（超过 1 GB 只读最后 100 MB），避免吃光内存。
- 所有异常：`precheck_active_mods` 顶层捕获任何未预期 Exception → 包装一条 RED 全档级 issue（check_code = "INTERNAL"，severity=RED，但同时把 traceback.print_exc 打 stderr，保留调试链路）。

---

## 7. 验证计划（Round7 测试矩阵）

验证脚本：工作目录 `_verify_round7.py`，Python 3.13.0 执行（`F:\Python3.13.0\python.exe _verify_round7.py`），输出 Round7.1~7.N 断言 + Round4/5/6 回归命令。

### 7.1 功能 A 用例（Precheck）

| 用例编号 | 构造场景 | 期望断言 |
|---|---|---|
| P1 L0-1 | 构造 active_mods 中指向不存在的 `ghost.scs` → 扫描 | issue 中出现 RED + L0-1 + check_code=="L0-1" |
| P2 L0-2 | 构造 1 个 1KB 截断的 .scs（仅写"PK\\x03"前 4 字节加随机垃圾到 1024 字节）→ 扫描 | 命中 RED + L0-2 |
| P3 L0-3 | 构造合法 zip 包装 manifest.sii，但内容注入 \x00 NUL 字节 → 解析 | 命中 RED + L0-3 |
| P4 L0-4 | active_mods 中放 2 个不同 .scs 但 manifest 里 mod_id 写成同一 `traffic.pack.v1` | 命中 RED + L0-4 |
| P5 L1-2 | 构造 manifest 写 compatible_versions: ["1.48"]，但当前实机 game.log 是 Build 1.52 → 扫描 | 命中 RED + L1-2 |
| P6 L2-1（交叉引用缺失） | 构造 mod A 的 manifest unit=def/vehicle/truck/xxx.sii，但 entry 里只有 def/vehicle/，没有 xxx.sii；再构造 mod B 试图 @include 该 xxx.sii | 扫描 L2 → 命中 RED + L2-1，evidence 含 "def/vehicle/truck/xxx.sii" |
| P7 L3-1（历史日志） | 把预置 game.log 段（含 "in package 'break.scs'" 错误行）写到临时目录，use_l3_history=True → 扫描 | 命中 RED + L3-1 |
| P8 全 GREEN 对照 | 用实机现有 profile（最近正常启动过）→ 扫描 L0+L1+L3 | RED = 0（或与用户实际场景一致的合理基线），elapsed_ms 合理 |

### 7.2 功能 B 用例（Crashlog）

| 用例编号 | 构造场景 | 期望断言 |
|---|---|---|
| C1 崩溃定性 | 用实机 `C:\Users\11253\Documents\Euro Truck Simulator 2\game.crash.txt` 2026-06-06 版本 | exception_code == "C0000005 ACCESS_VIOLATION"；fault_module_category == "game_binary" 或 "third_party_injector"（若识别到 Nahimic/SogouPY） |
| C2 对齐 session | 构造短 game.log.txt（伪造：启动标记 2 个——第 1 个正常收尾，第 2 个不正常收尾，尾部有 `[sii] invalid unit ... in package 'x.scs'`）→ analyze | suspects[0].suspicion == S；failed_to_match 取决于 profile 是否含 x.scs |
| C3 A/B 级降级 | 构造无任何 S 级错误的伪造日志，但尾部最后 100 行中最后 5 个 [hashfs] Created 正常 → analyze | len(suspects) == 5；均为 B 级，且按 order 倒序（最后 mount 的是 rank 1） |

### 7.3 回归用例

- R4R：`F:\Python3.13.0\python.exe _verify_round4.py` → 34/34 PASS
- R5R：`F:\Python3.13.0\python.exe _verify_round5.py` → 22/22 PASS
- R6R：`F:\Python3.13.0\python.exe _verify_round6.py` → 37/37 PASS
- COM：对 `F:\ETS2ModManager\src\` 下 13 个 `.py` 文件做 `py_compile.compile(..., doraise=True)` 全 OK（新增 crash_service.py + crash_check_dialog.py 也应通过）

---

## 8. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| R1：L2 在 300 条大型地图 mod 场景下超时 >30s | 中 | 用户以为卡死 | L2 内部 `time.monotonic()` 计时，每 2000 次 dict 写入检查 1 次；超过 30s 立即打 L2-4 YELLOW + 中止 L2，返回 L0+L1。 |
| R2：S 级错误行中 `in package 'XXX.scs'` 的包名在当前 mod 列表对不上（大小写差异 / workshop 编号名） | 中 | 漏匹配 | 匹配策略 3 级：① 精确 mod.file_name 全匹配（大小写不敏感）；② mod.display_title 包含匹配；③ active_mods 的 source_name 粗匹配；④ 都不命中则 `failed_to_match += 1`，保留灰色未匹配嫌疑。 |
| R3：L3 历史日志最近 session 对应 profile 和当前 active_mods 完全不同 | 低 | L3 给出错误/误导信息 | L3 对比"启动日志尾部 active_mods[#] = 'xxx'"行（若存在）与当前 profile 一致性；差异超过 30% 则 L3 全部降级为 YELLOW 信息级并附提示"本次 L3 对应 mod 组合与当前差异较大，参考即可"。 |
| R4：ScsArchiveReader.list_entries() 对加密 SCS/ModGuard 加密包报错 | 中 | 假阳性 RED | list_entries() 对已识别加密包（现有 external_extractor 识别路径）直接跳过该检查 + 打 YELLOW 信息（"加密包，无法静态校验"），不乱报 RED。 |
| R5：300 mod × 5万 entry = dict 1500万条，内存峰值 | 低 | 内存占 500MB~1GB | L2 VFS 内部按路径前缀分桶（/def 存、/automat 存、/vehicle 存、/material 存、/effect 存、其他丢弃——交叉引用缺的都是这 5 类），容量压降到约 30%。 |

---

## 9. 代码变更清单（预估行数）

| 文件 | 性质 | 预估新增/改数 |
|---|---|---|
| `src/services/crash_service.py` | 新建 | ~600 行（数据类 + 4 层引擎 + L3 历史日志解析 + B 崩溃解析） |
| `src/ui/crash_check_dialog.py` | 新建 | ~500 行（双 Tab UI + Signal + 两个 QTableWidget 填充器） |
| `src/core/scs_archive.py`（若 ScsArchiveReader 无 list_entries） | 修改 | ~20 行 加只读索引接口 |
| `src/ui/_mw_mixins/_toolbar_mixin.py` | 修改 | ~40 行（菜单/工具栏入口 + 打开对话框连线 3 Signal） |
| `src/ui/_mw_mixins/_table_data_mixin.py`（若缺 select_mod_row / disable_mods / move_to_bottom 辅助） | 修改 | ~50 行（3 辅助方法，优先复用现有操作） |
| `_verify_round7.py`（工作目录，一次性验证脚本） | 新建 | ~400 行（P1~P8 + C1~C3 + 回归 4 命令） |
