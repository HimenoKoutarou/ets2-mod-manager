"""解耦前的稳定行为契约测试。

这些测试只覆盖迁移期间必须保持的纯行为，不绑定具体 UI 或文件系统实现。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.sii_parser import parse_sii
from domain.mod_identity import aliases_match, profile_entry_aliases
from application.profile_use_cases import ProfileUseCases
from services.crash_service import PrecheckDepth, precheck_active_mods
from services.priority_service import PriorityService
from services.profile_service import ProfileInfo, ProfileService


def test_priority_profile_ui_roundtrip_is_stable():
    raw_profile_order = ["LOW", "MID", "HIGH"]
    service = PriorityService([])

    worklist = service.build_worklist(raw_profile_order, raw_profile_order)

    assert [row["package_name"] for row in worklist if row["enabled"]] == [
        "HIGH",
        "MID",
        "LOW",
    ]
    assert service.worklist_to_profile_active(worklist) == raw_profile_order


def test_profile_active_mods_parser_preserves_profile_order():
    text = """SiiNunit
{
 profile : _nameless.profile.profile {
  active_mods: 2
  active_mods[0]: \"low\"
  active_mods[1]: \"high|Display Name\"
 }
}
"""
    unit = parse_sii(text)[0]
    assert unit.get_indexed("active_mods") == ["low", "high|Display Name"]


def test_non_local_profile_is_rejected_at_service_boundary():
    profile = ProfileInfo(
        profile_id="cloud-profile",
        location="cloud",
        folder=Path("."),
        profile_sii=Path("profile.sii"),
    )

    try:
        ProfileService.ensure_local_profile(profile)
    except PermissionError:
        pass
    else:
        raise AssertionError("Cloud profiles must remain read-only")


def test_profile_alias_matches_scanned_mod_identity():
    class Manifest:
        package_name = "1254665283"
        display_name = "No Damage"

    class Mod:
        mod_id = "1254665283"
        display_title = "No Damage"
        package_path = "H:/mods/no_damage.scs"
        manifest = Manifest()

    assert aliases_match(
        "mod_workshop_package.000000004AC8AC43|No Damage",
        Mod(),
    )
    assert "no damage" in profile_entry_aliases("1254665283|No Damage")


def test_profile_use_case_enforces_game_closed_before_repository_write():
    class Repository:
        def set_active_mods(self, profile, new_mods, *, verify=False):
            return (profile, new_mods, verify)

    class RunningGame:
        def is_running(self):
            return True

    profile = ProfileInfo("p", "local", Path("."), Path("profile.sii"))
    use_cases = ProfileUseCases(Repository(), RunningGame())
    try:
        use_cases.replace_active_mods(profile, ["mod"])
    except RuntimeError:
        pass
    else:
        raise AssertionError("Profile mutation must be rejected while the game runs")


def test_crash_precheck_resolves_workshop_alias_without_missing_mod_issue():
    class Manifest:
        package_name = "1254665283"
        display_name = "No Damage"

    with tempfile.TemporaryDirectory() as td:
        class Mod:
            mod_id = "1254665283"
            display_title = "No Damage"
            package_path = str(Path(td) / "no_damage.scs")
            package_type = "directory"
            manifest = Manifest()
            priority_index = -1

        Path(Mod.package_path).mkdir()
        profile = type(
            "Profile",
            (),
            {
                "profile_id": "p",
                "active_mods": [
                    "mod_workshop_package.000000004AC8AC43|No Damage"
                ],
            },
        )()
        report = precheck_active_mods(
            profile,
            [Mod()],
            max_depth=PrecheckDepth.L0_FAST,
            use_l3_history=False,
        )
        assert not any(issue.check_code == "L0-1" for issue in report.issues)


if __name__ == "__main__":
    tests = [
        test_priority_profile_ui_roundtrip_is_stable,
        test_profile_active_mods_parser_preserves_profile_order,
        test_non_local_profile_is_rejected_at_service_boundary,
        test_profile_alias_matches_scanned_mod_identity,
        test_profile_use_case_enforces_game_closed_before_repository_write,
        test_crash_precheck_resolves_workshop_alias_without_missing_mod_issue,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)}/{len(tests)} passed")
