"""Ensure the save editor can initialize with the active application theme."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtWidgets import QApplication

from ui.save_editor_dialog import SaveEditorDialog


def main() -> int:
    app = QApplication.instance() or QApplication([])
    dialog = SaveEditorDialog(profile_svc=None, profiles=[])
    assert dialog.windowTitle()
    assert dialog.styleSheet()
    assert dialog.btn_save_as.text()
    assert dialog.edt_save_as_name.placeholderText()
    dialog.close()
    app.processEvents()
    print("PASS save editor theme initialization")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
