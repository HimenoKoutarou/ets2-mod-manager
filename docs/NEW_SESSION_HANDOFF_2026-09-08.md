# ETS2 Mod Manager 新会话交接文档

更新时间：2026-09-08  
项目目录：`F:\ETS2ModManager`  
GitHub：`https://github.com/HimenoKoutarou/ets2-mod-manager`  
当前分支：`main`  
当前版本：`1.2.3`

## 新会话开场提示

可在新会话中直接发送下面这段话：

> 请继续维护 `F:\ETS2ModManager` 项目。先阅读 `F:\ETS2ModManager\docs\NEW_SESSION_HANDOFF_2026-09-08.md`，再检查 Git 状态和最近提交。工作区包含用户已有的未提交修改，禁止清理、回退或覆盖。每次完成代码改动后单独 commit，但未经我明确批准不要 push、打包或发布 Release。

## 用户约束

1. 每次代码改动完成并验证后都要 commit。
2. 未经用户明确批准，不得 push。
3. 未经用户明确批准，不得打包或发布 GitHub Release。
4. 可以 commit，但提交时只加入本次相关文件。
5. 工作区存在大量用户已有修改和生成文件，禁止使用 `git reset --hard`、`git checkout --` 或其他方式清理、覆盖这些内容。
6. 修改后需要主动测试，不要把基础回归测试留给用户。
7. 优先修复真实功能和稳定性问题，不要只改表面显示。
8. 只允许编辑本地存档；云存档必须保持只读或不显示。
9. 切换或修改存档时，需要确保 ETS2 游戏处于关闭状态。

## 本机已知路径

- 项目：`F:\ETS2ModManager`
- ETS2 游戏：`E:\SteamLibrary\steamapps\common\Euro Truck Simulator 2`
- 用户曾提供的地图 Mod：`H:\Euro Truck Simulator 2 mod\VolgaMap_1_5_3.scs`
- 外部解包器：`C:\Users\11253\Desktop\extractor.exe`
- 参考程序 Modium：`D:\Program Files\Modiumh`
- ETS2 实际 Mod 目录由 `src/utils/paths.py` 自动检测，运行时使用 `self.paths.mod_dir`。

## Git 状态

生成本文档前：

- `main` 比 `origin/main` 超前 20 个提交。
- 最近提交：

```text
476bd28 feat: add explicit start button to localization window
aa694a0 fix: confirm localization scan and open baseline picker
c363705 fix: open baseline selector in mod directory
fae1846 feat: use selected localization mod as baseline and cache scans
0e45bb0 fix: make localization dialog closable and allow partial export
a1ab8be fix: report localization scan progress early
21de7f8 feat: layer game base localization before mods
57b52c2 fix: robustly locate bundled game locale
19ae03b fix: resolve ProMods localization aliases
e4cd232 fix: exclude road signs from localization entries
```

注意：`aa694a0` 中加入的入口确认弹窗已在 `476bd28` 的最终代码中移除。现在的正确流程是打开汉化窗口后，由用户点击窗口内的“开始扫描”。

## 当前未提交修改

下列文件在生成本文档前已经处于修改状态，不属于本交接文档，接手时不要回退：

```text
assets/i18n/en_US.json
assets/i18n/ru_RU.json
assets/i18n/zh_CN.json
src/core/models.py
src/core/sii_parser.py
src/services/crash_service.py
src/services/priority_service.py
src/services/profile_service.py
src/services/save_editor_service.py
src/ui/_mw_workers.py
src/ui/save_editor_dialog.py
tests/test_priority_service_category.py
tests/test_save_editor_safety.py
tests/test_stage2.py
```

工作区还有多个未跟踪的测试、扫描数据、示例汉化包、构建目录和压缩包。除非用户明确要求，不要删除或纳入普通功能提交。

## 最新汉化功能状态

### 窗口启动流程

- 点击主界面的汉化入口后，只打开汉化窗口，不立即扫描。
- 汉化窗口内有“开始扫描”按钮。
- 用户可以在扫描前选择目标语言和基准汉化 Mod。
- 点击“开始扫描”后才创建后台扫描线程。
- 扫描完成后按钮变为“重新扫描”。
- 扫描过程中可以关闭窗口；先请求取消，超时后强制停止外部线程。

