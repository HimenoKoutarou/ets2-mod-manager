# ETS2 Mod Manager 前端重做方案

日期：2026-09-10

## 目标

这不是存档编辑器的前端。产品主线是 Mod 管理：

- 首屏至少 80% 的注意力放在 Mod 扫描、启用状态、排序、Profile 和预设。
- 存档编辑只是辅助入口，不参与主导航竞争。
- 首次扫描可以慢，后续扫描必须依赖 SQLite 做增量增删补查。
- UI 支持简体中文、英文、俄语，所有显示文字走资源表。
- 前端不直接读写游戏文件；文件、加密、备份、回滚全部在后端完成。

## 最终技术栈

| 层 | 技术 | 责任 |
|---|---|---|
| 桌面壳 | Tauri 2 | Windows 窗口、打包、自动更新、权限边界 |
| UI | React + TypeScript + Vite | Mod 工作区、Profile、预设、辅助页面 |
| UI 状态 | Zustand | 当前 Profile、筛选、选中 Mod、任务状态 |
| 表格 | TanStack Table + 虚拟滚动 | 大量 Mod 的稳定渲染和拖拽排序 |
| 图标 | Lucide | 扫描、启用、排序、保存等熟悉图标 |
| 后端命令 | Rust + Tokio + Serde | 文件操作、任务取消、进度事件、DTO |
| 解析核心 | 现有 Rust archive/BSII/scanner | SCS/ZIP/HashFS/BSII/Hash |
| 索引 | SQLite + rusqlite | Mod、包内容、汉化条目、扫描指纹缓存 |
| Windows API | Rust `windows` crate | 进程检测、junction、Steam 路径、启动游戏 |
| 迁移过渡 | C# Service sidecar | 仅临时承载尚未迁入 Rust 的 Profile/Save 逻辑 |

不选 Electron：内存和启动开销较大，且文件操作边界更容易扩散到渲染层。
不选 Avalonia：仍然把 UI 绑定在 .NET 生态，无法解决当前 WPF 视觉和绑定复杂度问题。
最终发布程序不依赖 Python，也不依赖 WPF。

## 运行时边界

React 只处理状态和交互，不能调用 `fs`、`Process` 或直接拼接游戏路径。

Tauri 命令使用稳定的 DTO：

```text
输入：UTF-8 字符串、结构化 JSON、取消令牌
输出：结构化 JSON、错误码、进度事件
文件写入：只允许 Rust/C# service 端执行
```

首批命令契约：

```text
mod.scan / mod.cancel
mod.list / mod.set_enabled / mod.move
profile.list / profile.read_active / profile.write_active
preset.list / preset.save / preset.load / preset.delete
save.list_local / save.read / save.mutate / save.copy
localization.scan / localization.export
```

长任务通过事件返回：

```text
scan://progress
scan://completed
localization://progress
save://changed
```

每个写操作都在后端检查游戏进程、生成备份、原子替换并验证结果。

## 首屏信息架构

```text
顶部：当前 Profile | 扫描 Mod | 保存 Profile | 启动游戏 | 语言
左栏：本地 Profile | 分类 | 预设
中央：Mod 表格（启用、名称、来源、包名、分类、优先级）
右栏：选中 Mod 的图标、作者、版本、兼容性、描述
底部：扫描进度、数量、持久化状态、错误
次级入口：汉化、诊断、Workshop、城市、存档、工具
```

Mod 表格必须支持：

- 单行启用/禁用。
- 批量全启、全禁、反选。
- 拖拽排序以及上移、下移、置顶、置底。
- 当前 Profile 的 active_mods 顺序预览。
- 搜索名称、作者、包名、Workshop ID。
- 仅显示已启用、仅显示当前分类。

存档页面只显示本地 Profile 的 `save` 目录：

- `info.sii` 的 `name` 是显示名，读取失败才回退目录名。
- 普通存档按显示名排序。
- `autosave` 固定在普通存档之后；其它 `autosave*` 紧随其后。
- Steam/Cloud Profile 不返回存档槽位。

## 分阶段迁移

### 阶段 0：契约冻结

- 锁定 active_mods 顺序、Workshop alias、启用状态和预设格式。
- 锁定 `save.list_local` 的本地限定、名称解析和 autosave 排序。
- 为所有命令建立 JSON golden tests。

### 阶段 1：Tauri 壳和 UI 骨架

- 创建 `src/frontend` React/Vite 工程和 `src-tauri` Rust 工程。
- 建立主题、三语言资源、错误提示、任务进度和窗口状态。
- 不迁移业务，先用 DTO fixtures 渲染 Mod 主界面。

### 阶段 2：Mod 核心

- 接入现有 Rust scanner 和 SQLite index。
- 实现虚拟化 Mod 表格、批量启用/禁用、拖拽排序。
- 接入 Profile active_mods 读写和预设。
- 以真实 Mod 目录验证首次扫描和增量扫描。

### 阶段 3：Windows 和 Profile 适配

- Rust 封装 Steam 库、Workshop、Documents 和游戏进程检测。
- 迁移备份、原子写入、junction 和启动游戏。
- C# sidecar 只保留尚未迁移的 Profile 加解密路径，接口固定为 JSON-RPC。

### 阶段 4：辅助能力

- 迁移本地存档列表和 BSII 编辑。
- 迁移汉化扫描、城市搜索、Workshop 元数据、崩溃诊断。
- 存档和辅助页面只作为次级导航，不改变 Mod 首屏布局。

### 阶段 5：删除 WPF 和 sidecar

- 将 Profile SII 和存档 ScsC 逻辑迁入 Rust。
- 对比 C# sidecar 与 Rust 输出，逐项通过 golden tests 后删除 sidecar。
- 删除 WPF 项目、WPF 资源和旧启动入口。

### 阶段 6：发布验收

- `cargo tauri build` 生成 Windows 安装包。
- 真实 500+ Mod、多个 Profile、普通存档和 autosave 回归。
- 测试排序写回、重启后增量扫描、语言切换、取消扫描和失败回滚。

## 性能和稳定性要求

- SQLite 保存包路径、mtime、size、快速指纹和解析结果；无变化的 Mod 不重新解包。
- 表格使用虚拟滚动，禁止一次性创建数百个复杂 DOM 卡片。
- 图标和描述按需加载并缓存，不在扫描线程阻塞 UI。
- 扫描、汉化、解档任务支持取消；取消不提交半成品索引。
- 所有写入遵循 `backup -> staging -> atomic replace -> reread verify`。
- Rust 命令层统一错误码，React 不解析异常字符串决定业务分支。

## 推荐落地顺序

先做 Tauri 壳、Mod 表格、Profile/排序/预设和 SQLite 增量扫描，再迁移存档与汉化。
这样即使辅助功能迁移尚未完成，新的程序也已经是一个完整可用的 Mod 管理器。
