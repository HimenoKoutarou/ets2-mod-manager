"""Regression test for profile writes consumed by ETS2."""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from services.backup_service import BackupService
from services.profile_service import ProfileInfo, ProfileService
from utils.paths import detect_paths


def main() -> int:
    paths = detect_paths()
    source = paths.profiles_dir / "E7A781E381AFE887AAE58886E381AEE6ACB2E69C9BE381A8E6818BE38292" / "profile.sii"
    if not source.is_file():
        print("SKIP profile fixture not found")
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "profile.sii"
        shutil.copy2(source, target)
        profile = ProfileInfo(source.parent.name, "local", target.parent, target)
        service = ProfileService(paths, backup=BackupService())
        active = service.get_active_mods(profile)
        service.set_active_mods(profile, active, verify=True)
        payload = target.read_bytes()
        assert payload.startswith(b"SiiNunit")
        assert not payload.startswith(b"\xef\xbb\xbf")
        assert service.get_active_mods(profile) == active
    print("PASS plaintext profile write has no UTF-8 BOM")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
