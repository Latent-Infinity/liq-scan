"""Phase 0 contract stub: ``ScanEngine.execute`` exists (Phase 4 deliverable).

Strict xfail; flips green when Phase 4 wires the engine to
``DataService`` + ``TimeSeriesStore`` per plan §3.8.
"""

from __future__ import annotations

import pytest


@pytest.mark.xfail(
    strict=True,
    reason="Phase 4 deliverable — ScanEngine.execute not yet implemented",
)
def test_scan_engine_execute_signature_exists() -> None:
    from liq.scan.engine import ScanEngine  # noqa: PLC0415 — Phase 4 module

    assert callable(getattr(ScanEngine, "execute", None))
