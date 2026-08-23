# ETS2 Mod Manager (Euro Truck Simulator 2 Mod Manager)

专为《欧洲卡车模拟 2》打造的 **第三方模组管理器桌面程序**。
比游戏内 Mod Manager 更好用：批量启用/禁用、拖拽调优先级、
模组预览图/描述/适合版本一目了然，还提供 **Mod 目录跨盘迁移（软链接）** 功能，
拯救爆满的 C 盘。

## ✨ 功能

- 📋 **模组全览**：一次展示所有本地 + Steam Workshop 模组
- 🖼️ **预览图 + 描述**：直接读取 manifest.sii 中的图标和详细说明
- ✅ **批量启用/禁用**：一键全选 / 反选 / 按分类批量切换
- 🎯 **拖拽优先级**：QListWidget 原生拖拽，顺序即游戏加载优先级（列表越靠上优先级越高）
- 📁 **多 Profile 切换**：本地存档 / Steam Cloud 存档均可编辑 active_mods[]
- 💾 **预设方案**：保存「长途跑图」「短途卡车客运」等多套模组启用组合，一键切换
- 🔗 **Mod 目录跨盘迁移（新）**：一键把 `C:\...\mod` 搬到 F/D 盘，**Junction 目录联接**，游戏完全透明无需重启电脑
- 🛡️ **安全**：写入 profile.sii 前自动备份，最多保留 10 份历史版本

## 🧱 技术栈

| 层 | 技术 |
|---|---|
| 语言 | Python 3.11 |
| GUI | PySide6 (Qt 6, LGPL) |
| 打包 | PyInstaller |
| 图片 | Pillow (仅作为 PNG/JPG 解码兜底，核心使用 Qt 原生) |

## 🚀 快速开始（开发模式）

```bash
# 1. 克隆/下载项目到 F:\ETS2ModManager
cd F:\ETS2ModManager

# 2. 创建虚拟环境
python -m venv venv
venv\Scripts\activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 运行阶段 1 核心功能测试（不依赖 UI）
python tests\test_stage1.py

# 5. 启动完整程序（等 main.py 和 UI 层开发完）
python src\main.py
```

## 📁 目录结构

```
src/
├── core/                  ← 纯逻辑，无 UI 依赖
│   ├── models.py          # Mod / ModManifest / Profile 等 dataclass
│   ├── sii_parser.py      # SiiNunit 格式解析器（mods_info.sii / manifest.sii）
│   ├── scs_archive.py     # .scs/.zip/目录 读取
│   └── mod_scanner.py     # 扫描 /mod + Workshop 目录
├── services/              ← 业务服务
│   ├── profile_service.py # Profile 读写（调用 SII_Decrypt）
│   ├── priority_service.py# 排序/批量启用/预设
│   └── backup_service.py  # 自动备份
├── utils/                 ← 工具函数
│   ├── paths.py           # ETS2 文档/Workshop/Cloud 路径检测
│   └── symlink_manager.py # Mod 目录跨盘迁移（Junction + Symlink 双实现）
└── ui/                    ← Qt UI 层
```

## 🧪 开发路线图

- **✅ Stage 1**：数据层（SII 解析 / .scs 读取 / 扫描 / 路径 / 软链接）
- **Stage 2**：服务层（ProfileService / PriorityService / BackupService）
- **Stage 3**：UI 层（主窗口 / 拖拽列表 / 详情面板 / 批量工具栏）
- **Stage 4**：集成测试 + PyInstaller 打包

## ⚠️ 注意事项

1. **profile.sii 默认是加密格式**，读写需要社区工具 `SII_Decrypt.exe`（放在 `assets/bin/` 下即可自动调用）。如果不想解密，也可以只用本程序做「预设方案管理」，不写入真实存档。
2. **软链接功能**：优先使用 **Junction（目录联接）** 实现，不需要开启 Windows 开发者模式或管理员权限；如果 Junction 创建失败会自动回退到 Symlink。
3. **写入存档前务必备份**：本程序已内置自动备份，但仍建议你首次使用时手动复制一份 `profile.sii` 到安全位置。
