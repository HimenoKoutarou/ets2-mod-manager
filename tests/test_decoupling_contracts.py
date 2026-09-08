"""解耦前的稳定行为契约测试。

这些测试只覆盖迁移期间必须保持的纯行为，不绑定具体 UI 或文件系统实现。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.sii_parser import parse_sii
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


if __name__ == "__main__":
    tests = [
        test_priority_profile_ui_roundtrip_is_stable,
        test_profile_active_mods_parser_preserves_profile_order,
        test_non_local_profile_is_rejected_at_service_boundary,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)}/{len(tests)} passed")
