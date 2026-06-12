"""Phase 0 contract stub: ``ScanQuery`` exists (Phase 4 deliverable).

The body is xfail-strict so Phase 4 can flip it green by implementing
the real class. If Phase 4 accidentally lands a stub that doesn't
satisfy the contract, the xfail unexpectedly passes → strict mode turns
that into a failure, surfacing the regression.
"""

from __future__ import annotations

import pytest


@pytest.mark.xfail(
    strict=True,
    reason="Phase 4 deliverable — ScanQuery not yet implemented",
)
def test_scan_query_importable_and_constructable() -> None:
    from liq.scan.query import ScanQuery  # noqa: PLC0415 — Phase 4 module

    assert ScanQuery is not None
