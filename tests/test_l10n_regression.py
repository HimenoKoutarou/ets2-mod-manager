"""Regression checks for non-blocking localization and export persistence."""
from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from services.l10n_service import L10nService, TranslationEntry
from core.game_data import _expand_mod_sources, _extract_cities_from_text


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        service = L10nService(Path(tmp))
        calls = []
        service._translate_via_api = lambda value: calls.append(value) or "在线翻译"

        # Scan/render resolution must never call the network.
        entry = service.translate("Road\"Name", "city", "map", allow_api=False)
        assert entry.status == "missing_locale" and not entry.locale_key_present and not calls

        service.set_native_locale({'Road"Name': "原生道路名"})
        native = service.translate('Road"Name', "city", "map", allow_api=False)
        assert native.status == "native" and native.locale_key_present
        service.set_native_locale({})

        # ETS2 map defs may carry the locale key in city_name_localized as
        # @@Key@@. The scanner must expose the unwrapped key rather than the
        # internal city_name identifier.
        cities = _extract_cities_from_text(
            'SiiNunit { city_data : city.test { city_name: "city.test" '
            'city_name_localized: "@@Benin@@" country: "country.test" } }',
            "sample",
        )
        assert len(cities) == 1
        assert cities[0].locale_key == "Benin"
        assert cities[0].city_name_localized == "@@Benin@@"
        missing = _extract_cities_from_text(
            'SiiNunit { city_data : city.missing { city_name: "FallbackKey" } }',
            "sample",
        )[0]
        assert missing.locale_key == ""
        assert missing.city_name_localized == ""
        missing_entry = service.translate(
            missing.city_name, "city", "sample", allow_api=False,
            def_locale_key_present=False,
        )
        assert missing_entry.status == "missing_locale"
        assert not missing_entry.def_locale_key_present

        # Explicit batch translation may call the network and persists results.
        service.batch_translate([entry])
        assert entry.status == "api" and calls == ['Road"Name']
        saved = json.loads((Path(tmp) / "l10n_dict.json").read_text(encoding="utf-8"))
        assert saved['Road"Name'] == "在线翻译"

        # Exported SII must remain valid when source/translation contains quotes.
        result = type("R", (), {"cities": [entry], "countries": [], "ferries": [], "hints": []})()
        out = Path(tmp) / "generated.scs"
        service.generate_l10n_mod(result, out)
        with zipfile.ZipFile(out) as zf:
            text = zf.read("locale/zh_cn/local_module.generated.sii").decode("utf-8")
            assert '\\"' in text
            assert service._parse_localization_db(text) == [('Road"Name', "在线翻译")]

        workshop = Path(tmp) / "workshop"
        (workshop / "157_content" / "def").mkdir(parents=True)
        (workshop / "latest" / "def").mkdir(parents=True)
        (workshop / "universal" / "def").mkdir(parents=True)
        assert [p.name for p in _expand_mod_sources(workshop)] == ["latest"]

        universal = Path(tmp) / "universal_root"
        (universal / "universal" / "def").mkdir(parents=True)
        (universal / "alt" / "def").mkdir(parents=True)
        assert [p.name for p in _expand_mod_sources(universal)] == ["universal"]

    print("PASS l10n nonblocking, persistence, and escaping")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
