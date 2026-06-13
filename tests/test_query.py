"""Tests for ``ScanQuery`` and ``ScanResult`` domain types."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from liq.scan.predicates import (
    MovePredicate,
)
from liq.scan.query import ScanQuery, ScanResult
from liq.scan.window import TradingMinutesWindow


def _basic_query(**overrides: object) -> ScanQuery:
    base: dict[str, object] = {
        "universe_ref": ["AAPL", "MSFT"],
        "as_of": datetime(2024, 6, 3, 20, 0, tzinfo=UTC),
        "window": TradingMinutesWindow(kind="trading_minutes", n=30),
        "predicate": MovePredicate(threshold_pct=5.0, direction="either"),
        "ranking": "abs_move",
        "limit": 10,
    }
    base.update(overrides)
    return ScanQuery(**base)  # type: ignore[arg-type]


class TestScanQueryHappyPath:
    def test_basic_query_constructs(self) -> None:
        q = _basic_query()
        assert q.universe_ref == ["AAPL", "MSFT"]
        assert q.ranking == "abs_move"
        assert q.split_handling == "adjust"
        assert q.metric_version == "midrange-endpoint-v1"
        assert q.include_extended_hours is False
        assert q.limit == 10

    def test_accepts_universe_name_string(self) -> None:
        q = _basic_query(universe_ref="sp500")
        assert q.universe_ref == "sp500"

    def test_accepts_no_limit(self) -> None:
        q = _basic_query(limit=None)
        assert q.limit is None


class TestScanQueryValidation:
    def test_rejects_negative_limit(self) -> None:
        with pytest.raises(ValidationError):
            _basic_query(limit=-1)

    def test_rejects_zero_limit(self) -> None:
        with pytest.raises(ValidationError):
            _basic_query(limit=0)

    def test_rejects_unknown_ranking(self) -> None:
        with pytest.raises(ValidationError):
            _basic_query(ranking="random")

    def test_rejects_empty_universe_list(self) -> None:
        with pytest.raises(ValidationError):
            _basic_query(universe_ref=[])

    def test_rejects_empty_universe_string(self) -> None:
        with pytest.raises(ValidationError):
            _basic_query(universe_ref="")

    def test_as_of_must_be_tz_aware(self) -> None:
        with pytest.raises(ValidationError):
            _basic_query(as_of=datetime(2024, 6, 3, 20, 0))


class TestScanResult:
    def test_constructs_with_required_fields(self) -> None:
        r = ScanResult(
            symbol="AAPL",
            as_of=datetime(2024, 6, 3, 20, 0, tzinfo=UTC),
            move_pct=6.5,
            direction="up",
            window_actual=(
                datetime(2024, 6, 3, 13, 30, tzinfo=UTC),
                datetime(2024, 6, 3, 20, 0, tzinfo=UTC),
            ),
            bar_count=390,
            dollar_volume=Decimal("1500000"),
            metric_version="midrange-endpoint-v1",
            split_event=None,
        )
        assert r.symbol == "AAPL"
        assert r.split_event is None

    def test_round_trips_through_model_dump(self) -> None:
        r = ScanResult(
            symbol="MSFT",
            as_of=datetime(2024, 6, 3, 20, 0, tzinfo=UTC),
            move_pct=-3.2,
            direction="down",
            window_actual=(
                datetime(2024, 6, 3, 13, 30, tzinfo=UTC),
                datetime(2024, 6, 3, 20, 0, tzinfo=UTC),
            ),
            bar_count=390,
            dollar_volume=Decimal("2000000"),
            metric_version="midrange-endpoint-v1",
            split_event="2:1",
        )
        payload = r.model_dump(mode="json")
        rebuilt = ScanResult.model_validate(payload)
        assert rebuilt == r
