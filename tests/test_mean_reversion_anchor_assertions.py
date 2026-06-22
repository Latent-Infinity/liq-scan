from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import polars as pl
import pytest

from liq.scan.anchors.mean_reversion import (
    AnchorVolSource,
    MeanReversionAnchor,
    compute_anchor_event_id,
)
from liq.scan.anchors.mean_reversion.assertions import assert_anchor_pit
from liq.scan.exceptions import LeakageViolation


def _bars() -> pl.DataFrame:
    start = datetime(2024, 6, 3, 13, 30, tzinfo=UTC)
    return pl.DataFrame(
        {
            "timestamp": [start + timedelta(minutes=i) for i in range(4)],
            "value": [1.0, 2.0, 3.0, 4.0],
        }
    )


def _anchor(anchor_ts: datetime) -> MeanReversionAnchor:
    return MeanReversionAnchor(
        symbol="SPY",
        anchor_ts=anchor_ts,
        direction="up",
        anchor_event_id=compute_anchor_event_id("run-1", "SPY", anchor_ts, "query-v1", "up"),
        scan_run_id="run-1",
        scan_query_version="query-v1",
        metric_version="midrange-excursion-v1",
        resolved_universe_version="universe-v1",
        quality_flags=(),
        excursion_units=Decimal("2.5"),
        midrange_now=Decimal("105"),
        midrange_base=Decimal("100"),
        reversion_target=Decimal("100"),
        vol_t=Decimal("2"),
        anchor_vol_source=AnchorVolSource(
            estimator="range_mean",
            lookback=3,
            min_periods=3,
            calendar_policy="bar_count",
            availability_ts=anchor_ts,
        ),
    )


def test_assert_anchor_pit_accepts_prior_windows() -> None:
    bars = _bars()
    anchor = _anchor(bars["timestamp"][3])

    assert_anchor_pit(anchor, bars, {"vol_t": [0, 1, 2], "midrange_base": [0, 1, 2]})


def test_assert_anchor_pit_rejects_current_bar_reference() -> None:
    bars = _bars()
    anchor = _anchor(bars["timestamp"][3])

    with pytest.raises(LeakageViolation, match="anchor_event_id"):
        assert_anchor_pit(anchor, bars, {"vol_t": [1, 2, 3], "midrange_base": [0, 1, 2]})


def test_assert_anchor_pit_rejects_late_availability() -> None:
    bars = _bars()
    anchor_ts = bars["timestamp"][3]
    anchor = _anchor(anchor_ts).model_copy(
        update={
            "anchor_vol_source": AnchorVolSource(
                estimator="range_mean",
                lookback=3,
                min_periods=3,
                calendar_policy="bar_count",
                availability_ts=anchor_ts + timedelta(minutes=1),
            )
        }
    )

    with pytest.raises(LeakageViolation, match="availability_ts"):
        assert_anchor_pit(anchor, bars, {"vol_t": [0, 1, 2], "midrange_base": [0, 1, 2]})
