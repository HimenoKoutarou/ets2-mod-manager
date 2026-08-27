# Crash Check (Mod 预检 + Crashlog 解析) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 ETS2 Mod Manager 中新增 2 个功能：① Mod 加载预检（L0-L3 四层静态分级）② Crashlog 解析与嫌疑 mod 定位（S/A/B 三级排序）。UI 通过独立 QDialog 双 Tab 呈现，入口在主窗口 Tools 菜单。所有改动不引入真实游戏启动。

**Architecture:** 服务端 `services/crash_service.py` 提供纯同步 CPU 密集型两个公共入口（`precheck_active_mods` / `analyze_crashlog`），完全不含 Qt 依赖。UI 端 `ui/crash_check_dialog.py` 封装独立 QDialog、自定义 Signal 并将预检计算通过后台 QThread 执行。`_ToolbarMixin` 负责菜单入口、对话框打开、Signal 连线到现有 `_locate_mod_in_table` 并新增两个辅助方法（批量禁用 + 移到最底）。底层 `ScsArchiveReader` 新增只读 entry 列表接口，避免 crash_service 内部手动解析 zip。

**Tech Stack:** Python 3.13.0 / PySide6（QDialog, QTableWidget, QTabWidget, Signal, QThread）/ 标准库 zipfile, pathlib, re, time, dataclasses, enum / 现有 models.Mod、services.profile_service、core.scs_archive.ScsArchiveReader、core.sii_parser.parse_sii

---

## File Structure (Locked)

| 路径 | 性质 | 职责 | 预估行数 |
|---|---|---|---|
| `src/core/scs_archive.py` | 修改（追加） | `ScsArchiveReader.list_entries() -> list[str]` 只读索引接口（zip mode 直接 `z.namelist()`；dir mode 递归相对路径；external mode 返回空列表并打"加密包跳过"标记） | ~25 |
| `src/services/crash_service.py` | 新建 | 数据模型（Severity / PrecheckDepth / PrecheckIssue / PrecheckReport / CrashSuspicion / CrashSuspectMod / CrashAnalyzeResult）+ 4 层预检引擎 + discover_* 文档目录发现 + Crashlog 6 步解析 | ~620 |
| `src/ui/crash_check_dialog.py` | 新建 | `CrashCheckSignals(QObject)` 3 Signal + `CrashCheckDialog(QDialog)` 双 Tab（预检表 / Crashlog 表 + 日志面板），取消信号 cancel_flag 可中断 L2 | ~540 |
| `src/ui/_mw_mixins/_toolbar_mixin.py` | 修改（追加） | Tools 菜单新增 2 个动作（separator 分开），工具栏新增 🛡️ 图标按钮；新增 `_open_crash_dialog(initial_tab: int)`、`_disable_mods_and_save(mod_ids: list[str])`、`_move_mod_to_bottom(mod_id: str)` 3 辅助方法；Signal 连线到现有 `_locate_mod_in_table` | ~85 |
| `_verify_round7.py`（工作目录） | 新建 | 功能 A P1~P8 用例 + 功能 B C1~C3 用例 + 15 源文件 py_compile 编译检查 + Round4/5/6 回归命令执行 | ~460 |

---

## Task 1: 新增 ScsArchiveReader.list_entries() 只读索引接口

**Files:**
- Modify: `src/core/scs_archive.py` (在第 112 `# ---------- 底层文件读取 ----------` 注释之前追加 `list_entries()` 方法 + `is_encrypted_or_external` 属性)

- [ ] **Step 1: 读取 ScsArchiveReader 现有实现（定位第 89~112 行之间 close / __enter__ / __exit__ 之后，别名块之前）**

先读 `F:\ETS2ModManager\src\core\scs_archive.py` 确认第 89 `# ---------- 别名：兼容旧 API ----------` 行的准确位置。追加位置：在该 MARKER 行上方。

- [ ] **Step 2: 在工作目录写 patch 脚本 `_patch_task1_scs_archive.py` 进行外部文件原子修改**

