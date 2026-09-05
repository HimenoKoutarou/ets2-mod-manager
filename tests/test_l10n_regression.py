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
from core.game_data import (
    _expand_mod_sources,
    _extract_cities_from_text,
    _extract_countries_from_text,
    _extract_ferries_from_text,
    _extract_hint_texts_from_text,
    collect_all_def_files,
    parse_from_merged_files,
)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        service = L10nService(Path(tmp))
        calls = []
        service._translate_via_api = lambda value: calls.append(value) or "在线翻译"

        # Scan/render resolution must never call the network.
        entry = service.translate("Road\"Name", "city", "map", allow_api=False)
        assert entry.status == "missing_locale" and not entry.locale_key_present and not calls

        # A locale key with an explicitly empty val[] stays visible as a blank
        # editable entry instead of being mistaken for a missing key.
        service.set_native_locale({'BlankKey': ''})
        blank = service.translate("BlankKey", "city", "map", allow_api=False)
        assert blank.status == "missing_value"
        assert blank.locale_key_present and blank.translated == ""
        assert service._parse_localization_db(
            'SiiNunit { localization_db : .localization { '
            'key[]: "BlankKey" val[]: "" } }'
        ) == [('BlankKey', '')]
        assert service._parse_localization_db(
            'SiiNunit { localization_db : .localization { '
            'key[]: "HasValue" key[]: "MissingValue" val[]: "现有值" } }'
        ) == [('HasValue', '现有值'), ('MissingValue', '')]
        service.update_translation("BlankKey", "用户填写")
        assert service.translate("BlankKey", "city", "map").translated == "用户填写"
        service.clear_translation("BlankKey")
        assert service.translate("BlankKey", "city", "map").status == "missing_value"

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
        assert missing.locale_key == "FallbackKey"
        assert missing.city_name_localized == ""
        missing_entry = service.translate(
            missing.city_name, "city", "sample", allow_api=False,
            def_locale_key_present=False,
        )
        assert missing_entry.status == "missing_locale"
        assert not missing_entry.def_locale_key_present

        ferry = _extract_ferries_from_text(
            'SiiNunit { ferry_data : ferry.test { ferry_name: "@@Port X@@" } }',
            "sample",
        )[0]
        country = _extract_countries_from_text(
            'SiiNunit { country_data : country.test { name: "@@Country X@@" } }',
            "sample",
        )[0]
        assert ferry.locale_key == "Port X"
        assert country.locale_key == "Country X"

        sign_text = (
            'SiiNunit { sign_template_text : sign.route { '
            'text: "A<sub scale=0.4> </sub>1" } '
            'sign_template_text : sign.route2 { text: "E75" } '
            'sign_template_text : sign.words2 { text: "Ourense<br>Vigo" } '
            'sign_template_text : sign.words { text: "City centre" } }'
        )
        hints = _extract_hint_texts_from_text(sign_text, "sample")
        assert [hint.text for hint in hints] == ["Ourense<br>Vigo", "City centre"]

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

        # Multiple source mods are split into separate locale files and a
        # missing city_name_localized field gets a matching def override.
        split = type("R", (), {
            "cities": [
                TranslationEntry(source="Alpha", translated="阿尔法", status="api",
                                 source_mod="England", def_locale_key_present=False,
                                 locale_key="Alpha", unit_name="city.alpha"),
                TranslationEntry(source="Beta", translated="贝塔", status="api",
                                 source_mod="Germany", def_locale_key_present=True,
                                 locale_key="Beta", unit_name="city.beta"),
            ],
            "countries": [
                TranslationEntry(source="Country X", translated="国家X", status="api",
                                 source_mod="England", def_locale_key_present=False,
                                 locale_key="Country X", unit_name="country.x"),
            ],
            "ferries": [
                TranslationEntry(source="Port X", translated="港口X", status="api",
                                 source_mod="Germany", def_locale_key_present=False,
                                 locale_key="Port X", unit_name="ferry.x"),
            ],
            "hints": []
        })()
        split_out = Path(tmp) / "split.scs"
        service.generate_l10n_mod(split, split_out)
        with zipfile.ZipFile(split_out) as zf:
            names = set(zf.namelist())
            assert "locale/zh_cn/local_module.England.sii" in names
            assert "locale/zh_cn/local_module.Germany.sii" in names
            assert "def/city/generated_England.sii" in names
            def_text = zf.read("def/city/generated_England.sii").decode("utf-8")
            assert 'city_name_localized: "@@Alpha@@"' in def_text
            assert "# England Cities" in def_text
            assert "def/country/generated_England.sii" in names
            assert 'name_localized: "@@Country X@@"' in zf.read("def/country/generated_England.sii").decode("utf-8")
            assert "def/ferry/generated_Germany.sii" in names
            assert 'ferry_name_localized: "@@Port X@@"' in zf.read("def/ferry/generated_Germany.sii").decode("utf-8")
            assert "# England Cities" in zf.read("locale/zh_cn/local_module.England.sii").decode("utf-8")

        # Preserve an explicitly blank locale value while scanning a real
        # package. This is what lets the dialog render an empty editable cell.
        blank_locale_mod = Path(tmp) / "blank_locale.scs"
        with zipfile.ZipFile(blank_locale_mod, "w") as zf:
            zf.writestr(
                "def/city.sii",
                'SiiNunit { city_data : city.blank { city_name: "Blank City" '
                'city_name_localized: "@@BlankKey@@" } }',
            )
            zf.writestr(
                "locale/zh_cn/local_module.blank.sii",
                'SiiNunit { localization_db : .localization { '
                'key[]: "BlankKey" val[]: "" } }',
            )
        blank_defs, blank_locales = collect_all_def_files(
            [(str(blank_locale_mod), "Blank Locale")]
        )
        assert "BlankKey" in blank_locales["zh_cn"]
        assert blank_locales["zh_cn"]["BlankKey"] == ""
        blank_result = parse_from_merged_files(blank_defs, blank_locales["zh_cn"])
        assert blank_result.native_locale_dict["BlankKey"] == ""

        # Mod authors do not always use local_module.*.sii. Any SII/SUI under
        # the target locale directory should be considered when collecting
        # built-in translations, including a leading ./ in archive paths.
        custom_locale_mod = Path(tmp) / "custom_locale.scs"
        with zipfile.ZipFile(custom_locale_mod, "w") as zf:
            zf.writestr(
                "./locale/zh_cn/custom_strings.sui",
                'SiiNunit { localization_db : .localization { '
                'key[]: "CustomKey" val[]: "模组自带翻译" } }',
            )
        _, custom_locales = collect_all_def_files(
            [(str(custom_locale_mod), "Custom Locale")]
        )
        assert custom_locales["zh_cn"]["CustomKey"] == "模组自带翻译"

        # Active mod order is priority order (index 0 is highest). For a
        # colliding logical def path, the high-priority package must win.
        high = Path(tmp) / "high.scs"
        low = Path(tmp) / "low.scs"
        city_high = ('SiiNunit { city_data : city.same { city_name: "same" '
                      'city_name_localized: "@@High@@" } }')
        city_low = ('SiiNunit { city_data : city.same { city_name: "same" '
                     'city_name_localized: "@@Low@@" } }')
        for path, body in ((high, city_high), (low, city_low)):
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("def/city.sii", body)
        merged, _ = collect_all_def_files([(str(high), "High"), (str(low), "Low")])
        parsed = parse_from_merged_files(merged, {})
        assert len(parsed.cities) == 1
        assert parsed.cities[0].city_name_localized == "@@High@@"

        # The same unit can also be declared under different def paths.
        # Priority must still win over traversal/path order.
        with zipfile.ZipFile(high, "w") as zf:
            zf.writestr("def/city/z_high.sii", city_high)
        with zipfile.ZipFile(low, "w") as zf:
            zf.writestr("def/city/a_low.sii", city_low)
        merged, _ = collect_all_def_files([(str(high), "High"), (str(low), "Low")])
        parsed = parse_from_merged_files(merged, {})
        assert len(parsed.cities) == 1
        assert parsed.cities[0].source_mod == "High"
        assert parsed.cities[0].city_name_localized == "@@High@@"

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
