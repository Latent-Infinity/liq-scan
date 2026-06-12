"""Exception hierarchy for liq-scan.

Mirrors plan §Error Handling Standards. Concrete error subclasses are
imported by later modules; here we declare the base + the load-bearing
leaf errors so the contract tests can reference them.

All ``LiqScanError`` subclasses carry an ``event`` (snake_case) and
``correlation_id`` for structured logging — wired in the modules that
raise them, not on the base, so the constructor stays inspection-free.
"""

from __future__ import annotations


class LiqScanError(Exception):
    """Base class for every liq-scan error."""


class ScanValidationError(LiqScanError):
    """Caller supplied a malformed ``ScanQuery`` or predicate config."""


class CoverageGapError(LiqScanError):
    """FR-9 loud fail: required (symbol × window) not covered in the manifest.

    The exception payload enumerates the gap (per-symbol missing ranges)
    so the operator can act on it without re-running the scan.
    """


class NonPITUniverseError(LiqScanError):
    """I-7: a sweep refused a non-PIT resolved universe.

    Raised before any read so non-PIT data cannot contaminate a label
    artifact even by accident.
    """


class ScanSchemaError(LiqScanError):
    """Persisted scan-run was written by a newer/unknown schema version."""


class ScanPersistenceError(LiqScanError):
    """Failure while writing or finalizing a persisted scan run."""


__all__ = [
    "CoverageGapError",
    "LiqScanError",
    "NonPITUniverseError",
    "ScanPersistenceError",
    "ScanSchemaError",
    "ScanValidationError",
]
