"""Regression checks for the game base/DLC localization overlay."""
from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from core.game_data import collect_all_def_files, parse_from_merged_files
from services.game_launcher_service import find_game_localization_sources
from services.l10n_service import L10nService, TranslationEntry


def _write_zip(path: Path, city_key: str, value: str) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "def/city.sii",
            "SiiNunit { city_data : city.shared { city_name: \"Shared\" "
            f"city_name_localized: \"@@{city_key}@@\" }}",
        )
        zf.writestr(
            "locale/zh_cn/local.sii",
            "SiiNunit { localization_db : .localization { "
            f"key[]: \"{city_key}\" val[]: \"{value}\" }}",
        )


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        base = root / "base.scs"
        dlc = root / "dlc_map.scs"
        low_mod = root / "low.scs"
        high_mod = root / "high.scs"
        _write_zip(base, "Base", "本体")
        _write_zip(dlc, "Dlc", "DLC")
        _write_zip(low_mod, "Low", "低优先级")
        _write_zip(high_mod, "High", "高优先级")

        merged, locales = collect_all_def_files(
            [(str(high_mod), "High Mod"), (str(low_mod), "Low Mod")],
            base_game_sources=[(str(base), "Base Game"), (str(dlc), "Map DLC")],
        )
        parsed = parse_from_merged_files(merged, locales["zh_cn"])
        assert parsed.cities[0].source_mod == "High Mod"
        assert locales["zh_cn"]["Base"] == "本体"
        assert locales["zh_cn"]["Dlc"] == "DLC"

        # The helper includes map DLCs but excludes unrelated cosmetic DLCs.
        game = root / "Euro Truck Simulator 2"
        exe = game / "bin" / "win_x64" / "eurotrucks2.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")
        for name in ("base.scs", "base_map.scs", "def.scs", "locale.scs",
                     "dlc_iberia.scs", "dlc_greece.scs", "dlc_metallics.scs"):
            (game / name).write_bytes(b"")
        names = [Path(path).name for path, _label in find_game_localization_sources(exe)]
        assert names[:4] == ["base.scs", "base_map.scs", "def.scs", "dlc_greece.scs"] or names[:4] == ["base.scs", "base_map.scs", "def.scs", "dlc_iberia.scs"]
        assert "locale.scs" in names
        assert "dlc_metallics.scs" not in names

        # Missing localized fields are emitted even before a manual value is
        # entered, while the locale row remains editable in the UI.
        service = L10nService(root / "config")
        output = root / "generated.scs"
        result = type("Result", (), {
            "cities": [TranslationEntry(
                source="Untranslated City", translated="", status="missing_locale",
                source_mod="Map Mod", def_locale_key_present=False,
                locale_key="Untranslated City", unit_name="city.untranslated",
            )],
            "countries": [], "ferries": [], "hints": [],
            "translated_count": 0,
        })()
        service.generate_l10n_mod(result, output)
        with zipfile.ZipFile(output) as zf:
            assert "def/city/generated_Map_Mod.sii" in zf.namelist()
            assert "city_name_localized: \"@@Untranslated City@@\"" in zf.read(
                "def/city/generated_Map_Mod.sii"
            ).decode("utf-8")

    print("PASS game base/DLC localization overlay")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
