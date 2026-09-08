from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ui.crash_check_dialog import _crash_log_updated


class CrashMonitorContractTests(unittest.TestCase):
    def test_only_newer_crash_log_is_crash_evidence(self):
        self.assertTrue(_crash_log_updated(10.0, 11.0))
        self.assertFalse(_crash_log_updated(10.0, 10.0))
        self.assertFalse(_crash_log_updated(10.0, 9.0))


if __name__ == "__main__":
    unittest.main()
