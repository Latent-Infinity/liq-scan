"""Phase 0 contract stub: ``ScanEngine.sweep`` exists (Phase 5 deliverable).

Strict xfail; flips green when Phase 5 implements the historical sweep
+ persistence per plan §3.10. ``sweep`` must refuse non-PIT universes
(I-7) before any read — that requirement gets its own real test in
Phase 5.
"""

from __future__ import annotations

import pytest


@pytest.mark.xfail(
    strict=True,
    reason="Phase 5 deliverable — ScanEngine.sweep not yet implemented",
)
def test_scan_engine_sweep_signature_exists() -> None:
    from liq.scan.engine import ScanEngine  # noqa: PLC0415 — Phase 5 module

    assert callable(getattr(ScanEngine, "sweep", None))
