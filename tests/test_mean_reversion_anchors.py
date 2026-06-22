from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from liq.scan.anchors.mean_reversion import (
    AnchorEvent,
    AnchorVolSource,
    MeanReversionAnchor,
    compute_anchor_event_id,
)


def _vol_source() -> AnchorVolSource:
    return AnchorVolSource(
        estimator="range_mean",
        lookback=3,
        min_periods=3,
        calendar_policy="bar_count",
        availability_ts=datetime(2024, 6, 3, 14, 0, tzinfo=UTC),
    )


def test_anchor_event_id_is_deterministic_and_direction_sensitive() -> None:
    ts = datetime(2024, 6, 3, 14, 0, tzinfo=UTC)

    first = compute_anchor_event_id("run-1", "SPY", ts, "query-v1", "up")
    second = compute_anchor_event_id("run-1", "SPY", ts, "query-v1", "up")
    changed = compute_anchor_event_id("run-1", "SPY", ts, "query-v1", "down")

    assert first == second
    assert first != changed
    assert len(first) == 64


def test_anchor_models_round_trip_and_are_frozen() -> None:
    ts = datetime(2024, 6, 3, 14, 0, tzinfo=UTC)
    anchor_id = compute_anchor_event_id("run-1", "SPY", ts, "query-v1", "up")
    anchor = MeanReversionAnchor(
        symbol="SPY",
        anchor_ts=ts,
        direction="up",
        anchor_event_id=anchor_id,
        scan_run_id="run-1",
        scan_query_version="query-v1",
        metric_version="midrange-excursion-v1",
        resolved_universe_version="universe-v1",
        quality_flags=("warm",),
        excursion_units=Decimal("2.5"),
        midrange_now=Decimal("105"),
        midrange_base=Decimal("100"),
        reversion_target=Decimal("100"),
        vol_t=Decimal("2"),
        anchor_vol_source=_vol_source(),
    )

    restored = MeanReversionAnchor.model_validate(anchor.model_dump())

    assert restored == anchor
    assert restored.regime_at_anchor is None
    with pytest.raises(ValidationError):
        restored.symbol = "QQQ"


def test_anchor_event_uses_anchor_timestamp_not_as_of() -> None:
    event = AnchorEvent(
        symbol="SPY",
        anchor_ts=datetime(2024, 6, 3, 14, 0, tzinfo=UTC),
        direction="down",
        anchor_event_id="a" * 64,
        scan_run_id="run-1",
        scan_query_version="query-v1",
        metric_version="midrange-excursion-v1",
        resolved_universe_version="universe-v1",
        quality_flags=(),
    )

    dumped = event.model_dump()

    assert "anchor_ts" in dumped
    assert "as_of" not in dumped
    assert "resolved_universe_version" in dumped
    assert "universe_version" not in dumped
