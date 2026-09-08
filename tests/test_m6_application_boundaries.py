"""M6 tests for technology-neutral Application DTOs and facades."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from application.category_use_cases import CategoryUseCases
from application.contracts import (
    CategoryMutationResult,
    ModScanResult,
    ProgressEvent,
    ProgressStatus,
    ScanStatus,
)
from application.crash_diagnosis_use_cases import CrashDiagnosisUseCases
from application.mod_scan_use_cases import ModScanUseCases
from services.crash_service import CrashAnalyzeResult, CrashSuspicion, PrecheckDepth


class _Scanner:
    def scan(self, skip_manifest_parse=False):
        return (["mod-a", "mod-b"], ["mod-b"])


class _Categories:
    def __init__(self):
        self.folders = ["Maps"]
        self.mapping = {"a": "Maps"}

    def all_folders(self): return list(self.folders)
    def stats(self): return {"": 0, "Maps": 1}
    def get_category(self, mod_id): return self.mapping.get(mod_id, "")
    def mods_in_category(self, key): return {m for m, c in self.mapping.items() if c == key}
    def create_folder(self, name):
        if not name or name in self.folders: return False
        self.folders.append(name); return True
    def rename_folder(self, old, new):
        if new in self.folders: return -1
        if old not in self.folders: return 0
        self.folders[self.folders.index(old)] = new
        n = 0
        for mid, cat in list(self.mapping.items()):
            if cat == old: self.mapping[mid] = new; n += 1
        return n
    def delete_folder(self, name):
        if name not in self.folders: return 0
        self.folders.remove(name)
        n = 0
        for mid, cat in list(self.mapping.items()):
            if cat == name: self.mapping[mid] = ""; n += 1
        return n
    def set_category(self, mod_id, category): self.mapping[mod_id] = category
    def set_categories_bulk(self, mapping): self.mapping.update(mapping)
    def touch_and_detect_new(self, scanned_ids, name_hints=None): return ([], list(scanned_ids))
    def save(self, force=False): return None


def test_mod_scan_facade_normalizes_legacy_tuple():
    result = ModScanUseCases(_Scanner()).scan(skip_manifest_parse=True)
    assert isinstance(result, ModScanResult)
    assert result.status is ScanStatus.COMPLETED
    assert result.mods == ["mod-a", "mod-b"]
    assert result.new_mod_ids == ["mod-b"]
    assert result.snapshot().new_mod_ids == ("mod-b",)


def test_mod_scan_facade_honors_pre_cancelled_flag():
    class Cancelled:
        def is_set(self): return True

    result = ModScanUseCases(_Scanner()).scan(cancel_flag=Cancelled())
    assert result.status is ScanStatus.CANCELLED
    assert result.mods == []


def test_category_facade_returns_state_and_mutation_dto():
    repo = _Categories()
    use_cases = CategoryUseCases(repo)
    state = use_cases.snapshot()
    assert state.folders == ("Maps",)
    assert state.count("Maps") == 1
    created = use_cases.create_folder("Traffic")
    assert isinstance(created, CategoryMutationResult) and created.ok
    conflict = use_cases.rename_folder("Maps", "Traffic")
    assert conflict.conflict and not conflict.ok
    use_cases.set_categories_bulk({"b": "Traffic"})
    assert use_cases.get_category("b") == "Traffic"


def test_crash_service_reexports_application_contract_types():
    assert CrashSuspicion.S.value == "S"
    assert PrecheckDepth.L0_FAST.value == "L0"
    result = CrashAnalyzeResult("", "", "", "", [], 0, [])
    assert isinstance(result, CrashAnalyzeResult)


def test_crash_facade_normalizes_path_dict_to_dto():
    class Service:
        def discover_latest_crash_pair(self):
            return {"crash": Path("C:/game.crash.txt"), "log": None, "source": "ets2"}

    pair = CrashDiagnosisUseCases(Service()).discover_latest_crash_pair()
    assert pair.crash == "C:\\game.crash.txt"
    assert pair.log is None
    assert pair.source == "ets2"


def test_progress_event_is_transport_neutral():
    event = ProgressEvent("mod_scan", phase="discover", current=2, total=4,
                          status=ProgressStatus.RUNNING)
    assert event.ratio == 0.5


class M6ApplicationBoundaryTests(unittest.TestCase):
    """Also expose the tests to the repository's unittest discovery command."""

    def test_scan_facade(self):
        test_mod_scan_facade_normalizes_legacy_tuple()

    def test_scan_cancel(self):
        test_mod_scan_facade_honors_pre_cancelled_flag()

    def test_category_facade(self):
        test_category_facade_returns_state_and_mutation_dto()

    def test_crash_contract_reexport(self):
        test_crash_service_reexports_application_contract_types()

    def test_progress_contract(self):
        test_progress_event_is_transport_neutral()

    def test_crash_pair_contract(self):
        test_crash_facade_normalizes_path_dict_to_dto()
