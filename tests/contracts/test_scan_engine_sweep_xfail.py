"""Contract stub for ``ScanEngine.sweep``.

Strict xfail; flips green when the historical sweep + persistence
land. ``sweep`` must refuse non-PIT universes before any read — that
requirement gets its own real test alongside the implementation.
"""

from __future__ import annotations

import pytest


@pytest.mark.xfail(
    strict=True,
    reason="ScanEngine.sweep not yet implemented (planned)",
)
def test_scan_engine_sweep_signature_exists() -> None:
    from liq.scan.engine import ScanEngine  # noqa: PLC0415 — target module

    assert callable(getattr(ScanEngine, "sweep", None))