```python
# _patch_task1_scs_archive.py
from pathlib import Path
import os, tempfile

TARGET = Path(r"F:\ETS2ModManager\src\core\scs_archive.py")
OLD_MARKER = "# ---------- 别名：兼容旧 API ----------\n"
NEW_BLOCK = '''
    def list_entries(self, max_entries: int | None = None) -> list[str]:
        """返回包/目录内所有 entry 路径（小写 + 反斜杠转正斜杠，dir 模式为相对路径）。
        - zip 模式：直接 ZipFile.namelist()
        - dir 模式：递归走所有普通文件，返回相对 self.path 的相对路径；最多 max_entries 个截断
        - external / unknown 模式：**返回空列表（不抛异常）** —— 调用方应对空列表打 YELLOW "加密包或未知包跳过静态 entry 检查"。
        """
        try:
            out: list[str] = []
            if self._mode == "zip" and self._zf is not None:
                try:
                    names = self._zf.namelist()
                except Exception:
                    return []
                for n in names:
                    norm = n.replace("\\\\", "/").lstrip("/").lower()
                    if not norm:
                        continue
                    out.append(norm)
                    if max_entries is not None and len(out) >= max_entries:
                        break
                return out
            if self._mode == "dir":
                root = self.path
                for full in root.rglob("*"):
                    try:
                        if not full.is_file():
                            continue
                        rel = full.relative_to(root).as_posix().lstrip("/").lower()
                        if not rel:
                            continue
                        out.append(rel)
                        if max_entries is not None and len(out) >= max_entries:
                            break
                    except (OSError, ValueError):
                        continue
                return out
            # external(scs_hashfs / aem / zip_encrypted) / unknown -> 返回空
            return []
        except Exception:
            return []

    @property
    def is_encrypted_or_external(self) -> bool:
        """True = 当前包是加密 / SCS# / AEM! 等需要外部解包工具的类型，entry 列表无法静态枚举。"""
        return self._mode == "external" or self._mode == "unknown"

'''

def main():
    src = TARGET.read_text(encoding="utf-8")
    assert OLD_MARKER in src, "MARKER 未找到，可能代码库结构变了"
    new_src = src.replace(OLD_MARKER, NEW_BLOCK + OLD_MARKER, 1)
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".tmp_t1_", dir=str(TARGET.parent))
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8", newline="\\n") as f:
            f.write(new_src)
        os.replace(tmp_path, str(TARGET))
    except Exception:
        if os.path.exists(tmp_path):
            try: os.remove(tmp_path)
            except OSError: pass
        raise
    print(f"[OK] Task1 applied: {TARGET}")

if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 执行 patch 脚本**

Run: `F:\Python3.13.0\python.exe _patch_task1_scs_archive.py`
Expected: `[OK] Task1 applied: F:\ETS2ModManager\src\core\scs_archive.py`

- [ ] **Step 4: 编译验证（py_compile）**

Run: `F:\Python3.13.0\python.exe -c "import py_compile; py_compile.compile(r'F:\\ETS2ModManager\\src\\core\\scs_archive.py', doraise=True); print('compile OK')"`
Expected: `compile OK`

---

## Task 2: 新建 services/crash_service.py（4 层预检 + Crashlog 解析引擎）

**Files:**
- Create: `F:\ETS2ModManager\src\services\crash_service.py`
- Import 依赖复用：`from core.models import Mod, ModManifest`；`from core.scs_archive import ScsArchiveReader`；`from core.sii_parser import parse_sii`。

**先验（已实机代码验证）：**
- `Profile` 数据类：`profile_id: str`、`active_mods: List[str]`（按 priority_index=0 最高排列）。
- `Mod`：`mod_id: str`（唯一 ID）、`package_path: str`（真实磁盘绝对路径）、`package_type ∈ {"scs","zip","directory","workshop"}`、`priority_index: int`、`manifest: ModManifest`、`display_title: str` 属性。
- `ModManifest`：`package_name`、`author`、`package_version`、`compatible_versions: list[str]`、`unit_names: list[str]`（不存在则空列表，getattr 兜底）。
- 现有 `_toolbar_mixin._locate_mod_in_table(mod_id)` 已存在（第 328~350 行），Task 4 Signal 直接复用。

- [ ] **Step 1: 写 PLAN_TEXT_r7_crash_service_code.txt（配套独立源码文件，存计划目录）**

独立源码共 ~620 行（按 §3.1 / §3.2 / §3.3 / §4 结构），所有方法名严格按公共 API 章节（`discover_default_game_dirs`、`discover_latest_crash_pair`、`precheck_active_mods`、`analyze_crashlog`）。所有异常顶层捕获打 RED INTERNAL 告警 + traceback.stderr。**L2 每 2000 次 dict 写入检查 cancel_flag + 30s 预算超时，超时打 L2-4 YELLOW。**所有正则用原始字符串（r''），避免 Windows 路径反斜杠问题。**L3 degraded_mode：当前组合与最近 session 差异 > 30% 时，RED 全降为 YELLOW 并打 L3-3。**`CrashAnalyzeResult.failed_to_match` 必须对 3 级匹配失败计数。

- [ ] **Step 2: 写 `_write_task2_crash_service.py` 脚本，把 PLAN_TEXT_r7_crash_service_code.txt 字节复制到目标路径**

```python
# _write_task2_crash_service.py
import os, tempfile, sys
from pathlib import Path
SRC = Path(__file__).with_name("PLAN_TEXT_r7_crash_service_code.txt")
DST = Path(r"F:\ETS2ModManager\src\services\crash_service.py")
if not SRC.exists():
    sys.stderr.write(f"ERROR: {SRC} 未找到\n"); sys.exit(2)
