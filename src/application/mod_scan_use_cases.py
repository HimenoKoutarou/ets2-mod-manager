"""Application facade for Mod discovery.

The current scanner remains the compatibility adapter.  Keeping tuple
unpacking out of new callers gives the future Rust scanner a stable DTO
without changing the established scanner API used by older code and tests.
"""
from __future__ import annotations

import time
from typing import Callable, Optional, Protocol

from .contracts import ModScanResult, ScanStatus


class ModScannerPort(Protocol):
    def scan(self, skip_manifest_parse: bool = False): ...


class ModScanUseCases:
    def __init__(self, scanner: ModScannerPort):
        self._scanner = scanner

    @property
    def scanner(self) -> ModScannerPort:
        return self._scanner

    def scan(
        self,
        *,
        skip_manifest_parse: bool = False,
        cancel_flag=None,
        progress: Optional[Callable] = None,
    ) -> ModScanResult:
        """Run the legacy scanner and normalize its result to ``ModScanResult``.

        ``cancel_flag`` and ``progress`` are accepted now so a Rust worker can
        later implement cooperative cancellation and event streaming without
        changing the Application API.  The current scanner is synchronous and
        does not expose per-item callbacks, so these hooks are intentionally
        best-effort compatibility hooks.
        """
        started = time.monotonic()
        if cancel_flag is not None and callable(getattr(cancel_flag, "is_set", None)) and cancel_flag.is_set():
            return ModScanResult(status=ScanStatus.CANCELLED, elapsed_ms=0)
        try:
            raw_mods, raw_new_ids = self._scanner.scan(skip_manifest_parse=skip_manifest_parse)
            result = ModScanResult(
                mods=list(raw_mods or []),
                new_mod_ids=list(raw_new_ids or []),
                status=ScanStatus.COMPLETED,
            )
            result.elapsed_ms = int((time.monotonic() - started) * 1000)
            if progress is not None:
                progress(result)
            return result
        except Exception as exc:
            result = ModScanResult(
                status=ScanStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
            )
            result.elapsed_ms = int((time.monotonic() - started) * 1000)
            return result
