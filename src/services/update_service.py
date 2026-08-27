"""
自动更新服务
- 从 GitHub Releases API 检查新版本
- 下载并安装更新（zip 压缩包）
- 支持代理（127.0.0.1:7897）
- 使用 urllib（标准库，无需 requests 依赖）
- 通过 Qt 信号上报进度
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal

from version import __version__, __release_api__

GITHUB_API_LATEST = "https://api.github.com/repos/HimenoKoutarou/ets2-mod-manager/releases/latest"
_DEFAULT_PROXY = None  # 不硬编码代理，优先读环境变量
_DOWNLOAD_CHUNK_SIZE = 64 * 1024


def _parse_version(v: str) -> tuple:
    """将版本字符串解析为可比较的元组（如 '1.2.3' -> (1, 2, 3)）。"""
    v = v.lstrip("v")
    parts: list[int] = []
    for seg in v.split("."):
        num = ""
        for ch in seg:
            if ch.isdigit():
                num += ch
            else:
                break
        if num:
            parts.append(int(num))
        else:
            parts.append(0)
    return tuple(parts)


def _compare_versions(a: str, b: str) -> int:
    """比较两个版本号，返回 -1/0/1 表示 a<b, a==b, a>b。"""
    ta = _parse_version(a)
    tb = _parse_version(b)
    max_len = max(len(ta), len(tb))
    padded_a = ta + (0,) * (max_len - len(ta))
    padded_b = tb + (0,) * (max_len - len(tb))
    for x, y in zip(padded_a, padded_b):
        if x < y:
            return -1
        if x > y:
            return 1
    return 0


class UpdateService(QObject):
    """自动更新服务：检查版本、下载安装。

    信号：
        progress(int, int, str): 进度（当前, 总数, 描述）
        status_changed(str): 状态文字变化
        update_available(str, str): 发现新版本（版本号, 下载地址）
        no_update_needed(): 已是最新版本
        download_finished(str): 下载完成（临时文件路径）
        install_finished(str): 安装完成（目标目录）
        error_occurred(str): 错误发生（错误描述）
    """

    progress = Signal(int, int, str)
    status_changed = Signal(str)
    update_available = Signal(str, str)
    no_update_needed = Signal()
    download_finished = Signal(str)
    install_finished = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, proxy_url: Optional[str] = None, parent=None):
        super().__init__(parent)
        self._proxy_url = proxy_url or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or os.environ.get("https_proxy")
        self._github_api_url = GITHUB_API_LATEST
        self._latest_version: Optional[str] = None
        self._download_url: Optional[str] = None
        self._release_info: Optional[dict] = None

    def set_proxy(self, proxy_url: str) -> None:
        """设置代理地址。"""
        self._proxy_url = proxy_url

    def _build_opener(self):
        """构建带代理的 urllib opener。"""
        import urllib.request
        handlers = []
        if self._proxy_url:
            proxy_handler = urllib.request.ProxyHandler({
                "http": self._proxy_url,
                "https": self._proxy_url,
            })
            handlers.append(proxy_handler)
        if handlers:
            return urllib.request.build_opener(*handlers)
        return urllib.request.build_opener()

    def _http_get(self, url: str, timeout: int = 30):
        """发送 HTTP GET 请求，返回 (状态码, 响应头, 响应体)。"""
        import urllib.request
        import urllib.error

        opener = self._build_opener()
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "ETS2ModManager-UpdateChecker/1.0",
                "Accept": "application/vnd.github+json",
            },
        )
        try:
            with opener.open(req, timeout=timeout) as resp:
                body = resp.read()
                headers = dict(resp.getheaders())
                status = resp.getcode()
                return status, headers, body
        except urllib.error.HTTPError as e:
            body = e.read() if hasattr(e, "read") else b""
            return e.code, {}, body
        except Exception as e:
            raise ConnectionError(f"无法连接到服务器: {e}") from e

    def check_for_update(self) -> bool:
        """检查 GitHub Releases 是否有新版本。

        返回：
            bool: True 表示有可用更新
        """
        self.status_changed.emit("正在检查更新...")
        self.progress.emit(0, 100, "连接 GitHub API")

        try:
            status, headers, body = self._http_get(self._github_api_url)
        except ConnectionError as e:
            self.error_occurred.emit(str(e))
            return False
        except Exception as e:
            self.error_occurred.emit(f"检查更新失败: {e}")
            return False

        if status == 403 and "rate limit" in body.decode("utf-8", errors="replace").lower():
            self.error_occurred.emit("GitHub API 请求频率超限，请稍后再试或配置代理")
            return False

        if status != 200:
            self.error_occurred.emit(f"GitHub API 请求失败 (HTTP {status})")
            return False

        try:
            release = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as e:
            self.error_occurred.emit(f"解析版本信息失败: {e}")
            return False

        self._release_info = release
        tag_name = release.get("tag_name", "")
        self._latest_version = tag_name.lstrip("v")

        if not self._latest_version:
            self.error_occurred.emit("无法获取最新版本号")
            return False

        assets = release.get("assets", [])
        download_url = ""
        # P0 安全修复：精确匹配项目 ZIP，不选第一个 .zip（避免下载 source.zip）
        app_lower = "ets2"
        for asset in assets:
            name = asset.get("name", "").lower()
            if name.endswith(".zip") and (app_lower in name or "modmanager" in name or "mod-manager" in name):
                download_url = asset.get("browser_download_url", "")
                break
        # 兜底：如果没有精确匹配，选最大的 zip（通常是安装包而非 source）
        if not download_url:
            best_size = 0
            for asset in assets:
                name = asset.get("name", "").lower()
                if name.endswith(".zip") and "source" not in name:
                    sz = asset.get("size", 0)
                    if sz > best_size:
                        best_size = sz
                        download_url = asset.get("browser_download_url", "")

        if not download_url:
            self.error_occurred.emit("未找到可下载的 zip 压缩包")
            return False

        self._download_url = download_url
        self.progress.emit(100, 100, "检查完成")

        cmp_result = _compare_versions(self._latest_version, __version__)
        if cmp_result > 0:
            self.status_changed.emit(f"发现新版本: {self._latest_version}")
            self.update_available.emit(self._latest_version, self._download_url)
            return True
        else:
            self.status_changed.emit("当前已是最新版本")
            self.no_update_needed.emit()
            return False

    def download_and_install(self, install_dir=None) -> bool:
        """下载并安装最新版本。

        参数：
            install_dir: 安装目标目录，默认为应用所在目录

        返回：
            bool: True 表示安装成功
        """
        if not self._download_url or not self._latest_version:
            self.error_occurred.emit("请先检查更新")
            return False

        if install_dir is None:
            if getattr(sys, "frozen", False):
                install_dir = os.path.dirname(sys.executable)
            else:
                install_dir = str(Path(__file__).resolve().parents[2])

        self.status_changed.emit(f"正在下载版本 {self._latest_version}...")

        try:
            # R11.1: mkdtemp replaces fixed dir (avoid concurrent update race)
            temp_dir = Path(tempfile.mkdtemp(prefix="ets2mm_update_"))
            zip_path = temp_dir / f"ets2_mod_manager_{self._latest_version}.zip"
        except Exception as e:
            self.error_occurred.emit(f"创建临时目录失败: {e}")
            return False

        try:
            self._download_file(self._download_url, zip_path)
        except Exception as e:
            self.error_occurred.emit(f"下载失败: {e}")
            self._safe_remove(zip_path)
            return False

        self.download_finished.emit(str(zip_path))
        self.status_changed.emit("下载完成，正在解压...")

        try:
            extract_dir = temp_dir / f"extracted_{self._latest_version}"
            if extract_dir.exists():
                shutil.rmtree(extract_dir)
            extract_dir.mkdir(parents=True, exist_ok=True)
            self._extract_zip(zip_path, extract_dir)
        except zipfile.BadZipFile as e:
            self.error_occurred.emit(f"压缩包损坏: {e}")
            self._safe_remove(zip_path)
            self._safe_remove(extract_dir)
            return False
        except Exception as e:
            self.error_occurred.emit(f"解压失败: {e}")
            self._safe_remove(zip_path)
            self._safe_remove(extract_dir)
            return False

        self.status_changed.emit("正在安装新版本...")
        self.progress.emit(0, 100, "准备安装")

        # R14.1: 事务式安装 — staging → backup → commit → rollback on failure
        #   P11:  backup 目录加 timestamp，避免上次失败残留卡死下次更新
        #   P1-3: 事务核心路径拿掉 ignore_errors=True（失败必须知道）
        #   P1-4: 区分 ROLLBACK_SUCCESS / ROLLBACK_FAILED，不再误报"已恢复"
        import time as _time
        install_path = Path(install_dir)
        backup_dir = install_path.parent / f".{install_path.name}_backup_{int(_time.time())}"

        try:
            # 1. R14.2 P2: 用 _validate_package() 做严格校验（抽出独立函数，规则加强）
            items = list(extract_dir.iterdir())
            if len(items) == 1 and items[0].is_dir():
                source_root = items[0]
            else:
                source_root = extract_dir

            validation_issues = UpdateService._validate_package(source_root)
            if validation_issues:
                raise RuntimeError(
                    "解压内容校验失败，拒绝安装：" + "；".join(validation_issues)
                )

            # 2. 备份当前版本（R14.1: backup 目录带 timestamp，不再清理旧 backup）
            self.progress.emit(30, 100, "备份当前版本")
            if install_path.exists():
                try:
                    # 优先 rename（同卷原子操作）
                    os.rename(str(install_path), str(backup_dir))
                except OSError:
                    # 跨卷 fallback: copytree → rmtree（R14.1 P1-3: 不吞错误）
                    shutil.copytree(str(install_path), str(backup_dir))
                    shutil.rmtree(str(install_path))  # 失败抛出，不静默

            # 3. 复制新版本到 install_dir
            self.progress.emit(50, 100, "安装新版本")
            try:
                self._copy_extracted_to(extract_dir, install_path)
            except Exception as copy_err:
                # R14.1 P1-4: 安装失败 → 从 backup 回滚，区分 rollback 状态
                self.status_changed.emit("安装失败，正在回滚...")
                # 3a. 清理 partial install（R14.1 P1-3: 不吞错误）
                if install_path.exists():
                    try:
                        shutil.rmtree(str(install_path))
                    except OSError as clean_err:
                        # partial 清理失败 — backup 还在，明确告知用户
                        raise RuntimeError(
                            f"安装失败且无法清理 partial install: {copy_err}。"
                            f"备份保留在 {backup_dir}，请手动恢复。"
                        ) from clean_err
                # 3b. 从 backup 回滚
                if backup_dir.exists():
                    rollback_ok = False
                    rollback_suffix = ""
                    try:
                        os.rename(str(backup_dir), str(install_path))
                        rollback_ok = True
                    except OSError:
                        # 跨卷 fallback
                        try:
                            shutil.copytree(str(backup_dir), str(install_path))
                            rollback_ok = True
                            rollback_suffix = "（跨卷复制）"
                        except OSError as rb_err:
                            rollback_suffix = (
                                f"且自动恢复失败（{rb_err}），备份保留在 "
                                f"{backup_dir}，请手动恢复。"
                            )
                    if rollback_ok:
                        raise RuntimeError(
                            f"安装失败，已从备份恢复{rollback_suffix}: {copy_err}"
                        ) from copy_err
                    else:
                        raise RuntimeError(
                            f"安装失败{rollback_suffix}"
                        ) from copy_err
                else:
                    raise RuntimeError(
                        f"安装失败且无备份可恢复: {copy_err}"
                    ) from copy_err

            # 4. 安装成功 → 删除备份（外围清理，可用 _safe_remove 静默）
            self.progress.emit(90, 100, "清理临时文件")
            if backup_dir.exists():
                self._safe_remove(backup_dir)

        except Exception as e:
            self.error_occurred.emit(f"安装失败: {e}")
            self._safe_remove(extract_dir)
            self._safe_remove(zip_path)
            return False

        self.progress.emit(100, 100, "安装完成")
        self.install_finished.emit(install_dir)
        self.status_changed.emit("安装完成")

        self._safe_remove(extract_dir)
        self._safe_remove(zip_path)

        return True

    def _download_file(self, url: str, dest: Path) -> None:
        """下载文件并实时汇报进度。"""
        import urllib.request

        opener = self._build_opener()
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "ETS2ModManager-UpdateChecker/1.0"},
        )
        with opener.open(req, timeout=600) as resp:
            total_size = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            last_percent = -1
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(_DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        percent = int(downloaded / total_size * 100)
                        if percent != last_percent:
                            last_percent = percent
                            self.progress.emit(percent, 100, f"下载中 {percent}%")
                    else:
                        self.progress.emit(downloaded, 0, f"下载中 {downloaded / 1024 / 1024:.1f} MB")

    def _extract_zip(self, zip_path: Path, extract_dir: Path) -> None:
        """安全解压 zip 文件到指定目录（防 Zip Slip 路径穿越）。"""
        extract_dir_resolved = extract_dir.resolve()
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            for info in zf.infolist():
                # 检查每个 entry 的路径是否在 extract_dir 内
                target = (extract_dir_resolved / info.filename).resolve()
                if not str(target).startswith(str(extract_dir_resolved) + os.sep) and target != extract_dir_resolved:
                    raise RuntimeError(f"Zip Slip 检测：不安全的 ZIP entry: {info.filename}")
            zf.extractall(str(extract_dir))

    def _copy_extracted_to(self, extract_dir: Path, target_dir: Path) -> None:
        """将解压后的文件复制到目标目录。

        策略：
        - 如果解压后直接包含 src/、assets/ 等目录，则整体合并
        - 如果解压后有一层包装目录，则进入该目录再合并
        """
        target_dir.mkdir(parents=True, exist_ok=True)

        items = list(extract_dir.iterdir())
        if len(items) == 1 and items[0].is_dir():
            source_root = items[0]
        else:
            source_root = extract_dir

        for item in source_root.iterdir():
            dest = target_dir / item.name
            if item.is_dir():
                if dest.exists():
                    self._merge_dirs(item, dest)
                else:
                    shutil.copytree(str(item), str(dest))
            else:
                shutil.copy2(str(item), str(dest))

    @staticmethod
    def _merge_dirs(src: Path, dst: Path) -> None:
        """递归合并两个目录（src 中文件覆盖 dst 中同名文件）。"""
        dst.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            dest_item = dst / item.name
            if item.is_dir():
                UpdateService._merge_dirs(item, dest_item)
            else:
                shutil.copy2(str(item), str(dest_item))

    @staticmethod
    def _validate_package(source_root: Path) -> list[str]:
        """R14.2 P2: 验证解压后的软件包内容有效性。

        返回问题字符串列表；为空表示通过。

        规则：
          - 至少包含一个启动器（run.py is_file 或 main.py is_file 或 ETS2ModManager.spec is_file）
          - 若存在 src/，必须是目录且至少包含 1 个 .py 文件（不是空壳）
          - 至少满足以上任一条件（不是纯空目录）
        """
        issues: list[str] = []
        if not source_root.exists() or not source_root.is_dir():
            issues.append("软件包根目录不存在或不是文件夹")
            return issues

        launcher_files = ("run.py", "main.py", "ETS2ModManager.spec")
        has_launcher = any(
            (source_root / m).is_file() for m in launcher_files
        )
        if not has_launcher:
            issues.append(
                f"未找到启动器文件（需要其中之一：{', '.join(launcher_files)}）"
            )

        src_path = source_root / "src"
        if src_path.exists():
            if not src_path.is_dir():
                issues.append("src 存在但不是文件夹（不是空壳即可）")
            else:
                # src 是目录：至少有一个 .py
                has_py = any(p.suffix == ".py" for p in src_path.rglob("*") if p.is_file())
                if not has_py:
                    issues.append("src/ 目录下没有任何 .py 文件，疑似空壳包")

        # 兜底：完全没有内容
        if not any(source_root.iterdir()):
            issues.append("软件包解压后内容为空")

        return issues

    @staticmethod
    def _safe_remove(path: Path) -> None:
        """安全删除文件或目录，失败时静默忽略。"""
        try:
            if path.is_dir():
                shutil.rmtree(str(path), ignore_errors=True)
            elif path.exists():
                path.unlink()
        except Exception:
            pass

    def get_release_info(self):
        """获取当前缓存的 GitHub Release 信息。"""
        return self._release_info

    def get_latest_version(self):
        """获取已检查到的最新版本号。"""
        return self._latest_version

    def get_current_version(self) -> str:
        """获取当前应用版本号。"""
        return __version__
