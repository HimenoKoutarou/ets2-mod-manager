"""Stable DTOs exchanged by the application, workers, and UI adapters.

The contracts deliberately contain plain Python values (strings, numbers,
lists, tuples, and mappings).  They are the migration seam for the future
C#/.NET application and Rust scanning core; Qt widgets and service internals
must not be required to construct or consume them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Tuple


class ScanStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ProgressStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class ProgressEvent:
    """Technology-neutral progress notification for a long-running action."""

    operation: str
    phase: str = ""
    current: int = 0
    total: int = 0
    item: str = ""
    status: ProgressStatus = ProgressStatus.RUNNING
    message: str = ""

    @property
    def ratio(self) -> Optional[float]:
        if self.total <= 0:
            return None
        return max(0.0, min(1.0, self.current / self.total))


@dataclass(frozen=True)
class CancellationEvent:
    """Emitted when a cancellable operation stops before completion."""

    operation: str
    reason: str = "cancelled"


@dataclass(frozen=True)
class ModScanSnapshot:
    """Immutable scan projection suitable for caching or cross-process DTOs."""

    mods: Tuple[Any, ...] = ()
    new_mod_ids: Tuple[str, ...] = ()
    scanned_count: int = 0
    elapsed_ms: int = 0
    status: ScanStatus = ScanStatus.COMPLETED
    error: Optional[str] = None


@dataclass
class ModScanResult:
    """Mutable compatibility result returned by the Mod scan use case."""

    mods: List[Any] = field(default_factory=list)
    new_mod_ids: List[str] = field(default_factory=list)
    scanned_count: int = 0
    elapsed_ms: int = 0
    status: ScanStatus = ScanStatus.COMPLETED
    error: Optional[str] = None

    def __post_init__(self) -> None:
        self.mods = list(self.mods or [])
        self.new_mod_ids = [str(x) for x in (self.new_mod_ids or []) if x]
        if self.scanned_count <= 0:
            self.scanned_count = len(self.mods)

    def snapshot(self) -> ModScanSnapshot:
        return ModScanSnapshot(
            mods=tuple(self.mods),
            new_mod_ids=tuple(self.new_mod_ids),
            scanned_count=int(self.scanned_count),
            elapsed_ms=int(self.elapsed_ms),
            status=self.status,
            error=self.error,
        )


@dataclass(frozen=True)
class CategoryState:
    """Current category folders and aggregate counts."""

    folders: Tuple[str, ...] = ()
    stats: Mapping[str, int] = field(default_factory=dict)

    def count(self, category: str) -> int:
        return int(self.stats.get(category or "", 0) or 0)


@dataclass(frozen=True)
class CategoryMutationResult:
    """Outcome of a category/folder mutation."""

    operation: str
    success: bool
    affected_mods: int = 0
    conflict: bool = False
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.success


class Severity(str, Enum):
    RED = "red"
    YELLOW = "yellow"
    GREEN = "green"


class PrecheckDepth(str, Enum):
    L0_FAST = "L0"
    L1_MED = "L1"
    L2_DEEP = "L2"
    L3_HIST = "L3"


class CrashSuspicion(str, Enum):
    S = "S"
    A = "A"
    B = "B"


@dataclass
class PrecheckIssue:
    mod_id: str
    mod_display_name: str
    priority_index: Optional[int]
    severity: Severity
    layer: PrecheckDepth
    check_code: str
    evidence: str
    suggestion: str
    extra: dict = field(default_factory=dict)


@dataclass
class PrecheckReport:
    profile_id: str
    scanned_mods: int
    total_issues: int
    red_count: int
    yellow_count: int
    issues: List[PrecheckIssue]
    elapsed_ms: int


@dataclass
class CrashSuspectMod:
    rank: int
    suspicion: CrashSuspicion
    mod_id: str
    mod_display_name: str
    priority_index: Optional[int]
    evidence_lines: List[str]
    evidence_line_range: Tuple[int, int]


@dataclass
class CrashAnalyzeResult:
    crash_time: str
    build_version: str
    exception_code: str
    fault_module_category: str
    suspects: List[CrashSuspectMod]
    failed_to_match: int
    raw_tail_lines: List[str]


@dataclass(frozen=True)
class CrashPair:
    """Discovered crash/log files plus their game source."""

    crash: Optional[str] = None
    log: Optional[str] = None
    source: Optional[str] = None
