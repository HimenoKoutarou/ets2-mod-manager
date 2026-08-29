# ETS2 Mod Manager (Euro Truck Simulator 2 Mod Manager)

专为《欧洲卡车模拟 2》打造的 **第三方模组管理器桌面程序**。
比游戏内 Mod Manager 更好用：批量启用/禁用、拖拽调优先级、
模组预览图/描述/适合版本一目了然，还提供 **Mod 目录跨盘迁移（软链接）**、
**崩溃排查（启动游戏监控 + crashlog 自动解析嫌疑 mod）**，以及地图 Mod 汉化管理等高级功能。

## ✨ 功能

### 核心管理
- 📋 **模组全览**：一次展示所有本地 + Steam Workshop 模组
- 🖼️ **预览图 + 描述**：直接读取 manifest.sii 中的图标和详细说明
- ✅ **批量启用/禁用**：一键全选 / 反选 / 按分类批量切换
- 🎯 **拖拽优先级**：拖拽表格行调整顺序，顺序即游戏加载优先级（越靠上优先级越高）
- 📁 **多 Profile 切换**：本地存档 / Steam Cloud 存档均可编辑 active_mods[]
- 🗂️ **自定义文件夹**：将 Mod 拖入自定义文件夹，按文件夹批量启用、禁用和调整优先级
- 💾 **预设方案**：保存「长途跑图」「短途卡车客运」等多套模组启用组合，一键切换
- 🔗 **Mod 目录跨盘迁移**：一键把 `C:\...\mod` 搬到 F/D 盘，Junction 目录联接，游戏完全透明
- 🛡️ **安全写入**：写入 profile.sii 前自动备份，最多保留 10 份历史版本
- 🌐 **多语言**：中文 / 英文 / 俄语 三语切换

### 高级功能
- 🎮 **崩溃排查 — 启动监控**：从本工具启动游戏，游戏退出后自动检测是否崩溃；崩溃则自动解析 crashlog 定位嫌疑 mod
- 📄 **崩溃排查 — Crashlog 解析**：读取 `game.crash.txt` + `game.log.txt`，6 步分析（异常定性 → 崩溃 session 定位 → 嫌疑 mod 反推），按 S/A/B 三级置信度展示
- 🏙️ **城市反查**：输入城市名，反查哪些 mod 提供该城市定义
- 🌐 **汉化管理**：扫描已启用地图 Mod 的城市、国家、港口和提示文本，支持词典导入、批量翻译和汉化 Mod 导出
- 🔐 **加密 Mod 支持**：使用 Extractor / SXC 临时提取 `def` 和语言目录，支持扫描缓存与可取消后台任务
- 💾 **存档编辑**：查看和编辑 ETS2 存档数据
- 🔼 **自动更新**：检测 GitHub Release 新版本，一键下载安装

## 🧱 技术栈

| 层 | 技术 |
|---|---|
| 语言 | Python 3.11+ |
| GUI | PySide6 (Qt 6, LGPL) |
| 后台线程 | QThread + Signal/Slot（不使用 threading.Thread daemon） |
| 打包 | PyInstaller |
| 图片 | Qt 原生解码 + Pillow 兜底 |

## 🏗️ 架构概览

