from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl

from liq.scan.anchors.mean_reversion import AnchorEvaluator
from liq.scan.predicates import MeanReversionExcursionPredicate


def _bars() -> pl.DataFrame:
    start = datetime(2024, 6, 3, 13, 30, tzinfo=UTC)
    rows = []
    for i, midrange in enumerate([100.0, 101.0, 99.0, 106.0]):
        rows.append(
            {
                "timestamp": start + timedelta(minutes=i),
                "open": midrange,
                "high": midrange + 1.0,
                "low": midrange - 1.0,
                "close": midrange,
                "volume": 1000,
            }
        )
    return pl.DataFrame(rows)


def _evaluator(direction: str = "up", k: float = 2.0) -> AnchorEvaluator:
    return AnchorEvaluator(
        predicate=MeanReversionExcursionPredicate(
            K=k,
            L_vol=3,
            L_base=3,
            base_kind="roll_mean",
            direction=direction,
            metric_version="midrange-excursion-v1",
        ),
        scan_run_id="run-1",
        scan_query_version="query-v1",
        metric_version="midrange-excursion-v1",
        resolved_universe_version="universe-v1",
    )


def test_evaluator_uses_prior_window_for_base_and_vol() -> None:
    anchors = _evaluator().evaluate_symbol("SPY", _bars())

    assert len(anchors) == 1
    anchor = anchors[0]
    assert anchor.anchor_ts == datetime(2024, 6, 3, 13, 33, tzinfo=UTC)
    assert float(anchor.midrange_base) == 100.0
    assert float(anchor.midrange_now) == 106.0
    assert float(anchor.vol_t) == 2.0
    assert float(anchor.excursion_units) == 3.0


def test_evaluator_returns_empty_for_empty_bars() -> None:
    empty = pl.DataFrame(
        schema={
            "timestamp": pl.Datetime(time_zone="UTC"),
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Int64,
        }
    )

    assert _evaluator().evaluate_symbol("SPY", empty) == []


def test_evaluator_drops_bars_with_non_datetime_timestamp() -> None:
    bars = _bars().with_columns(pl.col("timestamp").dt.date().alias("timestamp"))

    assert _evaluator().evaluate_symbol("SPY", bars) == []


def test_evaluator_skips_when_predicate_rejects() -> None:
    # K=10 is above the realized excursion of 3.0 — predicate rejects every candidate.
    assert _evaluator(k=10.0).evaluate_symbol("SPY", _bars()) == []
