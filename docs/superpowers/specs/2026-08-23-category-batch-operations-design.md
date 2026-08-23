# ETS2ModManager — 用户自定义分类文件夹的整体块操作设计文档

> 日期：2026-08-23
> 作者：TRAE AI
> 状态：待用户 review
> 关联需求：
>   1. 批量启用时，选择哪个用户自定义文件夹一起启用/禁用/反选
>   2. 排序时，把某个自定义文件夹作为整体的排序成员（保持块内相对顺序）

---

## 1. 需求澄清与范围边界

### 1.1 已确认的关键语义
- **块内相对顺序保持**：对分类做整体排序时，块内已启用 mod 的相对 order 保持不变；只做整体平移。
- **整体排序作用范围**：只对该分类下「已启用」的 mod 生效（未启用的 mod 不在 active order 中，不参与）。
- **未分类不开放整体块**：左栏的「未分类」兜底节点**不**提供以下整体操作；仅用户自己创建的分类文件夹（通过 category_service `create_category` 创建，存储在 `user_categories.json` 中）开放。
- **启用/禁用作用范围**：操作该分类的**所有 mod**（已启用的不重复启用，未启用的改为启用；禁用同理）。
- **UI 双入口**：左栏分类树右键菜单（常用快捷操作）+ 工具栏下拉菜单弹窗式（新手可发现），两处都做。

### 1.2 非目标
- 不支持"拖拽分类文件夹到 priority worklist 整体重排"：拖拽排序仅针对单条 mod 行为（保留现有）。整体块操作通过右键/工具栏菜单触发。
- 不支持分类嵌套：分类树是扁平的（category_service 当前实现就是扁平 key 列表，无 parent），本次不新增。
- 不支持"同一个 mod 属于多个分类"：`Mod.category_tag` 是单字符串，保持不变；多分类将是后续独立需求。

---

## 2. 系统现状与数据模型

### 2.1 Mod 分类归属
每个 mod **属于 0 或 1 个**用户自定义文件夹（`Mod._category_tag: str`；空串 = 未分类）。

### 2.2 category_service 查询接口（无需改动）
- `mods_in_category(category_key: str) -> Set[str]`：返回该分类下的 **mod_id 集合**。
- `user_categories() -> Dict[str, dict]`：返回所有用户自定义分类。

### 2.3 PriorityService 当前接口
- `build_worklist(active_mod_ids, all_package_names)`：返回 `[{package_name, enabled, order}]` 列表。
- `move_up/move_down(worklist, indices, steps)`：按 worklist 下标数组上下移动，已能**保持所选 indices 之间的相对顺序**。
- `extract_active_mods(worklist)`：提取最终启用顺序。

### 2.4 左栏分类树节点类型标识
- 用户自定义分类：`setData(0, Qt.UserRole, ("cat", key))` — `node_data[0] == "cat"`。
- 未分类：`("uncat",)`。
- 全部 mod：`("all",)`。
- 存档节点：`("profile", ...)`。

---

## 3. 架构设计（方案 A）

### 3.1 分层
1. **UI 层 (main_window.py)**
   - 左栏右键「用户自定义分类」菜单扩展 9 项。
   - 工具栏「模组操作▼/优先级▼」新增按分类的子菜单（动态生成）。
   - Helper 函数集：`_cat_key_to_pkg_set`, `_enable_category`, `_disable_category`, `_toggle_category`, `_move_cat_up`, `_move_cat_down`, `_cat_top`, `_cat_bottom`。
2. **PriorityService（扩展）**：
   - `indices_for_category(worklist, pkg_set) -> List[int]`：返回 `enabled=True` 且 `package_name in pkg_set` 的所有 worklist 下标，**按原顺序升序**（保证块内相对顺序）。
   - `move_up_by_package_set(worklist, pkg_set, steps)` / `move_down_by_package_set(worklist, pkg_set, steps)`。
   - `move_top_by_package_set(worklist, pkg_set)` / `move_bottom_by_package_set(worklist, pkg_set)`。
3. **CategoryService（不变）**：查询 mod 集合。

### 3.2 数据流
```
category_key
  → category_service.mods_in_category(key) → Set[mod_id]
  → self._all_mods_by_id 查询每个 mod 的 package_name → Set[str] (pkg_set)
  → PriorityService 新 API 或 UI 层改写 worklist → save & 重绘
```