```
src/
├── core/                               # 纯逻辑层，无 UI 依赖
│   ├── models.py                        # Mod / ModManifest / Profile / ModIcon 等 dataclass
│   ├── sii_parser.py                    # SiiNunit 格式解析器（mods_info / manifest / profile.sii）
│   ├── scs_archive.py                    # .scs/.zip/目录 统一读取（ScsArchiveReader）
│   │                                     #   支持 HashFS(SCS#) / AEM!加密 / 标准 ZIP / 目录
│   │                                     #   list_entries() + is_encrypted_or_external
│   ├── mod_scanner.py                   # 扫描本地/mod + Workshop，构造 Mod 对象
│   │                                     #   _build_mod_from_package + _try_nested_scs/_try_nested_subdir 共享兜底
│   └── game_data.py                     # 游戏静态数据（城市/国家/港口/提示文本汉化数据）
│
├── services/                            # 业务服务层
│   ├── profile_service.py               # Profile 读写（调用 SII_Decrypt 解密/加密）
│   ├── priority_service.py              # 排序 / 批量启用 / 预设方案
│   ├── backup_service.py                # 自动备份（最多 10 份）
│   ├── session_service.py               # 会话状态 + 新 mod 检测（iterdir 轻量签名）
│   ├── category_service.py              # mod 分类持久化（known_mods.json）
│   ├── i18n_service.py                  # 国际化 _() / tr()（中/英/俄）
│   ├── update_service.py                # GitHub Release 检查 + 下载安装
│   ├── steam_workshop_service.py         # Workshop API 查询 mod 元数据
│   ├── crash_service.py                 # 崩溃分析引擎
│   │                                     #   analyze_crashlog(): 6 步解析 crash.txt + game.log
│   │                                     #   discover_default_game_dirs() / discover_latest_crash_pair()
│   ├── game_launcher_service.py         # 游戏启动 + 退出监控（find_game_exe + launch_and_watch）
│   ├── city_lookup_service.py           # 城市反查 mod
│   ├── save_editor_service.py           # 存档编辑
│   └── external_extractor_service.py    # 外部解包、临时扫描与加密包缓存（Extractor / SXC）
│
├── ui/                                  # Qt UI 层
│   ├── main_window.py                   # MainWindow（Mixin 组合入口）
│   │                                     #   class MainWindow(QMainWindow, _SignalMixin,
│   │                                     #       _TableDataMixin, _ToolbarMixin, _DialogMixin)
│   ├── _mw_widgets.py                    # 自定义 Widget（ModTable / SplashScreen 等）
│   ├── _mw_workers.py                    # QThread Worker（_QuickScanWorker / _AsyncParseWorker / _WorkshopFetchWorker）
│   ├── _mw_mixins/                       # MainWindow Mixin 拆分（R9 重构）
│   │   ├── _toolbar_mixin.py             #   工具栏 + 菜单 + 崩溃排查入口 + 3 辅助方法
│   │   ├── _signal_mixin.py              #   信号/事件/closeEvent + Worker 启停
│   │   ├── _table_data_mixin.py          #   表格数据填充/刷新/定位
│   │   └── _dialog_mixin.py              #   对话框管理（弱引用防二次打开）
│   ├── crash_check_dialog.py            # 崩溃排查对话框（启动监控 + crashlog 分析双模式）
│   │                                     #   _GameLaunchWorker(QThread) + _AnalyzeWorker(QThread)
│   │                                     #   3 Signal: locate_mod / disable_mods / move_to_bottom
│   ├── city_lookup_dialog.py            # 城市反查对话框
│   ├── save_editor_dialog.py            # 存档编辑对话框
│   └── l10n_dialog.py                   # 汉化管理对话框（城市/国家/港口/提示文本）
│
├── utils/
│   ├── paths.py                         # ETS2 文档/Workshop/Cloud/Steam 路径检测
│   └── symlink_manager.py               # Mod 目录跨盘迁移（Junction + Symlink 双实现）
│
└── version.py                            # 版本号（当前 v1.2.2）
```

### 线程模型

所有后台任务统一使用 **QThread + Signal/Slot** 模式（Round6 R10 重构后标准）：

| Worker | 职责 | 信号 |
|---|---|---|
| `_QuickScanWorker` | 快速扫描本地 + Workshop mod 列表 | progress / finished / failed |
| `_AsyncParseWorker` | 异步解析加密包 manifest/icon/description | progress / finished |
| `_WorkshopFetchWorker` | Workshop API 查询 mod 元数据 | progress / finished / failed |
| `_ExtractThread` | 汉化数据提取、加密 Mod 临时扫描 | progress / result_ready / canceled |
| `_TranslateThread` | 汉化词条批量翻译 | progress / result_ready |
| `_GameLaunchWorker` | 启动游戏 + 等待退出 + 崩溃检测 | launched / finished / failed |
| `_AnalyzeWorker` | crashlog 分析（6 步解析） | done / failed |

### 崩溃分析流程

```
用户点🛡️按钮 → CrashCheckDialog
  ├─ 模式 A：启动游戏监控
  │   → find_game_exe() 发现 eurotrucks2.exe
  │   → 记录 crash.txt mtime 快照
  │   → QProcess 启动游戏（用户正常玩）
  │   → QThread watchdog 等进程退出
  │   → 退出后：mtime 变新 or 退出码!=0 = 崩溃
  │   → 自动调 analyze_crashlog()
  │   → 弹出嫌疑 mod 列表
  │
  └─ 模式 B：手动选 crashlog 分析
      → 用户选 game.crash.txt
      → 自动配对 game.log.txt
      → 6 步解析：
         Step 1: crash.txt 头 80 行 → 异常定性（code/module）
         Step 2: game.log 尾部找崩溃 session
         Step 3: 第三方注入白名单过滤
         Step 4: 尾部 600 行 S 级嫌疑
         Step 5: 不足 5 条 → 最后挂载 A 级 + B 级补充
         Step 6: 3 级匹配 mod（精确/包含/兜底）
      → S/A/B 三色徽章展示
```

