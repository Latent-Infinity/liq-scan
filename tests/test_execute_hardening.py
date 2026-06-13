"""Hardening tests for ``ScanEngine.execute`` edge cases."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from liq.data.manifest import CoverageManifest, CoverageRange
from liq.data.service import DataService
from liq.scan.engine import (
    ScanEngine,
    _action_ratio,
    _action_timestamp,
    _is_split_action,
)
from liq.scan.exceptions import CoverageGapError
from liq.scan.predicates import (
    AndPredicate,
    DollarVolumePredicate,
    MovePredicate,
    PricePredicate,
)
from liq.scan.query import ScanQuery
from liq.scan.window import CalendarWindow, TradingMinutesWindow
from liq.store.parquet import ParquetStore


def _bars(
    *,
    start: datetime,
    n: int,
    open_px: float,
    close_px: float,
    volume: int = 1000,
) -> pl.DataFrame:
    rows = []
    for i in range(n):
        px = open_px if i < n // 2 else close_px
        rows.append(
            {
                "timestamp": start + timedelta(minutes=i),
                "open": px,
                "high": px,
                "low": px,
                "close": px,
                "volume": volume,
            }
        )
    return pl.DataFrame(rows)


def _write_symbol(
    *,
    store: ParquetStore,
    data_root: Path,
    symbol: str,
    start: datetime,
    n: int,
    open_px: float,
    close_px: float,
    volume: int = 1000,
) -> None:
    store.write(
        f"databento/{symbol}/bars/1m",
        _bars(start=start, n=n, open_px=open_px, close_px=close_px, volume=volume),
    )
    manifest = CoverageManifest.load(
        root=data_root,
        provider="databento",
        dataset="EQUS.MINI",
        timeframe="1m",
        symbol=symbol,
    )
    with manifest.transaction() as txn:
        txn.record(
            CoverageRange(
                start=start,
                end=start + timedelta(minutes=n),
                fetched_at=datetime(2024, 6, 4, tzinfo=UTC),
            )
        )


@pytest.fixture
def scan_stack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ScanEngine, ParquetStore, Path]:
    from liq.data.settings import get_settings, get_store

    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    get_settings.cache_clear()
    get_store.cache_clear()

    store = ParquetStore(str(tmp_path))
    engine = ScanEngine(data_service=DataService(), store=store)
    return engine, store, tmp_path


def _query(
    symbols: list[str] | str,
    *,
    as_of: datetime,
    window: Any,
    predicate: Any,
    ranking: str = "abs_move",
    include_extended_hours: bool = False,
    split_handling: str = "adjust",
) -> ScanQuery:
    return ScanQuery(
        universe_ref=symbols,
        as_of=as_of,
        window=window,
        predicate=predicate,
        ranking=ranking,  # type: ignore[arg-type]
        include_extended_hours=include_extended_hours,
        split_handling=split_handling,  # type: ignore[arg-type]
    )


class TestCalendarEdges:
    def test_include_extended_hours_uses_premarket_window(self, scan_stack) -> None:
        engine, store, data_root = scan_stack
        start = datetime(2024, 6, 3, 8, 0, tzinfo=UTC)
        end = datetime(2024, 6, 3, 13, 30, tzinfo=UTC)
        _write_symbol(
            store=store,
            data_root=data_root,
            symbol="PREUP",
            start=start,
            n=330,
            open_px=100.0,
            close_px=108.0,
        )

        results = engine.execute(
            _query(
                ["PREUP"],
                as_of=end,
                window=TradingMinutesWindow(kind="trading_minutes", n=330),
                predicate=MovePredicate(threshold_pct=5.0, direction="up"),
                ranking="up",
                include_extended_hours=True,
            )
        )

        assert [r.symbol for r in results] == ["PREUP"]
        assert results[0].window_actual == (start, end)


class TestPredicateComposition:
    def test_three_condition_and_chain_filters_results(self, scan_stack) -> None:
        engine, store, data_root = scan_stack
        start = datetime(2024, 6, 3, 13, 30, tzinfo=UTC)
        for symbol, open_px, close_px, volume in [
            ("PASS", 100.0, 108.0, 50_000),
            ("CHEAP", 4.0, 4.5, 50_000),
            ("THIN", 100.0, 108.0, 100),
        ]:
            _write_symbol(
                store=store,
                data_root=data_root,
                symbol=symbol,
                start=start,
                n=30,
                open_px=open_px,
                close_px=close_px,
                volume=volume,
            )

        predicate = AndPredicate(
            predicates=[
                MovePredicate(threshold_pct=5.0, direction="up"),
                DollarVolumePredicate(min_usd=Decimal("1000000")),
                PricePredicate(min_usd=Decimal("5")),
            ]
        )
        results = engine.execute(
            _query(
                ["PASS", "CHEAP", "THIN"],
                as_of=start + timedelta(minutes=30),
                window=CalendarWindow(kind="calendar", duration=timedelta(minutes=30)),
                predicate=predicate,
                ranking="up",
            )
        )
        assert [r.symbol for r in results] == ["PASS"]

    def test_down_direction_returns_only_down_movers(self, scan_stack) -> None:
        engine, store, data_root = scan_stack
        start = datetime(2024, 6, 3, 13, 30, tzinfo=UTC)
        _write_symbol(
            store=store,
            data_root=data_root,
            symbol="DOWN",
            start=start,
            n=30,
            open_px=100.0,
            close_px=92.0,
        )
        _write_symbol(
            store=store,
            data_root=data_root,
            symbol="UP",
            start=start,
            n=30,
            open_px=100.0,
            close_px=108.0,
        )

        results = engine.execute(
            _query(
                ["DOWN", "UP"],
                as_of=start + timedelta(minutes=30),
                window=CalendarWindow(kind="calendar", duration=timedelta(minutes=30)),
                predicate=MovePredicate(threshold_pct=5.0, direction="down"),
                ranking="down",
            )
        )
        assert [r.symbol for r in results] == ["DOWN"]


class TestSplitHandling:
    def test_adjusted_split_window_uses_pre_split_factor(self, scan_stack) -> None:
        engine, store, data_root = scan_stack
        start = datetime(2024, 6, 3, 13, 30, tzinfo=UTC)
        split_ts = start + timedelta(minutes=10)
        _write_symbol(
            store=store,
            data_root=data_root,
            symbol="SPLT",
            start=start,
            n=20,
            open_px=100.0,
            close_px=55.0,
        )
        store.write(
            "databento/SPLT/corp_actions",
            pl.DataFrame(
                {
                    "timestamp": [split_ts],
                    "type": ["split"],
                    "ratio": [0.5],
                }
            ),
        )

        results = engine.execute(
            _query(
                ["SPLT"],
                as_of=start + timedelta(minutes=20),
                window=CalendarWindow(kind="calendar", duration=timedelta(minutes=20)),
                predicate=MovePredicate(threshold_pct=5.0, direction="up"),
                ranking="up",
            )
        )
        assert [r.symbol for r in results] == ["SPLT"]
        assert results[0].split_event == f"split:0.5@{split_ts.isoformat()}"

    def test_excluded_split_window_removes_symbol(self, scan_stack) -> None:
        engine, store, data_root = scan_stack
        start = datetime(2024, 6, 3, 13, 30, tzinfo=UTC)
        split_ts = start + timedelta(minutes=10)
        _write_symbol(
            store=store,
            data_root=data_root,
            symbol="SPLT",
            start=start,
            n=20,
            open_px=100.0,
            close_px=55.0,
        )
        store.write(
            "databento/SPLT/corp_actions",
            pl.DataFrame({"timestamp": [split_ts], "type": ["split"], "ratio": [0.5]}),
        )

        results = engine.execute(
            _query(
                ["SPLT"],
                as_of=start + timedelta(minutes=20),
                window=CalendarWindow(kind="calendar", duration=timedelta(minutes=20)),
                predicate=MovePredicate(threshold_pct=5.0, direction="either"),
                split_handling="exclude",
            )
        )
        assert results == []


@dataclass
class _Resolved:
    symbols: list[str]
    pit: bool = True


class _EmptyService:
    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root

    def resolve_universe(self, *_args: object, **_kwargs: object) -> _Resolved:
        return _Resolved(symbols=[])


class TestFailureModesAndLogging:
    def test_manifest_covered_but_store_missing_raises_coverage_gap(self, scan_stack) -> None:
        engine, _store, data_root = scan_stack
        start = datetime(2024, 6, 3, 13, 30, tzinfo=UTC)
        manifest = CoverageManifest.load(
            root=data_root,
            provider="databento",
            dataset="EQUS.MINI",
            timeframe="1m",
            symbol="GHOST",
        )
        with manifest.transaction() as txn:
            txn.record(
                CoverageRange(
                    start=start,
                    end=start + timedelta(minutes=30),
                    fetched_at=datetime(2024, 6, 4, tzinfo=UTC),
                )
            )

        with pytest.raises(CoverageGapError) as excinfo:
            engine.execute(
                _query(
                    ["GHOST"],
                    as_of=start + timedelta(minutes=30),
                    window=CalendarWindow(kind="calendar", duration=timedelta(minutes=30)),
                    predicate=MovePredicate(threshold_pct=5.0, direction="either"),
                )
            )
        assert excinfo.value.missing == [("GHOST", [(start, start + timedelta(minutes=30))])]

    def test_manifest_covered_file_with_no_window_rows_returns_empty(self, scan_stack) -> None:
        engine, store, data_root = scan_stack
        stored_start = datetime(2024, 6, 3, 13, 30, tzinfo=UTC)
        query_start = datetime(2024, 6, 3, 14, 30, tzinfo=UTC)
        _write_symbol(
            store=store,
            data_root=data_root,
            symbol="OUTSIDE",
            start=stored_start,
            n=10,
            open_px=100.0,
            close_px=108.0,
        )
        manifest = CoverageManifest.load(
            root=data_root,
            provider="databento",
            dataset="EQUS.MINI",
            timeframe="1m",
            symbol="OUTSIDE",
        )
        with manifest.transaction() as txn:
            txn.record(
                CoverageRange(
                    start=query_start,
                    end=query_start + timedelta(minutes=30),
                    fetched_at=datetime(2024, 6, 4, tzinfo=UTC),
                )
            )

        results = engine.execute(
            _query(
                ["OUTSIDE"],
                as_of=query_start + timedelta(minutes=30),
                window=CalendarWindow(kind="calendar", duration=timedelta(minutes=30)),
                predicate=MovePredicate(threshold_pct=5.0, direction="either"),
            )
        )
        assert results == []

    def test_empty_resolved_universe_returns_empty_and_warns(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        engine = ScanEngine(data_service=_EmptyService(tmp_path), store=ParquetStore(str(tmp_path)))  # type: ignore[arg-type]
        caplog.set_level(logging.INFO, logger="liq.scan.engine")

        results = engine.execute(
            _query(
                "empty",
                as_of=datetime(2024, 6, 3, 20, 0, tzinfo=UTC),
                window=TradingMinutesWindow(kind="trading_minutes", n=30),
                predicate=MovePredicate(threshold_pct=5.0, direction="either"),
            )
        )

        assert results == []
        warnings = [r for r in caplog.records if getattr(r, "event", None) == "empty_universe"]
        assert len(warnings) == 1
        assert warnings[0].levelno == logging.WARNING

    def test_scan_logs_reconstructable_event_chain(
        self,
        scan_stack,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        engine, store, data_root = scan_stack
        start = datetime(2024, 6, 3, 13, 30, tzinfo=UTC)
        _write_symbol(
            store=store,
            data_root=data_root,
            symbol="LOGS",
            start=start,
            n=30,
            open_px=100.0,
            close_px=108.0,
        )

        caplog.set_level(logging.INFO, logger="liq.scan.engine")
        engine.execute(
            _query(
                ["LOGS"],
                as_of=start + timedelta(minutes=30),
                window=CalendarWindow(kind="calendar", duration=timedelta(minutes=30)),
                predicate=MovePredicate(threshold_pct=5.0, direction="up"),
                ranking="up",
            )
        )

        records = [r for r in caplog.records if hasattr(r, "scan_run_id")]
        scan_run_ids = {r.scan_run_id for r in records}
        assert len(scan_run_ids) == 1
        events = [r.event for r in records]
        expected = [
            "scan_started",
            "universe_resolved",
            "coverage_verified",
            "read_multi",
            "predicate_evaluated",
            "scan_completed",
        ]
        positions = [events.index(event) for event in expected]
        assert positions == sorted(positions)


class TestCorporateActionParsing:
    def test_action_kind_defaults_to_split_when_missing(self) -> None:
        assert _is_split_action({})
        assert _is_split_action({"event": "stock_split"})
        assert not _is_split_action({"type": "dividend"})

    def test_action_timestamp_accepts_date_and_string_inputs(self) -> None:
        assert _action_timestamp({"date": "2024-06-03"}) == datetime(2024, 6, 3, tzinfo=UTC)
        assert _action_timestamp({"ex_date": datetime(2024, 6, 3, 13, 30)}) == datetime(
            2024, 6, 3, 13, 30, tzinfo=UTC
        )
        assert _action_timestamp({}) is None

    def test_action_ratio_accepts_common_shapes(self) -> None:
        assert _action_ratio({"ratio": "2:1"}) == 0.5
        assert _action_ratio({"split_ratio": Decimal("0.25")}) == 0.25
        assert _action_ratio({"numerator": 3, "denominator": 2}) == pytest.approx(2 / 3)
        assert _action_ratio({}) is None