data = SRC.read_bytes()
DST.parent.mkdir(parents=True, exist_ok=True)
tf, tp = tempfile.mkstemp(prefix=".tmp_t2_", dir=str(DST.parent))
try:
    with os.fdopen(tf, "wb") as f: f.write(data)
    os.replace(tp, str(DST))
except Exception:
    if os.path.exists(tp):
        try: os.remove(tp)
        except OSError: pass
    raise
print(f"[OK] crash_service.py 写入 ({DST.stat().st_size} bytes)")
```

- [ ] **Step 3: 执行写脚本 + 编译 + 导入冒烟**

Run: `F:\Python3.13.0\python.exe _write_task2_crash_service.py`
Expected: `[OK] crash_service.py 写入 (xxxxx bytes)`

Run: `F:\Python3.13.0\python.exe -c "import py_compile; py_compile.compile(r'F:\\ETS2ModManager\\src\\services\\crash_service.py', doraise=True); print('compile OK')"`
Expected: `compile OK`

Run: `F:\Python3.13.0\python.exe -c "import sys; sys.path.insert(0, r'F:\\ETS2ModManager\\src'); from services import crash_service; names=['Severity','PrecheckDepth','PrecheckIssue','PrecheckReport','CrashSuspicion','CrashSuspectMod','CrashAnalyzeResult','precheck_active_mods','analyze_crashlog','discover_default_game_dirs','discover_latest_crash_pair']; print('imports OK:', [n for n in names if hasattr(crash_service,n)])"`
Expected: 列表 12 个名字全在。

---

## Task 3: 新建 ui/crash_check_dialog.py（双 Tab UI + 后台 QThread Worker + Signal 定义）

**Files:**
- Create: `F:\ETS2ModManager\src\ui\crash_check_dialog.py`
- Import：PySide6 所有控件 + Signal/QThread/QObject + services.crash_service 公共 API 集合 + threading.Event。

- [ ] **Step 1: 写 PLAN_TEXT_r7_dialog_code.txt（独立源码文件，存计划目录）**

独立源码共 ~540 行。**信号类 `CrashCheckSignals`：`locate_mod_requested = Signal(str)`、`disable_mods_requested = Signal(list)`、`move_to_bottom_requested = Signal(str)`。**预检 Worker 继承 QThread，构造参数含 `cancel_event`；L2 通过 `cancel_flag=threading.Event()` 传入。**停止按钮**：`self._cancel.set()` + `worker.requestInterruption()` + `worker.wait(2000)`，仍未结束则 `worker.terminate()` 兜底。**Severity 颜色固定**：RED `#D7263D`、YELLOW `#F46036`、GREEN `#1B998B`。**嫌疑度徽章**：S 红底白字 / A 橙底黑字 / B 黄底黑字。预检表动作列用 `setCellWidget` 放 2 个 QPushButton；Crashlog 表动作列 3 个按钮；"跳到"按钮 emit locate_mod_requested。**一键禁用所有 RED 按钮** —— QMessageBox::warning + StandardButton.Yes/No 二次确认后，收集该 Tab 所有 RED 的 mod_id（去重），emit `disable_mods_requested(list)`。**Crashlog 自动扫描** —— 对话框构造 `__init__` 末尾 `QTimer.singleShot(150, self._run_auto_analyze)`（避免构造期阻塞）。

