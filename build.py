"""
ETS2 Mod Manager 构建脚本
- 清理 dist/ 和 build/ 目录
- 使用 PyInstaller spec 文件构建
- 复制 assets/ 到输出目录
- 生成 version.json
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parent
_SPEC_FILE = _PROJECT_ROOT / "ETS2ModManager.spec"
_SRC_DIR = _PROJECT_ROOT / "src"
_ASSETS_DIR = _PROJECT_ROOT / "assets"
_DIST_DIR = _PROJECT_ROOT / "dist"
_BUILD_DIR = _PROJECT_ROOT / "build"
_OUTPUT_DIR = _DIST_DIR / "ETS2ModManager"
_VERSION_JSON_PATH = _DIST_DIR / "version.json"


def _clean_dir(path: Path) -> None:
    """清理指定目录（如存在则删除重建）。"""
    if path.exists():
        print(f"[clean] 删除 {path}")
        shutil.rmtree(str(path), ignore_errors=True)


def _check_pyinstaller() -> bool:
    """检查 PyInstaller 是否已安装。"""
    try:
        import PyInstaller
        print(f"[info] PyInstaller 版本: {PyInstaller.__version__}")
        return True
    except ImportError:
        print("[warn] PyInstaller 未安装，正在安装...")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "pyinstaller"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print("[info] PyInstaller 安装成功")
            return True
        else:
            print(f"[error] PyInstaller 安装失败: {result.stderr}")
            return False


def _check_upx() -> bool:
    """检查 UPX 是否已安装（可选，用于压缩可执行文件）。"""
    upx_path = shutil.which("upx")
    if upx_path:
        print(f"[info] 检测到 UPX: {upx_path}")
        return True
    else:
        print("[info] 未检测到 UPX，跳过压缩（可执行文件会更大）")
        return False


def _run_pyinstaller() -> bool:
    """运行 PyInstaller 使用 spec 文件构建。"""
    if not _SPEC_FILE.exists():
        print(f"[error] Spec 文件不存在: {_SPEC_FILE}")
        return False

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        str(_SPEC_FILE),
    ]
    print(f"[build] 执行: {' '.join(cmd)}")
    print()

    result = subprocess.run(cmd, cwd=str(_PROJECT_ROOT))
    if result.returncode == 0:
        print("[build] PyInstaller 构建成功")
        return True
    else:
        print(f"[error] PyInstaller 构建失败 (退出码: {result.returncode})")
        return False


def _copy_assets() -> None:
    """复制 assets/ 目录到输出目录。"""
    if not _ASSETS_DIR.exists():
        print("[warn] assets/ 目录不存在，跳过复制")
        return

    dest_assets = _OUTPUT_DIR / "assets"
    if dest_assets.exists():
        print(f"[clean] 删除旧 assets: {dest_assets}")
        shutil.rmtree(str(dest_assets), ignore_errors=True)

    print(f"[copy] {_ASSETS_DIR} -> {dest_assets}")
    # Runtime cache is machine-specific and can exceed 100 MB; keep it out of
    # release archives. The application creates the directory on first run.
    shutil.copytree(
        str(_ASSETS_DIR), str(dest_assets),
        ignore=shutil.ignore_patterns("cache"),
    )


def _create_version_json() -> None:
    """从 src/version.py 读取版本信息并生成 version.json。"""
    version_data = {
        "version": "0.0.0",
        "app_name": "ETS2 Mod Manager",
        "author": "Himeno Sena",
        "repo_url": "https://github.com/HimenoKoutarou/ets2-mod-manager",
        "build_timestamp": "",
    }

    version_py = _SRC_DIR / "version.py"
    if version_py.exists():
        try:
            import re
            content = version_py.read_text(encoding="utf-8")
            for key in ["version", "app_name", "author", "repo_url"]:
                pattern = rf'__{key}__\s*=\s*["\'](.+?)["\']'
                m = re.search(pattern, content)
                if m:
                    version_data[key] = m.group(1)
        except Exception as e:
            print(f"[warn] 读取 version.py 失败: {e}")

    from datetime import datetime
    version_data["build_timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    dest = _OUTPUT_DIR / "version.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(version_data, f, ensure_ascii=False, indent=2)
    print(f"[info] version.json 已生成: {dest}")


def _create_dist_version_json() -> None:
    """在 dist/ 根目录也放一份 version.json。"""
    version_py = _SRC_DIR / "version.py"
    version_data = {}
    if version_py.exists():
        try:
            import re
            content = version_py.read_text(encoding="utf-8")
            for key in ["version", "app_name", "author", "repo_url"]:
                pattern = rf'__{key}__\s*=\s*["\'](.+?)["\']'
                m = re.search(pattern, content)
                if m:
                    version_data[key] = m.group(1)
        except Exception:
            pass
    _DIST_DIR.mkdir(parents=True, exist_ok=True)
    with open(_VERSION_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(version_data, f, ensure_ascii=False, indent=2)


def main() -> bool:
    """主构建流程。"""
    print("=" * 60)
    print("  ETS2 Mod Manager - 构建脚本")
    print("=" * 60)
    print()

    # 1. 清理
    print("[1/5] 清理旧构建产物...")
    _clean_dir(_DIST_DIR)
    _clean_dir(_BUILD_DIR)
    print()

    # 2. 检查 PyInstaller
    print("[2/5] 检查 PyInstaller...")
    if not _check_pyinstaller():
        return False
    print()

    # 3. 检查 UPX（可选）
    print("[3/5] 检查 UPX...")
    _check_upx()
    print()

    # 4. 运行 PyInstaller
    print("[4/5] 开始构建...")
    if not _run_pyinstaller():
        return False
    print()

    # 5. 复制 assets + 生成 version.json
    print("[5/5] 复制资源文件...")
    # 判断输出位置（onefile 模式生成 dist/ETS2ModManager.exe，onedir 模式生成 dist/ETS2ModManager/）
    # 这里做兼容：如果是 onedir 模式（有目录），则在目录内操作
    global _OUTPUT_DIR
    exe_path = _DIST_DIR / "ETS2ModManager.exe"
    dir_path = _DIST_DIR / "ETS2ModManager"

    if dir_path.exists() and dir_path.is_dir():
        _OUTPUT_DIR = dir_path
        _copy_assets()
        _create_version_json()
    elif exe_path.exists():
        _OUTPUT_DIR = _DIST_DIR
        # onefile 模式仍需要把外部工具、图标、汉化资源和运行缓存目录
        # 放在 exe 旁边；这些资源不能依赖 PyInstaller 临时解压目录。
        _copy_assets()
        _create_version_json()
    else:
        print("[warn] 未找到输出产物，跳过资源复制")

    # 无论哪种模式都在 dist/ 放一份 version.json
    _create_dist_version_json()

    print()
    print("=" * 60)
    if exe_path.exists():
        print(f"  构建完成: {exe_path}")
    elif dir_path.exists():
        print(f"  构建完成: {dir_path}/")
    else:
        print("  构建完成")
    print("=" * 60)
    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
