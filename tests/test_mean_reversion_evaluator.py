from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal, get_args

import polars as pl
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from liq.scan.anchors.mean_reversion import AnchorEvaluator
from liq.scan.anchors.mean_reversion.regime import RegimePredicate
from liq.scan.predicates import AndPredicate, MeanReversionExcursionPredicate, RegimeLabel

_REGIME_LABELS: tuple[RegimeLabel, ...] = get_args(Literal["trend", "chop", "indeterminate"])


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


def _evaluator_with_regime(
    labels: tuple[RegimeLabel, ...], adverse: tuple[RegimeLabel, ...]
) -> AnchorEvaluator:
    return AnchorEvaluator(
        predicate=AndPredicate(
            predicates=[
                MeanReversionExcursionPredicate(
                    K=2.0,
                    L_vol=3,
                    L_base=3,
                    base_kind="roll_mean",
                    direction="up",
                    metric_version="midrange-excursion-v1",
                ),
                RegimePredicate(labels=labels, adverse_labels=adverse),
            ]
        ),
        scan_run_id="run-1",
        scan_query_version="query-v1",
        metric_version="midrange-excursion-v1",
        resolved_universe_version="universe-v1",
    )


@given(
    labels=st.tuples(
        st.sampled_from(_REGIME_LABELS),
        st.sampled_from(_REGIME_LABELS),
        st.sampled_from(_REGIME_LABELS),
        st.sampled_from(_REGIME_LABELS),
    ),
    adverse_set=st.sets(st.sampled_from(_REGIME_LABELS), min_size=0, max_size=3),
)
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=50)
def test_regime_gate_count_is_bounded_by_ungated_count(
    labels: tuple[RegimeLabel, RegimeLabel, RegimeLabel, RegimeLabel],
    adverse_set: set[RegimeLabel],
) -> None:
    """For any label series and adverse-label set, regime gating cannot
    increase the number of anchors emitted by the ungated evaluator.
    """
    adverse = tuple(sorted(adverse_set))
    ungated_count = len(_evaluator().evaluate_symbol("SPY", _bars()))
    gated_count = len(_evaluator_with_regime(labels, adverse).evaluate_symbol("SPY", _bars()))

    assert gated_count <= ungated_count
    surviving_label = labels[3]  # only bar index 3 fires in the fixture
    if surviving_label in adverse and ungated_count > 0:
        assert gated_count == 0
    else:
        assert gated_count == ungated_count


def test_regime_predicate_suppresses_adverse_anchor_emits_log_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="liq.scan.anchors.mean_reversion")
    AnchorEvaluator(
        predicate=AndPredicate(
            predicates=[
                MeanReversionExcursionPredicate(
                    K=2.0,
                    L_vol=3,
                    L_base=3,
                    base_kind="roll_mean",
                    direction="up",
                    metric_version="midrange-excursion-v1",
                ),
                RegimePredicate(
                    labels=("chop", "chop", "chop", "trend"), adverse_labels=("trend",)
                ),
            ]
        ),
        scan_run_id="run-1",
        scan_query_version="query-v1",
        metric_version="midrange-excursion-v1",
        resolved_universe_version="universe-v1",
    ).evaluate_symbol("SPY", _bars())

    suppressions = [r for r in caplog.records if r.message == "anchor_suppressed_by_regime"]
    assert len(suppressions) == 1
    assert suppressions[0].symbol == "SPY"
    assert suppressions[0].bar_index == 3
    assert suppressions[0].regime_label == "trend"


def test_regime_predicate_suppresses_adverse_anchor() -> None:
    ungated = _evaluator().evaluate_symbol("SPY", _bars())
    gated = AnchorEvaluator(
        predicate=AndPredicate(
            predicates=[
                MeanReversionExcursionPredicate(
                    K=2.0,
                    L_vol=3,
                    L_base=3,
                    base_kind="roll_mean",
                    direction="up",
                    metric_version="midrange-excursion-v1",
                ),
                RegimePredicate(
                    labels=("chop", "chop", "chop", "trend"), adverse_labels=("trend",)
                ),
            ]
        ),
        scan_run_id="run-1",
        scan_query_version="query-v1",
        metric_version="midrange-excursion-v1",
        resolved_universe_version="universe-v1",
    ).evaluate_symbol("SPY", _bars())

    assert len(ungated) == 1
    assert gated == []


def test_regime_at_anchor_passes_through_permissive_label() -> None:
    anchors = AnchorEvaluator(
        predicate=AndPredicate(
            predicates=[
                MeanReversionExcursionPredicate(
                    K=2.0,
                    L_vol=3,
                    L_base=3,
                    base_kind="roll_mean",
                    direction="up",
                    metric_version="midrange-excursion-v1",
                ),
                RegimePredicate(labels=("trend", "trend", "trend", "chop"), adverse_labels=()),
            ]
        ),
        scan_run_id="run-1",
        scan_query_version="query-v1",
        metric_version="midrange-excursion-v1",
        resolved_universe_version="universe-v1",
    ).evaluate_symbol("SPY", _bars())

    assert len(anchors) == 1
    assert anchors[0].regime_at_anchor == "chop"