- [ ] **Step 2: 写 `_write_task3_dialog.py` 脚本复制 PLAN_TEXT_r7_dialog_code.txt → 目标路径**

（与 Task 2 Step 2 同构，`SRC=PLAN_TEXT_r7_dialog_code.txt`，`DST=ui/crash_check_dialog.py`）。

- [ ] **Step 3: 运行 + 编译 + 导入冒烟**

Run 写脚本 → compile → import 检查 `CrashCheckDialog` / `CrashCheckSignals` 类存在。

---

## Task 4: 修改 _ToolbarMixin 增加崩溃排查菜单入口 + 3 辅助方法

**Files:**
- Modify: `F:\ETS2ModManager\src\ui\_mw_mixins\_toolbar_mixin.py`

**实机定位 3 个插入点（通过 Grep 确认）：**
1. **顶部 import 区**（`from ui._mw_workers import` 附近）→ 追加 `from ui.crash_check_dialog import CrashCheckDialog`；再加 `import weakref`（`weakref.ref` 用）。
2. **`_build_toolbar()` 方法中城市反查按钮之后**（grep `city_lookup` 按钮所在行）→ 追加一个 `🛡️` QToolButton，clicked 连接 `lambda: self._open_crash_dialog(0)`，CSS 用 `_QTB_STYLE_DEFAULT`。
3. **`_build_menubar()` 方法中 Tools 菜单**（mb.addMenu 区，城市反查动作所在 block 末尾 separator 之后）→ 追加 separator + 2 QAction：`🔍 Mod 加载预检…` → `_open_crash_dialog(0)`；`📄 解析 Crashlog…` → `_open_crash_dialog(1)`。
4. **Mixin 类末尾** 追加 3 新方法 + 1 属性初始化（`__init__` 末尾或懒初始化）：
   - `self._crash_dialog_ref: weakref.ReferenceType | None = None`（懒初始化）
   - `_open_crash_dialog(self, initial_tab: int=0)`：现有 self._crash_dialog_ref() 活着则 show/raise_/activateWindow/return；否则 new；连 3 Signal → 现有 locate + 新增 disable_mods_and_save / move_mod_to_bottom；exec()；存 weakref。
   - `_disable_mods_and_save(self, mod_ids: list[str])`：4 层匹配 mod_id / pkg stem / pkg name / display_title → 从 active_mods 删除对应 entry → `self.profile_svc.set_active_mods(self.current_profile, new_active)` → 成功则刷新表格 + QMessageBox.information 提示"已禁用 N 个 RED 告警 mod"。
   - `_move_mod_to_bottom(self, mod_id: str)`：找到 entry，pop + append → set_active_mods + refresh + info。

- [ ] **Step 1: 写 `_patch_task4_toolbar_mixin.py`（原子修改）**

采用 3 段最小替换：先读原文 → 替换 import 行块（加 crash_check_dialog 和 weakref）→ 替换 `_build_toolbar` 城市反查按钮 block（加 🛡️ 按钮）→ 替换 `_build_menubar` Tools separator 之前 block（加 2 动作 + separator）→ 替换类 class 末尾最后一行 `class` 结束前 `# END MIXIN` 标记（若无可在 Mixin 最后一个 `return None` 或最后一个方法末尾之后 append 3 方法文本。**最安全做法**：用正则定位 Mixin 最后一个方法末尾的空行 + 下一个顶层元素之前（Mixin 最后一行之后，下一个顶层 class/import 之前）的位置，插入 3 个方法 + 注释块。

- [ ] **Step 2: 运行 patch 脚本**

Expected: `[OK] toolbar_mixin applied`。

- [ ] **Step 3: 编译 + 签名检查**

Run: py_compile toolbar_mixin.py → OK；import `_ToolbarMixin`，检查 hasattr(3 方法 + locate 现成方法) 全部 True。

---

## Task 5: 新建工作目录 _verify_round7.py（功能 A 8 用例 + 功能 B 3 用例 + 编译 + Round4/5/6 回归）

**Files:**
- Create: 工作目录 `_verify_round7.py`

- [ ] **Step 1: 写 PLAN_TEXT_r7_verify_code.txt（独立源码文件）**

