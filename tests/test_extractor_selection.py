from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "src"))

from services import external_extractor_service as extractor


class ExtractorSelectionTest(unittest.TestCase):
    def setUp(self):
        self._original = {
            "legacy": extractor._EXTRACTOR,
            "modern": extractor._EXTRACTOR_MODERN,
            "config": extractor._EXTRACTOR_CONFIG_PATH,
            "mode_cache": extractor._extractor_mode_cache,
        }
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        extractor._EXTRACTOR = root / "extractor.exe"
        extractor._EXTRACTOR_MODERN = root / "extractor-2025-10-21.exe"
        extractor._EXTRACTOR.write_bytes(b"legacy")
        extractor._EXTRACTOR_MODERN.write_bytes(b"modern")
        extractor._EXTRACTOR_CONFIG_PATH = root / "config" / "extractor.json"
        extractor._extractor_mode_cache = None

    def tearDown(self):
        extractor._EXTRACTOR = self._original["legacy"]
        extractor._EXTRACTOR_MODERN = self._original["modern"]
        extractor._EXTRACTOR_CONFIG_PATH = self._original["config"]
        extractor._extractor_mode_cache = self._original["mode_cache"]
        self._temp.cleanup()

    def test_default_preserves_legacy_engine(self):
        self.assertEqual(extractor.get_extractor_mode(), extractor.EXTRACTOR_MODE_LEGACY)
        self.assertEqual(extractor._extractor_candidates(), [extractor._EXTRACTOR])

    def test_new_and_auto_modes_are_persisted_and_ordered(self):
        self.assertTrue(extractor.set_extractor_mode(extractor.EXTRACTOR_MODE_MODERN))
        self.assertEqual(extractor._extractor_candidates(), [extractor._EXTRACTOR_MODERN])
        modern_key = extractor._cache_key(Path(self._temp.name) / "sample.scs")

        extractor._extractor_mode_cache = None
        self.assertEqual(extractor.get_extractor_mode(), extractor.EXTRACTOR_MODE_MODERN)

        self.assertTrue(extractor.set_extractor_mode(extractor.EXTRACTOR_MODE_AUTO))
        self.assertEqual(
            extractor._extractor_candidates(),
            [extractor._EXTRACTOR_MODERN, extractor._EXTRACTOR],
        )
        auto_key = extractor._cache_key(Path(self._temp.name) / "sample.scs")
        self.assertNotEqual(modern_key, auto_key)

    def test_unavailable_engine_is_not_reported_as_candidate(self):
        extractor._EXTRACTOR_MODERN.unlink()
        self.assertTrue(extractor.set_extractor_mode(extractor.EXTRACTOR_MODE_MODERN))
        self.assertEqual(extractor._extractor_candidates(), [])
        self.assertFalse(extractor.extractor_availability()[extractor.EXTRACTOR_MODE_MODERN])


if __name__ == "__main__":
    unittest.main()
