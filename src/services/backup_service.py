from __future__ import annotations

import os
import shutil
import datetime as _dt
from pathlib import Path
from typing import Optional, List


DEFAULT_MAX_BACKUPS = 10  # 与 README 文档一致


class BackupService:
    """
    读写 profile.sii / mods_info.sii / 任何重要文件前自动备份。
    规则：
      - 每个源文件有独立的 backups 子目录（如 profile.sii → profile.sii.backups/*.bak）
      - 文件名格式：YYYYMMDD-HHMMSS-<短标签>.bak
      - 超过 MAX_BACKUPS 时自动删除最老的
      - 如果源文件与最近一次备份 100% 字节相同，跳过（去重）
    """

    def __init__(self, max_backups: int = DEFAULT_MAX_BACKUPS):
        self.max_backups = max_backups

    @staticmethod
    def _backup_dir(source: Path) -> Path:
        return source.parent / f"{source.name}.backups"

    @staticmethod
    def _files_identical(a: Path, b: Path) -> bool:
        if not a.exists() or not b.exists():
            return False
        if a.stat().st_size != b.stat().st_size:
            return False
        with open(a, "rb") as fa, open(b, "rb") as fb:
            while True:
                ba, bb = fa.read(1 << 16), fb.read(1 << 16)
                if ba != bb:
                    return False
                if not ba:
                    return True

    def backup(self, source: Path, tag: str = "auto") -> Optional[Path]:
        """执行一次备份，返回备份文件路径（如果跳过则返回 None）。"""
        source = Path(source)
        if not source.exists():
            return None
        bdir = self._backup_dir(source)
        bdir.mkdir(parents=True, exist_ok=True)
        ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        # 与最新备份对比：完全相同则跳过
        existing = sorted(bdir.glob("*.bak"))
        if existing and self._files_identical(source, existing[-1]):
            return None
        dest = bdir / f"{ts}-{tag}.bak"
        shutil.copy2(source, dest)
        # 滚动清理
        self._prune(bdir)
        return dest

    def _prune(self, bdir: Path) -> None:
        files = sorted(bdir.glob("*.bak"))
        if len(files) > self.max_backups:
            for old in files[: len(files) - self.max_backups]:
                try:
                    old.unlink()
                except OSError:
                    pass

    def list_backups(self, source: Path) -> List[Path]:
        bdir = self._backup_dir(source)
        if not bdir.exists():
            return []
        return sorted(bdir.glob("*.bak"))

    def restore_latest(self, source: Path) -> Optional[Path]:
        bs = self.list_backups(source)
        if not bs:
            return None
        shutil.copy2(bs[-1], source)
        return bs[-1]