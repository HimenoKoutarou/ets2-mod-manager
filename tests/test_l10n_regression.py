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
from core.models import Mod, ModManifest
from ui._mw_mixins._toolbar_mixin import _is_l10n_candidate_mod
from core.game_data import (
    _expand_mod_sources,
    _extract_cities_from_text,
    _extract_countries_from_text,
    _extract_ferries_from_text,
    _extract_hint_texts_from_text,
    _source_has_def_tree,
    _source_has_l10n_defs,
    _is_l10n_def_path,
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

        # Locale keys in real map packages are not consistently cased.  Both
        # the initial render and the explicit batch path must resolve them.
        service.set_native_locale({'poti': "波季"})
        folded = service.translate("Poti", "city", "map", allow_api=False)
        assert folded.status == "native" and folded.translated == "波季"
        folded_batch = TranslationEntry(source="POTI", status="missing_locale")
        service.batch_translate([folded_batch])
        assert folded_batch.status == "native"
        assert folded_batch.translated == "波季"
        assert folded_batch.locale_key_present

        service.set_native_locale({"  @@Cafe\u0301 @@  ": "咖啡馆"})
        normalized = service.translate("Café", "city", "map", allow_api=False)
        assert normalized.status == "native"
        assert normalized.translated == "咖啡馆"

        # Some definitions bypass locale keys and store the Chinese display
        # value directly.  This is visible as translated in-game and should
        # not be offered to the online translator again.
        service.set_native_locale({})
        direct_chinese = service.translate("南宁", "city", "map", allow_api=False)
        assert direct_chinese.status == "native"
        assert direct_chinese.translated == "南宁"
        assert not direct_chinese.locale_key_present
        service.set_native_locale({})

        # Valid UTF-8 names with diacritics must survive SII tokenization.
        accented = _extract_cities_from_text(
            'SiiNunit { city_data : city.kasepaa { city_name: "Kasepää" '
            'city_name_localized: "@@Kasepää@@" } }', "sample"
        )[0]
        assert accented.locale_key == "Kasepää"
        norwegian = _extract_cities_from_text(
            'SiiNunit { city_data : city.are { city_name: "Åre" '
            'city_name_localized: "@@Åre@@" } }', "sample"
        )[0]
        assert norwegian.locale_key == "Åre"

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

        # Some legacy sign files contain UTF-8 bytes that were previously
        # expanded as latin-1 (Ð/Ñ mojibake).  They must be repaired before
        # becoming editable hint entries.
        mojibake_sign = (
            'SiiNunit { sign_template_text : sign.rus { '
            'text: "Ð¥Ð\x90Ð\x91Ð\x90Ð\xa0Ð\x9eÐ\x92Ð¡Ð\x9a" } }'
        )
        repaired_hints = _extract_hint_texts_from_text(mojibake_sign, "sample")
        assert repaired_hints and repaired_hints[0].text == "ХАБАРОВСК"

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
            zf.writestr("def/city.sii", "SiiNunit {}")
            zf.writestr(
                "./locale/zh_cn/custom_strings.sui",
                'SiiNunit { localization_db : .localization { '
                'key[]: "CustomKey" val[]: "模组自带翻译" } }',
            )
        _, custom_locales = collect_all_def_files(
            [(str(custom_locale_mod), "Custom Locale")]
        )
        assert custom_locales["zh_cn"]["CustomKey"] == "模组自带翻译"

        # A standalone localization package may contain no def tree at all.
        # It still contributes translations for definitions from another Mod.
        locale_only_mod = Path(tmp) / "locale_only.scs"
        with zipfile.ZipFile(locale_only_mod, "w") as zf:
            zf.writestr(
                "locale/zh_cn/local_module.translation.sii",
                'SiiNunit { localization_db : .localization { '
                'key[]: "MapCity" val[]: "地图城市" } }',
            )
        locale_only_defs, locale_only_values = collect_all_def_files(
            [(str(locale_only_mod), "Localization Only")]
        )
        assert locale_only_defs == {}
        assert locale_only_values["zh_cn"]["MapCity"] == "地图城市"

        # The official language archive supplies base-game translations at
        # the lowest priority.  Any active Mod locale must be able to replace
        # it, including when the key spelling differs only by case.
        official_locale = Path(tmp) / "official_locale.scs"
        with zipfile.ZipFile(official_locale, "w") as zf:
            zf.writestr(
                "locale/zh_cn/local_module.base.sii",
                'SiiNunit { localization_db : .localization { '
                'key[]: "BaseCity" val[]: "官方城市" '
                'key[]: "Poti" val[]: "官方波季" } }',
            )
        override_locale = Path(tmp) / "override_locale.scs"
        with zipfile.ZipFile(override_locale, "w") as zf:
            zf.writestr(
                "locale/zh_cn/local_module.override.sii",
                'SiiNunit { localization_db : .localization { '
                'key[]: "poti" val[]: "模组波季" } }',
            )
        _, combined_locales = collect_all_def_files(
            [(str(override_locale), "Override Locale")],
            official_locale_path=official_locale,
        )
        combined_service = L10nService(Path(tmp) / "combined")
        combined_service.set_native_locale(combined_locales["zh_cn"])
        assert combined_service.translate("BaseCity").translated == "官方城市"
        assert combined_service.translate("POTI").translated == "模组波季"

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

        # The archive scanner visits the UI list from bottom to top.
        scan_order = []
        collect_all_def_files(
            [(str(high), "High"), (str(low), "Low")],
            progress=lambda _cur, _total, name: scan_order.append(name),
        )
        assert scan_order == ["Low", "High"]

        no_def = Path(tmp) / "no_def.scs"
        with zipfile.ZipFile(no_def, "w") as zf:
            zf.writestr("manifest.sii", "SiiNunit {}")
        assert _source_has_def_tree(no_def) is False
        assert _source_has_l10n_defs(no_def) is False
        skipped_progress = []
        collect_all_def_files(
            [(str(no_def), "Not a map")],
            progress=lambda *_args: skipped_progress.append(_args),
        )
        assert skipped_progress == []

        non_map_def = Path(tmp) / "non_map_def.scs"
        with zipfile.ZipFile(non_map_def, "w") as zf:
            zf.writestr("def/vehicle/truck/example.sii", "SiiNunit {}")
        assert _source_has_def_tree(non_map_def) is True
        assert _source_has_l10n_defs(non_map_def) is False
        assert _is_l10n_def_path("def/world/city/hidden.sui") is True
        assert _is_l10n_def_path("def/world/sign/road_sign.sii") is False

        sign_only = Path(tmp) / "sign_only.scs"
        with zipfile.ZipFile(sign_only, "w") as zf:
            zf.writestr(
                "def/sign/road.sii",
                'SiiNunit { sign_template_text : sign.road { '
                'text: "Road sign text" } }',
            )
        sign_progress = []
        sign_defs, _ = collect_all_def_files(
            [(str(sign_only), "Road Signs")],
            progress=lambda *_args: sign_progress.append(_args),
        )
        assert sign_defs == {}
        assert sign_progress == []

        translation_mod = Mod(
            mod_id="community_translation_pack",
            package_path=str(locale_only_mod),
            package_type="scs",
            manifest=ModManifest(
                package_name="community_translation_pack",
                display_name="Community Chinese Translation",
                categories=["other"],
            ),
        )
        assert _is_l10n_candidate_mod(translation_mod) is True
        category_translation_mod = Mod(
            mod_id="generic_pack",
            package_path=str(locale_only_mod),
            package_type="scs",
            manifest=ModManifest(
                package_name="generic_pack",
                display_name="Generic Pack",
                categories=["localization"],
            ),
        )
        assert _is_l10n_candidate_mod(category_translation_mod) is True

        promods_component = Mod(
            mod_id="promods-eu-model1-v282",
            package_path=str(no_def),
            package_type="scs",
            manifest=ModManifest(
                package_name="promods-eu-model1-v282",
                display_name="ProMods Europe Models",
                categories=["models"],
            ),
        )
        assert _is_l10n_candidate_mod(promods_component) is True
        truck_mod = Mod(
            mod_id="truck-skin-pack",
            package_path=str(no_def),
            package_type="scs",
            manifest=ModManifest(
                package_name="truck-skin-pack",
                display_name="Truck Skin Pack",
                categories=["truck"],
            ),
        )
        assert _is_l10n_candidate_mod(truck_mod) is False
        misleading_name = Mod(
            mod_id="navigation_map_zoom",
            package_path=str(no_def),
            package_type="scs",
            manifest=ModManifest(categories=["ui"]),
        )
        assert _is_l10n_candidate_mod(misleading_name) is False
        unknown_traffic = Mod(
            mod_id="ai_traffic_pack_by_jazzycat",
            package_path=str(no_def),
            package_type="scs",
        )
        assert _is_l10n_candidate_mod(unknown_traffic) is False
        ambiguous_map = Mod(
            mod_id="KazakhstanTgsRC",
            package_path=str(no_def),
            package_type="scs",
        )
        assert _is_l10n_candidate_mod(ambiguous_map) is True

        directory_mod = Path(tmp) / "directory_mod"
        (directory_mod / "def" / "city").mkdir(parents=True)
        (directory_mod / "vehicle").mkdir(parents=True)
        (directory_mod / "vehicle" / "large.bin").write_bytes(b"x")
        (directory_mod / "def" / "city" / "city.sii").write_text(
            'SiiNunit { city_data : city.test { city_name: "Test" } }',
            encoding="utf-8",
        )
        defs, _ = collect_all_def_files([(str(directory_mod), "Directory")])
        assert "def/city/city.sii" in defs

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
