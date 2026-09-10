"""Regression test for editing locale keys whose value is empty."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtWidgets import QApplication

from services.l10n_service import L10nResult, L10nService
from ui.l10n_dialog import L10nDialog


def main() -> int:
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as tmp:
        service = L10nService(Path(tmp))
        service.set_native_locale({"BlankKey": ""})
        entry = service.translate("BlankKey", "city", "Sample", allow_api=False)

        dialog = L10nDialog(service)
        dialog.result = L10nResult(cities=[entry])
        dialog._entries = [entry]
        dialog._fill_table(dialog.tab_cities, dialog.result.cities)

        assert dialog.tab_cities.item(0, 1).text() == ""
        assert dialog.tab_cities.item(0, 3).text() == "缺少 value，可填写"

        dialog.tab_cities.item(0, 1).setText("用户填写")
        app.processEvents()
        assert entry.translated == "用户填写"
        assert entry.status == "local"
        assert service.translate("BlankKey").translated == "用户填写"

        dialog.tab_cities.item(0, 1).setText("")
        app.processEvents()
        assert entry.translated == ""
        assert entry.status == "missing_value"
        assert dialog.tab_cities.item(0, 3).text() == "缺少 value，可填写"

        dialog.close()

    print("PASS blank locale values remain editable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
