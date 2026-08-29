"""PriorityService：基于 package_set 的分类整体块移动 TDD 用例。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from core.models import Mod, ModManifest
from services.priority_service import PriorityService


def _active_order(worklist):
    return [e["package_name"] for e in worklist if e["enabled"]]


def test_indices_for_category_returns_enabled_only_and_preserves_order():
    svc = PriorityService([])
    all_pkg = ["A", "B", "C", "D", "E", "F"]
    active = ["A", "B", "C", "D"]
    wl = svc.build_worklist(active, all_pkg)
    pkg_set = {"B", "D", "E"}
    indices = svc.indices_for_category(wl, pkg_set)
    assert indices == [1, 3], f"indices={indices}"
    assert indices == sorted(indices)


def test_move_down_by_package_set_keeps_relative_order():
    svc = PriorityService([])
    all_pkg = ["A", "B", "C", "D", "E"]
    active = ["A", "B", "C", "D", "E"]
    wl = svc.build_worklist(active, all_pkg)
    new_wl = svc.move_down_by_package_set(wl, {"A", "B"}, steps=1)
    order = _active_order(new_wl)
    assert order == ["C", "A", "B", "D", "E"], f"order={order}"
    assert order.index("A") < order.index("B")


def test_move_up_by_package_set_boundary_and_relative_order():
    svc = PriorityService([])
    all_pkg = ["A", "B", "C", "D", "E", "F"]
    active = ["C", "D", "A", "B", "E", "F"]
    wl = svc.build_worklist(active, all_pkg)
    new_wl = svc.move_up_by_package_set(wl, {"A", "B"}, steps=100)
    order = _active_order(new_wl)
    assert order[:2] == ["A", "B"], f"first 2={order[:2]}"
    rest = [x for x in order if x not in {"A", "B"}]
    assert rest == ["C", "D", "E", "F"], f"rest={rest}"


def test_move_top_bottom_by_package_set():
    svc = PriorityService([])
    all_pkg = ["A", "B", "C", "D", "E"]
    active = ["A", "C", "D", "B", "E"]
    wl = svc.build_worklist(active, all_pkg)
    new_wl = svc.move_top_by_package_set(wl, {"C", "D"})
    order = _active_order(new_wl)
    assert order[:2] == ["C", "D"] and order.index("C") < order.index("D"), f"top order={order}"
    wl2 = svc.build_worklist(["A", "C", "D", "B", "E"], all_pkg)
    new_wl2 = svc.move_bottom_by_package_set(wl2, {"C", "D"})
    order2 = _active_order(new_wl2)
    tail = order2[-2:]
    assert tail == ["C", "D"], f"tail={tail}, order2={order2}"
    assert order2.index("C") < order2.index("D")


def test_empty_pkg_set_does_not_mutate_worklist():
    svc = PriorityService([])
    wl = svc.build_worklist(["A", "B", "C"], ["A", "B", "C"])
    before = [dict(e) for e in wl]
    svc.move_up_by_package_set(wl, set(), steps=1)
    svc.move_down_by_package_set(wl, set(), steps=1)
    svc.move_top_by_package_set(wl, set())
    svc.move_bottom_by_package_set(wl, set())
    assert before == [dict(e) for e in wl], "empty set should not mutate"


def test_batch_toggle_clears_disabled_priority_index():
    svc = PriorityService([])
    wl = svc.build_worklist(["A", "B"], ["A", "B", "C"])
    changed = svc.batch_toggle(wl, [0], action="disable")
    disabled = next(e for e in changed if e["package_name"] == "A")
    enabled = next(e for e in changed if e["package_name"] == "B")
    assert disabled["order"] == -1
    assert disabled["priority_index"] is None
    assert enabled["order"] == 0
    assert enabled["priority_index"] == 0


def test_rebuild_from_active_uses_known_mods_and_sets_order_fields():
    mod = Mod(
        mod_id="A",
        package_path="A",
        package_type="directory",
        manifest=ModManifest(package_name="A", display_name="A"),
    )
    svc = PriorityService([mod])
    rebuilt = svc.rebuild_from_active(svc, [], ["A"])
    assert len(rebuilt) == 1
    assert rebuilt[0]["mod"] is mod
    assert rebuilt[0]["enabled"] is True
    assert rebuilt[0]["order"] == 0
    assert rebuilt[0]["priority_index"] == 0


if __name__ == "__main__":
    import traceback
    failed = 0
    tests = [
        test_indices_for_category_returns_enabled_only_and_preserves_order,
        test_move_down_by_package_set_keeps_relative_order,
        test_move_up_by_package_set_boundary_and_relative_order,
        test_move_top_bottom_by_package_set,
        test_empty_pkg_set_does_not_mutate_worklist,
        test_batch_toggle_clears_disabled_priority_index,
        test_rebuild_from_active_uses_known_mods_and_sets_order_fields,
    ]
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {t.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests)-failed}/{len(tests)} passed")
    sys.exit(0 if failed == 0 else 1)
