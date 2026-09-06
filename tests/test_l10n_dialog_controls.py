"""Regression checks for localization dialog close/export controls."""
from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from services.l10n_service import L10nResult, L10nService, TranslationEntry
from ui.l10n_dialog import L10nDialog


def main() -> int:
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as tmp:
        service = L10nService(Path(tmp))
        dialog = L10nDialog(service)
        dialog.result = L10nResult(cities=[TranslationEntry(
            source="City", translated="城市", status="local",
            source_mod="Sample", def_locale_key_present=True,
        )])
        dialog._entries = dialog.result.all_entries
        dialog.show()
        app.processEvents()
        dialog.btn_close.click()
        app.processEvents()
        assert not dialog.isVisible()

        # A result containing only pending rows is still exportable after the
        # user confirms the warning; the generated archive remains valid.
        dialog = L10nDialog(service)
        pending = TranslationEntry(
            source="Pending City", translated="", status="missing_locale",
            source_mod="Sample", def_locale_key_present=True,
        )
        dialog.result = L10nResult(cities=[pending])
        dialog._entries = [pending]
        output = Path(tmp) / "partial.scs"
        original_question = QMessageBox.question
        original_get_save = __import__("ui.l10n_dialog", fromlist=["QFileDialog"]).QFileDialog.getSaveFileName
        try:
            QMessageBox.question = staticmethod(lambda *args, **kwargs: QMessageBox.Yes)
            __import__("ui.l10n_dialog", fromlist=["QFileDialog"]).QFileDialog.getSaveFileName = staticmethod(
                lambda *args, **kwargs: (str(output), "SCS Mod (*.scs)")
            )
            dialog._do_export()
        finally:
            QMessageBox.question = original_question
            __import__("ui.l10n_dialog", fromlist=["QFileDialog"]).QFileDialog.getSaveFileName = original_get_save
        assert output.is_file()
        with zipfile.ZipFile(output) as zf:
            assert "manifest.sii" in zf.namelist()

    print("PASS localization dialog close and partial export")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
