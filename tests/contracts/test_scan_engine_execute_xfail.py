"""Contract stub for ``ScanEngine.execute`` (planned, not built).

Strict xfail; flips green when the engine is wired to ``DataService``
+ ``TimeSeriesStore`` per the liq-scan plan §3.8.
"""

from __future__ import annotations

import pytest


@pytest.mark.xfail(
    strict=True,
    reason="ScanEngine.execute not yet implemented (planned)",
)
def test_scan_engine_execute_signature_exists() -> None:
    from liq.scan.engine import ScanEngine  # noqa: PLC0415 — target module

    assert callable(getattr(ScanEngine, "execute", None))
