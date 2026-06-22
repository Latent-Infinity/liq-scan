"""Exception hierarchy for liq-scan.

Concrete error subclasses are imported by later modules; here we
declare the base + the load-bearing leaf errors so tests and callers
can reference them.

All ``LiqScanError`` subclasses carry an ``event`` (snake_case) and
``correlation_id`` for structured logging — wired in the modules that
raise them, not on the base, so the constructor stays inspection-free.
"""

from __future__ import annotations

from datetime import datetime


class LiqScanError(Exception):
    """Base class for every liq-scan error."""


class ScanValidationError(LiqScanError):
    """Caller supplied a malformed ``ScanQuery`` or predicate config."""


class CoverageGapError(LiqScanError):
    """Required (symbol × window) not covered in the manifest.

    Carries ``missing`` — a list of ``(symbol, gap_ranges)`` pairs
    enumerating uncovered slices per symbol — so the operator can act
    on it without re-running the scan.
    """

    def __init__(
        self,
        message: str,
        *,
        missing: list[tuple[str, list[tuple[datetime, datetime]]]] | None = None,
    ) -> None:
        super().__init__(message)
        self.missing: list[tuple[str, list[tuple[datetime, datetime]]]] = missing or []


class NonPITUniverseError(LiqScanError):
    """A sweep refused a non-point-in-time resolved universe.

    Raised before any read so non-PIT data cannot contaminate a label
    artifact even by accident.
    """


class LeakageViolation(LiqScanError):
    """A scanner artifact referenced information unavailable at its anchor."""


class ScanSchemaError(LiqScanError):
    """Persisted scan-run was written by a newer/unknown schema version."""


class ScanPersistenceError(LiqScanError):
    """Failure while writing or finalizing a persisted scan run."""


__all__ = [
    "CoverageGapError",
    "LeakageViolation",
    "LiqScanError",
    "NonPITUniverseError",
    "ScanPersistenceError",
    "ScanSchemaError",
    "ScanValidationError",
]
