"""Contract stub for ``ScanQuery`` (planned, not built).

The body is xfail-strict so the eventual implementation flips it green
just by satisfying the import. If a stub that doesn't actually satisfy
the contract lands, the xfail unexpectedly passes — strict mode turns
that into a failure, surfacing the regression.
"""

from __future__ import annotations

import pytest


@pytest.mark.xfail(
    strict=True,
    reason="ScanQuery not yet implemented (planned)",
)
def test_scan_query_importable_and_constructable() -> None:
    from liq.scan.query import ScanQuery  # noqa: PLC0415 — target module

    assert ScanQuery is not None