共 ~460 行。结构：
- R7.1-R7.13 共 13 断言；失败不退出，累计 FAIL 计数；末尾打印汇总。
- **P1 L0-1 (R7.1)**：active_mods=["ghost.scs"] + 假 Mod(package_path 不存在) → precheck L0 → 存在 L0-1 RED。
- **P2 L0-2 (R7.2)**：tempdir 下 1KB 截断 break.scs(b"PK\x03" + 垃圾字节) → L0-2 RED。
- **P3 L0-3 (R7.3)**：造合法 zip，写 manifest.sii 带 \x00 控制字符 → L0-3 RED。
- **P4 L0-4 (R7.4)**：2 个不同 Mod 同 mod_id → L0-4 RED。
- **P5 L1-2 (R7.5)**：ModManifest(compatible_versions=["1.48"])，伪造 Build 1.52 game.log.txt → L1-2 RED。
- **P6 L2-1 (R7.6)**：造 2 个 zip 包（modA 缺 unit，modB 引用缺失路径）→ L2-1 RED，evidence 包含缺失路径。
- **P7 L3-1 (R7.7)**：伪造启动标记 + [hashfs] break_me.scs: Created + in package 错误 + 无 shutdown → L3-1 RED，mod_id 匹配。
- **P8 (R7.8)**：调用 discover_latest_crash_pair 无异常，返回 dict 结构 OK。
- **C1 (R7.9)**：实机 ETS2 game.crash.txt → exception_code 正确 + fault_module_category ∈ {game_binary, third_party_injector, unknown}。
- **C2 (R7.10)**：伪造 game.log（2 session，第 2 个无 shutdown 且含 in package 'x.scs'）→ suspects[0].suspicion==S。
- **C3 (R7.11)**：5 个 Created、无 S 级错误 → len(suspects)≥5 且前 5 个 suspicion 含 B 级；最后 mount 的 B 级 rank≤第 1 个。
- **COM (R7.12)**：15 个关键 py 文件 py_compile 全通过（新增 crash_service、crash_check_dialog + 13 旧文件）。
- **R4R / R5R / R6R (R7.13)**：subprocess.run 执行 `_verify_round4.py` / `_verify_round5.py` / `_verify_round6.py`（存在于工作目录则执行），exit code 非 0 记 FAIL。

- [ ] **Step 2: 写 `_write_task5_verify.py` 脚本复制 PLAN_TEXT_r7_verify_code.txt → 工作目录 _verify_round7.py**

- [ ] **Step 3: 执行验证**

Run: `F:\Python3.13.0\python.exe _verify_round7.py`
Expected: `Round7 Summary: 13/13 PASS, 0 FAIL`（至少 11/13；Round4/5/6 回归脚本必须 exit 0）。

---

## Self-Review (Plan 内部校验)

1. **Spec 覆盖检查：**
   - L0 (5 条) / L1 (5 条) / L2 (4 条) / L3 (3 条) 四层规则 → Task2 `_run_L0/1/2/3` ✓
   - 去重合并（相同 mod_id + check_code 合并）→ `_finalize_report` ✓
   - Crashlog 6 步解析（定性 + session 对齐 + 加载顺序 + S 提取 + A/B 级 + 未匹配计数）→ `analyze_crashlog` ✓
   - 文档目录自动发现（ETS2/ATS + OneDrive 重定向兜底）→ `discover_default_game_dirs` / `discover_latest_crash_pair` ✓
   - UI 双 Tab 规格（左预检控件+表格+底部动作；右 Crashlog 路径+摘要+上下拆分）→ Task3 ✓
   - 工具栏菜单 + 工具按钮 + Signal 三连线 → Task4 ✓
   - 验证矩阵 P1~P8 / C1~C3 共 11 条 + 编译 + 回归 = 13 断言 ✓
   - 风险缓解 5 条：L2 超时 30s ✓ / 包名匹配 4 层 ✓ / degraded_mode 差异>30% 降 YELLOW ✓ / 加密包 list_entries 返回空 ✓ / VFS 前缀分桶只存 8 类路径 ✓
2. **Placeholder 扫描：** 没有 "TODO / TBD / 适当处理 / 类似 Task N"。
3. **类型一致性：** `_locate_mod_in_table` 实机已存在签名 `(mod_id: str)`，与 Signal(str) 匹配；disable_mods_requested 传 list[str] 与方法签名 list[str] 一致；move_to_bottom(str) 一致。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-26-crash-check.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
