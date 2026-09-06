"""Regression checks for locating the bundled game locale archive."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from services.game_launcher_service import find_game_locale_path


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        game = Path(tmp) / "Euro Truck Simulator 2"
        exe = game / "bin" / "win_x64" / "eurotrucks2.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")
        locale = game / "locale.scs"
        locale.write_bytes(b"")
        assert find_game_locale_path(exe) == locale.resolve()

        # Portable layouts may keep the executable at the game root.
        portable_exe = game / "portable.exe"
        portable_exe.write_bytes(b"")
        assert find_game_locale_path(portable_exe) == locale.resolve()

        missing = Path(tmp) / "missing" / "game.exe"
        assert find_game_locale_path(missing) is None

    print("PASS game locale path discovery")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