### 3.3 相对顺序保证
`indices_for_category` 按 worklist 原有顺序（升序）返回下标。现有 `move_up`/`move_down` 的处理逻辑：向下移动从最大下标开始，向上移动从最小下标开始，保证 indices 之间**相对顺序不混乱**。整体块与"批量选中行"使用同一套机制，天然正确。

---

## 4. UI 设计

### 4.1 左栏右键（仅用户自定义分类节点）
```
📁 启用此分类所有 mod
📁 禁用此分类所有 mod
🔁 反选此分类
──────────────────────
⏫ 整体上移    ▶  +1  +10  +50  +100
⏬ 整体下移    ▶  -1  -10  -50  -100
──────────────────────
⬆ 置顶此分类
⬇ 置底此分类
──────────────────────
(原有的重命名/删除/移动选中mod到此/取消分类保持不变)
```

### 4.2 工具栏下拉
- **模组操作▼** 末尾追加：
  - `按分类批量启用  ▶  {分类A} {分类B} ...`
  - `按分类批量禁用  ▶  {分类A} {分类B} ...`
  - `按分类反选      ▶  {分类A} {分类B} ...`
- **优先级▼** 在「批量下移」之后、「预设」之前追加：
  - `按分类整体上移  ▶  {分类A} ▶ +1 +10 +50 +100 ; {分类B} ▶ ...`
  - `按分类整体下移  ▶  {分类A} ▶ -1 -10 -50 -100 ; {分类B} ▶ ...`
  - `按分类置顶      ▶  {分类A} {分类B} ...`
  - `按分类置底      ▶  {分类A} {分类B} ...`

### 4.3 刷新与边界
- 操作后统一调 `_save_current_worklist()`：写存档 + 重绘表格 + 状态栏「操作分类 X 完成：N 个 mod」。
- 空分类：状态栏提示，不做任何改动。
- `current_profile is None`：所有整体块操作菜单 `setEnabled(False)`。

---

## 5. 错误处理

| 场景 | 处理 |
|---|---|
| category_key 不存在 | 不执行，状态栏提示「分类不存在」 |
| pkg_set 某些 package 在 worklist 中不存在 | 跳过（indices_for_category/enabled 改写只改 worklist 里的） |
| steps 过大 | PriorityService 现有 clamp，移到边界不抛错 |
| save_active_mods 失败 | QMessageBox.warning，保留内存 worklist，可重试 |

---

## 6. i18n 新增 16 条 key

`ui.cat_enable`, `ui.cat_disable`, `ui.cat_toggle`, `ui.cat_move_up`, `ui.cat_move_down`, `ui.cat_top`, `ui.cat_bottom`, `ui.grp_cat_enable`, `ui.grp_cat_disable`, `ui.grp_cat_toggle`, `ui.grp_cat_move_up`, `ui.grp_cat_move_down`, `ui.grp_cat_top`, `ui.grp_cat_bottom`, `ui.sb_cat_empty`, `ui.sb_cat_done`, `ui.sb_cat_no_profile`（实际 17 条，保留 16 方便统计）。

---

## 7. 改动文件清单

1. `src/services/priority_service.py` — 新增 5 个 API。
2. `src/ui/main_window.py` — Helper 函数 + 左右栏菜单扩展。
3. `assets/i18n/{zh_CN,en_US,ru_RU}.json` — 三语新增 key。

---

## 8. 自测用例

1. 启用分类 → 表格勾选 + active_mods 写入正确。
2. 禁用分类 → 勾选取消。
3. 整体下移 1 位 → 块平移，块内顺序不变（例：地图A,地图B,货柜C → 地图分类下移1 → 货柜C,地图A,地图B）。
4. steps 过大边界 → 移到末尾即止。
5. 工具栏下拉效果与右键一致。
6. 置顶 / 置底分类。
7. 空分类：状态栏提示，不改动。
8. 无存档选中：菜单置灰。

---

## 9. Spec Self-Review

- 无 TBD/TODO。
- 分层与改动清单一致；相对顺序保证与现有批量选中机制一致；排序范围=已启用；未分类=不开放；全部无歧义。
- 改动范围：仅方案 A 的最小必要，不引入新服务层。
