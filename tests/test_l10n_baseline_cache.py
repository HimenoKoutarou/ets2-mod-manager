"""Regression checks for user-selected baseline mods and persistent scans."""
from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from core.game_data import extract_game_data_for_active_mods
from services.l10n_service import L10nResult, L10nService, TranslationEntry


def _write_mod(path: Path, city_name: str, *, localized_key: str = "Baseline City") -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "def/city/city.sii",
            f'SiiNunit {{ city_data : city.test {{ city_name: "Original City" city_name_localized: "@@{localized_key}@@" }} }}',
        )
        zf.writestr(
            "locale/zh_cn/cities.sii",
            f'SiiNunit {{ localization_db : .localization {{ key[]: "{localized_key}" val[]: "{city_name}" }} }}',
        )
        zf.writestr("locale/zh_cn/keep.sii", 'SiiNunit { localization_db : .localization { key[]: "Outside Scan" val[]: "保留内容" } }')


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        baseline = root / "my_translation.scs"
        active = root / "map.scs"
        _write_mod(baseline, "基准城市", localized_key="Canonical City")
        with zipfile.ZipFile(active, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "def/city/city.sii",
                'SiiNunit { city_data : city.test { city_name: "Original City" } }',
            )

        service = L10nService(root / "config")
        ok, error = service.set_baseline_mod(baseline)
        assert ok, error
        entry = service.translate("Original City", "city", "map", unit_name="city.test")
        assert entry.status == "baseline"
        assert entry.translated == "基准城市"
        assert service.baseline_path == baseline.resolve()

        output = root / "corrected.scs"
        service.generate_l10n_mod(
            L10nResult(cities=[entry]), output,
            baseline_path=baseline,
        )
        with zipfile.ZipFile(output) as zf:
            assert "locale/zh_cn/keep.sii" in zf.namelist()
            assert "manifest.sii" in zf.namelist()

        cache = root / "config" / "l10n_scan_cache.json"
        first = extract_game_data_for_active_mods(
            [(str(active), "Map")], cache_path=cache,
        )
        assert len(first.cities) == 1
        assert cache.is_file()
        progress = []
        second = extract_game_data_for_active_mods(
            [(str(active), "Map")], cache_path=cache,
            progress=lambda cur, total, name: progress.append((cur, total, name)),
        )
        assert len(second.cities) == 1
        assert any("缓存" in name for _cur, _total, name in progress)

    print("PASS localization baseline and persistent scan cache")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
