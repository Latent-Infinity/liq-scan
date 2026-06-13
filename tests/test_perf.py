"""Perf gate: a 10-symbol toy universe scan completes under 100 ms.

Default pytest run includes this — the assertion budget is generous
and the seed is cheap. If the budget tightens later, gate behind
``@pytest.mark.perf``.
"""

from __future__ import annotations

import statistics
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from liq.data.manifest import CoverageManifest, CoverageRange
from liq.data.service import DataService
from liq.scan.engine import ScanEngine
from liq.scan.predicates import MovePredicate
from liq.scan.query import ScanQuery
from liq.scan.window import TradingMinutesWindow
from liq.store.parquet import ParquetStore


def _bars(open_px: float, close_px: float, *, start: datetime, n: int = 30) -> pl.DataFrame:
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
                "volume": 1000,
            }
        )
    return pl.DataFrame(rows)


@pytest.fixture
def engine_and_query(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from liq.data.settings import get_settings, get_store

    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    get_settings.cache_clear()
    get_store.cache_clear()

    session_open = datetime(2024, 6, 3, 13, 30, tzinfo=UTC)
    store = ParquetStore(str(tmp_path))
    symbols = [f"S{i:02d}" for i in range(10)]
    for i, sym in enumerate(symbols):
        bars = _bars(100.0, 100.0 + i, start=session_open)
        store.write(f"databento/{sym}/bars/1m", bars)
        manifest = CoverageManifest.load(
            root=tmp_path,
            provider="databento",
            dataset="EQUS.MINI",
            timeframe="1m",
            symbol=sym,
        )
        with manifest.transaction() as txn:
            txn.record(
                CoverageRange(
                    start=session_open,
                    end=session_open + timedelta(minutes=30),
                    fetched_at=datetime(2024, 6, 4, tzinfo=UTC),
                )
            )

    engine = ScanEngine(data_service=DataService(), store=store)
    query = ScanQuery(
        universe_ref=symbols,
        as_of=session_open + timedelta(minutes=30),
        window=TradingMinutesWindow(kind="trading_minutes", n=30),
        predicate=MovePredicate(threshold_pct=1.0, direction="either"),
        ranking="abs_move",
        limit=None,
    )
    yield engine, query
    get_settings.cache_clear()
    get_store.cache_clear()


def test_ten_symbol_scan_under_100ms(engine_and_query) -> None:
    engine, query = engine_and_query

    # Warm up to pay one-time costs.
    for _ in range(2):
        engine.execute(query)

    timings = []
    last_results = engine.execute(query)
    for _ in range(10):
        t0 = time.perf_counter()
        last_results = engine.execute(query)
        timings.append(time.perf_counter() - t0)

    p95 = sorted(timings)[8]
    assert len(last_results) >= 1
    assert p95 < 0.1, (
        f"p95 latency {p95 * 1000:.1f} ms exceeds 100 ms; median "
        f"{statistics.median(timings) * 1000:.1f} ms"
    )
