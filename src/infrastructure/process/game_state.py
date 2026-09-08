"""Windows process adapter used by write-side application boundaries."""
from __future__ import annotations

import subprocess


class WindowsGameState:
    """Detect whether ETS2 or ATS is currently running."""

    PROCESS_NAMES = ("eurotrucks2.exe", "amtrucks.exe")

    def is_running(self) -> bool:
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            for name in self.PROCESS_NAMES:
                result = subprocess.run(
                    ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
                    capture_output=True,
                    text=True,
                    creationflags=flags,
                    timeout=3,
                )
                if name.lower() in result.stdout.lower():
                    return True
        except Exception:
            # Preserve the existing cross-platform behavior: inability to
            # inspect processes should not make read-only/test workflows fail.
            return False
        return False