相关文件：

- `src/ui/_mw_mixins/_toolbar_mixin.py`
- `src/ui/l10n_dialog.py`

### 基准汉化 Mod

- 汉化窗口提供“选择基准汉化 mod”和“清除基准”。
- 文件选择器默认打开实际 ETS2 Mod 目录。
- 支持 `.scs` 和 `.zip`；服务层也支持已解包目录。
- 基准包的目标语言翻译优先作为修正参考。
- 会读取基准包里的 `def`，通过 unit name、原名称和 localized key 建立别名匹配。
- 解决当前 Mod 缺少 localized key、但基准汉化包已有对应翻译时的匹配问题。
- 基准内容与用户本地词典分离，不会直接写回基准文件。

相关文件：

- `src/services/l10n_service.py`
- `src/ui/l10n_dialog.py`

### 另存为规则

- 选择基准包后，默认输出名类似：

```text
原文件名.corrected.zh_cn.scs
```

- UI 和服务层都禁止输出路径覆盖原基准 Mod。
- 对标准 ZIP/SCS 或目录基准，会保留基准包中本次扫描范围之外的文件和翻译，再写入本次修正。
- 对无法完整读取的加密基准包，会生成独立增量汉化包，不修改原包。
- 即使还有未翻译项目，也允许用户确认后导出已完成内容。
- 未填写的项目不会写入空 locale value。
- 缺少 localized 字段的城市、国家或港口仍会生成最小 def 覆盖来补字段。

### 汉化扫描缓存

缓存文件：

```text
F:\ETS2ModManager\config\l10n_scan_cache.json
```

缓存特性：

- 按目标语言、Mod 列表及顺序、文件路径、大小、修改时间、游戏本体/DLC 基底生成签名。
- 目录 Mod 会计算 `def` 和目标 `locale` 目录内文件的签名，避免修改子文件后误用旧缓存。
- 最多保留 8 组不同存档或 Mod 组合的结果。
- 缓存命中时进度会显示“恢复汉化扫描缓存”。
- 加密包的选择性解包树还有独立缓存，位于外部解包服务定义的 `cache/l10n_tree`。

### 汉化覆盖顺序

当前目标流程：

1. 游戏本体与 DLC 的 def/locale 作为最低基底。
2. 已启用 Mod 按游戏优先级从低到高覆盖。
3. 同一 unit 最终保留高优先级定义。
4. 扫描完成后判断：已有翻译、缺少 value、缺少 locale、缺少 def localized 字段。
5. 用户选择的基准汉化 Mod 作为明确翻译参考。
6. 用户手动填写缺少的翻译；自动翻译当前禁用。
7. 导出为新汉化 Mod。

### 扫描范围

- 城市：city
- 国家：country
- 港口/轮渡：ferry
- 提示文本：hint
- 路牌文本已明确排除。
- 明确不是地图的 Mod 应在入口预筛选阶段跳过。
- 模糊或地图相关 Mod 才进入 def/locale 探测。
- 包内不存在相关 `def` 和目标 `locale` 时应快速跳过。

## 汉化相关测试

优先运行：

```powershell
cd F:\ETS2ModManager
python -m compileall -q src tests
python tests\test_l10n_baseline_cache.py
python tests\test_l10n_dialog_controls.py
python tests\test_l10n_blank_value_ui.py
python tests\test_l10n_regression.py
python tests\test_game_base_localization.py
```

截至 2026-09-08，上述测试均通过。

补充说明：

- `tests/test_extractor_selection.py` 直接运行时曾因没有把 `src` 加入 `sys.path` 而报 `ModuleNotFoundError: services`，这是测试启动方式问题，不是本次汉化功能失败。
- `tests/test_stage1.py` 和 `tests/test_stage2.py` 在 GBK PowerShell 中曾因输出勾号字符触发 `UnicodeEncodeError`，可先设置 `PYTHONIOENCODING=utf-8` 再运行。

## 项目主要结构