## 🚀 快速开始（开发模式）

```bash
# 1. 克隆/下载项目
git clone https://github.com/HimenoKoutarou/ets2-mod-manager.git
cd ets2-mod-manager

# 2. 创建虚拟环境
python -m venv venv
venv\Scripts\activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 运行
python run.py
```

## 📦 打包发布

```bash
# PyInstaller 一键打包
python build.py
# 或
build.bat
```

构建脚本生成以下产物：

- `dist/ETS2ModManager.exe`：Windows onefile 可执行文件
- `dist/assets/`：外部解包工具、SII 解密工具、图标和语言资源
- `dist/ETS2ModManager-win-x64.zip`：可直接分发的 Windows 压缩包

运行缓存不会随发布包打包，首次运行时会在 `assets/cache/` 自动创建。

## 📁 目录结构

```
ETS2ModManager/
├── src/                    # 源代码
│   ├── core/               # 纯逻辑层
│   ├── services/           # 业务服务层
│   ├── ui/                 # Qt UI 层
│   └── utils/              # 工具函数
├── assets/                 # 资源文件
│   ├── bin/                # 外部工具（SII_Decrypt.exe 等）
│   ├── i18n/               # 国际化翻译（zh_CN / en_US / ru_RU）
│   ├── tools/              # Extractor / SXC 加密 Mod 解包工具
│   ├── cache/              # 运行时缓存（扫描、预览图、Workshop 数据，不进发布包）
│   └── app_icon.png        # 应用图标
├── tests/                  # 测试
├── docs/                   # 文档（spec / plan）
├── run.py                  # 启动入口
├── build.py                # 打包脚本
├── requirements.txt        # 依赖
└── README.md
```

## 🧪 开发路线图

- **✅ Stage 1**：数据层（SII 解析 / .scs 读取 / 扫描 / 路径 / 软链接）
- **✅ Stage 2**：服务层（ProfileService / PriorityService / BackupService）
- **✅ Stage 3**：UI 层（主窗口 / 拖拽列表 / 详情面板 / 批量工具栏）
- **✅ Stage 4**：集成测试 + PyInstaller 打包
- **✅ v1.1**：Workshop 元数据查询 / 分类管理 / 多语言 / 自动更新 / 城市反查 / 存档编辑
- **✅ v1.2**：崩溃排查（启动监控 + crashlog 解析）/ MainWindow Mixin 重构 / R1-R8 优化
- **✅ v1.2.2**：汉化管理器 / 加密 Mod 临时扫描与缓存 / 可取消扫描 / 多地图城市和提示文本提取

## 🙏 Credits / 致谢

本项目集成了以下社区开源工具，用于解密和提取 ETS2 加密模组包：

| 工具 | 作者 / 来源 | 用途 |
|---|---|---|
| **Extractor** (extractor.exe) | sk-zk — [github.com/sk-zk/Extractor](https://github.com/sk-zk/Extractor) | 解析 SCS# (HashFS) 格式模组包 |
| **SXC Extractor** (sxc64.exe) | madman271 — [SCS Forum](https://forum.scssoft.com/viewtopic.php?t=276948) | 解析 AEM! / 加密 ZIP 格式模组包 |
| **SII_Decrypt** (SII_Decrypt.exe) | TheLazyTomcat — [SII_Decrypt](https://github.com/TheLazyTomcat/SII_Decrypt) | 解密 ETS2 的 profile.sii / controls.sii 等加密存档文件 |

特别感谢以上工具的作者和 Euro Truck Simulator 2 社区的逆向工程贡献者们。

## ⚠️ 注意事项

1. **profile.sii 默认是加密格式**，读写需要社区工具 `SII_Decrypt.exe`（放在 `assets/bin/` 下即可自动调用）。
2. **软链接功能**：优先使用 Junction（目录联接），不需要管理员权限；Junction 失败自动回退到 Symlink。
3. **写入存档前务必备份**：本程序已内置自动备份，但仍建议首次使用时手动复制一份 `profile.sii`。
4. **崩溃排查功能**：需要游戏至少运行过一次（产生 game.log.txt），崩溃后需要游戏生成 game.crash.txt。
5. **代理配置**：自动更新功能通过环境变量 `HTTP_PROXY` / `HTTPS_PROXY` 读取代理，不硬编码。
6. **汉化扫描**：首次扫描加密 Mod 可能需要较长时间；程序会缓存目录列表和提取结果，后续启动会明显加快。扫描过程中可点击关闭按钮取消任务。

## 📜 License

本项目仅供学习和个人使用。Euro Truck Simulator 2 是 SCS Software 的商标。
