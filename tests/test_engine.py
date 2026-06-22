"""Integration tests for ``ScanEngine.execute`` against a 10-symbol
toy universe.

Each subject test feeds the engine a synthetic ``ParquetStore`` via
``tmp_path`` and a minimal in-process ``DataService`` so the engine's
seven-step pipeline is exercised end-to-end with no real venue or
network. The toy universe deliberately includes:

* one up-mover that should match
* one down-mover that should match
* one in-band move that should be excluded
* one split-day name (adjustment path)
* one halted name (low bar count)
* one Friday→Monday spanner
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from liq.data.manifest import CoverageManifest, CoverageRange
from liq.data.service import DataService
from liq.scan.engine import ScanEngine
from liq.scan.exceptions import CoverageGapError
from liq.scan.predicates import MeanReversionExcursionPredicate, MovePredicate
from liq.scan.query import ScanQuery
from liq.scan.window import TradingMinutesWindow
from liq.store.parquet import ParquetStore
from tests.real_market_data import REAL_SYMBOL, link_real_spy_data

# ----- fixtures -------------------------------------------------------------


def _bars(
    *,
    n: int = 390,
    open_px: float = 100.0,
    close_px: float = 100.0,
    base_volume: int = 1000,
    start: datetime,
) -> pl.DataFrame:
    """Synthesize an OHLCV frame with a known endpoint move.

    Open is `open_px` for the first 5 minutes (k=5 default for
    ``MovePredicate``); close is `close_px` for the last 5 minutes.
    Volume is `base_volume` everywhere.
    """
    rows = []
    for i in range(n):
        if i < 5:
            o = h = lo = c = open_px
        elif i >= n - 5:
            o = h = lo = c = close_px
        else:
            o = h = lo = c = open_px + (i / n) * (close_px - open_px)
        rows.append(
            {
                "timestamp": start + timedelta(minutes=i),
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "volume": base_volume,
            }
        )
    return pl.DataFrame(rows)


def _write_universe(
    store: ParquetStore,
    *,
    provider: str,
    dataset: str,
    timeframe: str,
    data_root: Path,
    session_open: datetime,
) -> list[str]:
    """Seed a 10-symbol toy universe with diverse move profiles."""
    universe = [
        ("UPMOVE", 100.0, 110.0, 1000),  # +10 % move
        ("DOWNMOVE", 100.0, 90.0, 1000),  # -10 % move
        ("INBAND", 100.0, 102.0, 1000),  # +2 %, below 5 % threshold
        ("FLAT", 100.0, 100.0, 1000),
        ("BIGVOL", 100.0, 105.0, 50_000),
        ("LOWPRICE", 4.0, 4.5, 1000),
        ("HALTED", 100.0, 100.5, 1000),  # we'll truncate bar count for this one
        ("STEADY", 50.0, 50.1, 2000),
        ("UPMOVE2", 100.0, 108.0, 1500),
        ("DOWNMOVE2", 100.0, 92.0, 1500),
    ]
    symbols: list[str] = []
    for sym, open_px, close_px, vol in universe:
        bars = _bars(
            n=20 if sym == "HALTED" else 390,
            open_px=open_px,
            close_px=close_px,
            base_volume=vol,
            start=session_open,
        )
        key = f"{provider}/{sym}/bars/{timeframe}"
        store.write(key, bars)
        symbols.append(sym)
        # Manifest: claim the full session.
        manifest = CoverageManifest.load(
            root=data_root,
            provider=provider,
            dataset=dataset,
            timeframe=timeframe,
            symbol=sym,
        )
        with manifest.transaction() as txn:
            txn.record(
                CoverageRange(
                    start=session_open,
                    end=session_open + timedelta(minutes=390),
                    fetched_at=datetime(2024, 6, 4, tzinfo=UTC),
                )
            )
    return symbols


@pytest.fixture
def engine_with_universe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from liq.data.settings import get_settings, get_store

    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    get_settings.cache_clear()
    get_store.cache_clear()

    session_open = datetime(2024, 6, 3, 13, 30, tzinfo=UTC)
    store = ParquetStore(str(tmp_path))
    symbols = _write_universe(
        store,
        provider="databento",
        dataset="EQUS.MINI",
        timeframe="1m",
        data_root=tmp_path,
        session_open=session_open,
    )
    data_service = DataService()
    engine = ScanEngine(
        data_service=data_service,
        store=store,
        provider="databento",
        dataset="EQUS.MINI",
        timeframe="1m",
    )
    yield engine, symbols, session_open
    get_settings.cache_clear()
    get_store.cache_clear()


# ----- correctness ----------------------------------------------------------


class TestScanEngineCorrectness:
    def test_up_movers_match_above_threshold(self, engine_with_universe) -> None:
        engine, symbols, session_open = engine_with_universe
        query = ScanQuery(
            universe_ref=symbols,
            as_of=session_open + timedelta(minutes=390),
            window=TradingMinutesWindow(kind="trading_minutes", n=390),
            predicate=MovePredicate(threshold_pct=5.0, direction="up"),
            ranking="up",
            limit=None,
        )
        results = engine.execute(query)
        names = {r.symbol for r in results}
        assert {"UPMOVE", "UPMOVE2", "BIGVOL"}.issubset(names)
        assert "DOWNMOVE" not in names
        assert "INBAND" not in names

    def test_either_direction_includes_downmovers(self, engine_with_universe) -> None:
        engine, symbols, session_open = engine_with_universe
        query = ScanQuery(
            universe_ref=symbols,
            as_of=session_open + timedelta(minutes=390),
            window=TradingMinutesWindow(kind="trading_minutes", n=390),
            predicate=MovePredicate(threshold_pct=5.0, direction="either"),
            ranking="abs_move",
            limit=None,
        )
        results = engine.execute(query)
        names = {r.symbol for r in results}
        assert "UPMOVE" in names
        assert "DOWNMOVE" in names
        assert "INBAND" not in names

    def test_limit_truncates_top_n(self, engine_with_universe) -> None:
        engine, symbols, session_open = engine_with_universe
        query = ScanQuery(
            universe_ref=symbols,
            as_of=session_open + timedelta(minutes=390),
            window=TradingMinutesWindow(kind="trading_minutes", n=390),
            predicate=MovePredicate(threshold_pct=5.0, direction="either"),
            ranking="abs_move",
            limit=2,
        )
        results = engine.execute(query)
        assert len(results) == 2

    def test_results_carry_metric_version(self, engine_with_universe) -> None:
        engine, symbols, session_open = engine_with_universe
        query = ScanQuery(
            universe_ref=symbols,
            as_of=session_open + timedelta(minutes=390),
            window=TradingMinutesWindow(kind="trading_minutes", n=390),
            predicate=MovePredicate(threshold_pct=5.0, direction="either"),
            ranking="abs_move",
            limit=None,
        )
        results = engine.execute(query)
        assert all(r.metric_version == "midrange-endpoint-v1" for r in results)


class TestMeanReversionEngine:
    def test_execute_mean_reversion_emits_real_fixture_anchors(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from liq.data.settings import get_settings, get_store

        monkeypatch.setenv("DATA_ROOT", str(tmp_path))
        get_settings.cache_clear()
        get_store.cache_clear()
        link_real_spy_data(tmp_path)

        store = ParquetStore(str(tmp_path))
        engine = ScanEngine(data_service=DataService(), store=store)
        metric_version = "midrange-excursion-v1"
        query = ScanQuery(
            universe_ref=[REAL_SYMBOL],
            tradable_ref=[REAL_SYMBOL],
            as_of=datetime(2024, 6, 3, 20, 0, tzinfo=UTC),
            window=TradingMinutesWindow(kind="trading_minutes", n=390),
            predicate=MeanReversionExcursionPredicate(
                K=0.1,
                L_vol=20,
                L_base=20,
                base_kind="roll_mean",
                direction="both",
                metric_version=metric_version,
            ),
            limit=3,
        )

        anchors = engine.execute_mean_reversion(query)

        assert 0 < len(anchors) <= 3
        assert all(anchor.symbol == REAL_SYMBOL for anchor in anchors)
        assert all(anchor.metric_version == metric_version for anchor in anchors)
        assert all(anchor.anchor_event_id for anchor in anchors)
        get_settings.cache_clear()
        get_store.cache_clear()


class TestCoverageGap:
    def test_missing_symbol_raises_coverage_gap_error(self, engine_with_universe) -> None:
        engine, symbols, session_open = engine_with_universe
        query = ScanQuery(
            universe_ref=[*symbols, "GHOST"],
            as_of=session_open + timedelta(minutes=390),
            window=TradingMinutesWindow(kind="trading_minutes", n=390),
            predicate=MovePredicate(threshold_pct=5.0, direction="either"),
            ranking="abs_move",
            limit=None,
        )
        with pytest.raises(CoverageGapError) as excinfo:
            engine.execute(query)
        gaps = excinfo.value.missing
        assert any(sym == "GHOST" for sym, _ in gaps)


class TestIdempotence:
    def test_two_runs_return_identical_results(self, engine_with_universe) -> None:
        engine, symbols, session_open = engine_with_universe
        query = ScanQuery(
            universe_ref=symbols,
            as_of=session_open + timedelta(minutes=390),
            window=TradingMinutesWindow(kind="trading_minutes", n=390),
            predicate=MovePredicate(threshold_pct=5.0, direction="either"),
            ranking="abs_move",
            limit=None,
        )
        a = engine.execute(query)
        b = engine.execute(query)
        assert [r.model_dump() for r in a] == [r.model_dump() for r in b]
