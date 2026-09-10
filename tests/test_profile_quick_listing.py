"""Quick profile listing must not decrypt every profile during startup."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from services.profile_service import ProfileService


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        documents = Path(td)
        profiles_dir = documents / "profiles"
        display_name = "测试存档"
        profile_dir = profiles_dir / display_name.encode("utf-8").hex().upper()
        profile_dir.mkdir(parents=True)
        (profile_dir / "profile.sii").write_bytes(b"intentionally-not-readable")

        paths = SimpleNamespace(
            documents_dir=documents,
            mod_dir=documents / "mod",
            mods_info_path=documents / "mods_info.sii",
            profiles_dir=profiles_dir,
            steam_profiles_dir=None,
            workshop_content_dir=None,
            steam_cloud_dir=None,
        )
        service = ProfileService(paths)
        service._enrich = lambda profile: (_ for _ in ()).throw(
            AssertionError("quick listing must not decrypt profile.sii")
        )

        profiles = service.list_profiles(quick=True)
        assert len(profiles) == 1
        assert profiles[0].display_name == display_name
        assert profiles[0].active_mods == []

    print("PASS quick profile listing avoids profile decryption")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
