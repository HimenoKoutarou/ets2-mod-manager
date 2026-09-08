"""Application facade for crash discovery, precheck, and log analysis."""
from __future__ import annotations

from typing import Protocol

from .contracts import (
    CrashAnalyzeResult,
    CrashPair,
    PrecheckReport,
)


class CrashDiagnosisPort(Protocol):
    def discover_latest_crash_pair(self): ...
    def precheck_active_mods(self, profile, all_mods, **kwargs): ...
    def analyze_crashlog(self, crash_path, log_path=None, **kwargs): ...


class CrashDiagnosisUseCases:
    """Thin orchestration boundary; the existing diagnostic engine stays intact."""

    def __init__(self, service=None):
        if service is None:
            from services import crash_service
            service = crash_service
        self._service = service

    @property
    def service(self):
        return self._service

    def discover_latest_crash_pair(self):
        raw = self._service.discover_latest_crash_pair() or {}
        def _text(value):
            return str(value) if value is not None else None
        return CrashPair(
            crash=_text(raw.get("crash")),
            log=_text(raw.get("log")),
            source=_text(raw.get("source")),
        )

    def precheck_active_mods(self, profile, all_mods, **kwargs) -> PrecheckReport:
        return self._service.precheck_active_mods(profile, all_mods, **kwargs)

    def analyze_crashlog(
        self,
        crash_path,
        log_path=None,
        *,
        profile=None,
        all_mods=None,
    ) -> CrashAnalyzeResult:
        return self._service.analyze_crashlog(
            crash_path, log_path, profile=profile, all_mods=list(all_mods or [])
        )