```text
src/core/
  game_data.py              汉化扫描、覆盖合并、扫描结果缓存
  mod_scanner.py            本地和 Workshop Mod 扫描
  scs_archive.py            ZIP/SCS/目录/加密包统一读取
  sii_parser.py             SII 解析

src/services/
  l10n_service.py           翻译匹配、基准 Mod、词典和汉化包导出
  external_extractor_service.py
                            Extractor/SXC、加密包索引与选择性解包缓存
  profile_service.py        本地 Profile 读取和 active_mods 写入
  priority_service.py       启用、禁用、排序和分类操作
  save_editor_service.py    存档编辑

src/ui/
  main_window.py            主窗口组合入口
  l10n_dialog.py            汉化窗口、开始按钮、进度、表格和导出
  save_editor_dialog.py     存档编辑窗口
  _mw_workers.py            初始化和 Profile 后台 Worker
  _mw_mixins/               主窗口逻辑拆分
```

## 已知高风险区域

1. Profile 的 `active_mods` 顺序与 UI 顺序方向相反，改动排序逻辑前必须跑优先级往返测试。
2. Workshop 条目可能使用数字 ID、旧别名、显示名或带 `workshop_` 前缀，需要统一别名解析。
3. 初始化和切换存档必须保持异步，禁止在 GUI 线程执行解密、HashFS 列表或大量目录扫描。
4. 全部 Mod 页面禁止拖动调整优先级，但仍允许拖入分类文件夹。
5. 自定义文件夹需要作为已启用 Mod 列表中的组合项显示；展开后的子 Mod 只显示实际启用项。
6. 加密 Mod 的预览图、description、适配版本和汉化扫描都依赖外部工具及缓存，修复一处时不要破坏其他读取路径。
7. 保存 Profile 前必须确认游戏关闭，并且只能写本地 Profile。
8. 工作区当前未提交的 Profile、优先级和存档编辑相关代码可能仍在开发中，修改前先逐个阅读 diff。

## 接手后的第一步

建议新会话按以下顺序开始：

```powershell
cd F:\ETS2ModManager
git status --short
git log -10 --oneline
git diff -- src/ui/l10n_dialog.py src/services/l10n_service.py src/core/game_data.py
python -m compileall -q src tests
```

然后根据用户的新问题，只读取相关模块并做小范围修改。提交前使用明确文件列表：

```powershell
git add -- path/to/changed_file.py path/to/test_file.py
git commit -m "type: concise description"
```

不要执行全量 `git add .`，避免把已有修改、构建产物和测试数据混入提交。

## 2026-09-08 发布记录

本次会话已完成一次构建和 push：

- 交接文档提交：`3caa3ad docs: add new session handoff`
- 已推送范围：`origin/main` 已更新到 `3caa3ad`
- 推送命令：

```powershell
git push origin main
```

- 构建来源：从干净 Git worktree 检出当前 `HEAD`，没有带入主工作区未提交修改。
- 构建命令：

```powershell
python build.py
```

- 构建版本：`1.2.3`
- 构建产物：`ETS2ModManager.exe`、`assets/`、`version.json`
- 压缩包：

```text
C:\Users\11253\Documents\Codex\2026-08-28\new-chat-5\outputs\ETS2ModManager-win-x64-v1.2.3-20260908.zip
```

- 压缩包已验证包含 `ETS2ModManager.exe` 和 `version.json`，`version.json` 中版本为 `1.2.3`。
- PyInstaller 构建成功；过程中有少量可选 Qt 数据库驱动和 QML 插件警告，不影响当前窗口程序构建。

### 发布后的 Git 注意事项

当前主工作区仍有用户之前留下的未提交修改和未跟踪构建/测试文件。`git status -sb` 应显示：

```text
## main...origin/main
```

这表示已提交内容已经与远端同步，但不代表工作区干净。新会话不得因为这些文件而执行清理或回退操作。后续若需要发布新的修改，应先确认具体文件，再只提交相关文件；不要把存档编辑、Profile、优先级和本地化测试产物一次性加入提交。

### 新会话发布检查

```powershell
cd F:\ETS2ModManager
git fetch origin
git status -sb
git log -3 --oneline
git diff --stat
```

发布前确认：

1. 功能改动已经有独立 commit。
2. `git status` 中没有意外被纳入的用户修改。
3. 构建使用的是明确的 commit 或干净 worktree。
4. `dist/version.json` 与 `src/version.py` 版本一致。
5. 压缩包能够打开，且至少包含 exe、assets 和 version.json。
6. 用户明确要求 push 或 Release 后才能执行远端发布。
