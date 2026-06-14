"""Tradable-filter contract for ``ScanEngine.execute``.

A `ScanQuery.tradable_ref` (optional) composes with the research
universe at the very end of `execute`: rank + truncate happen on the
full research set, then results outside the tradable set are dropped
and an audit event ``tradable_filtered`` fires.

This honors the separation of concerns established in
``docs/universes.md`` / ``docs/scan-query.md``:

* ``universe_ref`` is the **research** scope (what we study).
* ``tradable_ref`` is the **execution** policy (what we'd act on).

Dropping happens *after* ranking so the operator can still observe
"AAPL ranked #1 but we don't trade it" via the log even though it
doesn't appear in the returned list.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
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


def _bars(open_px: float, close_px: float, *, n: int = 30) -> pl.DataFrame:
    base = datetime(2024, 6, 3, 13, 30, tzinfo=UTC)
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
                "timestamp": base + timedelta(minutes=i),
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "volume": 1000,
            }
        )
    return pl.DataFrame(rows)


@pytest.fixture
def engine_with_universe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from liq.data.settings import get_settings, get_store

    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    get_settings.cache_clear()
    get_store.cache_clear()

    session_open = datetime(2024, 6, 3, 13, 30, tzinfo=UTC)
    store = ParquetStore(str(tmp_path))
    # Three big movers — predicate will pass all of them.
    for sym, open_px, close_px in [
        ("NVDA", 100.0, 110.0),
        ("PLTR", 100.0, 115.0),  # biggest mover; intentionally NOT in tradable
        ("AMD", 100.0, 108.0),
    ]:
        store.write(f"databento/{sym}/bars/1m", _bars(open_px, close_px))
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

    data_service = DataService()
    engine = ScanEngine(data_service=data_service, store=store)
    yield engine, session_open
    get_settings.cache_clear()
    get_store.cache_clear()


def _query(*, universe_ref, tradable_ref=None, limit=None):
    return ScanQuery(
        universe_ref=universe_ref,
        tradable_ref=tradable_ref,
        as_of=datetime(2024, 6, 3, 14, 0, tzinfo=UTC),
        window=TradingMinutesWindow(kind="trading_minutes", n=30),
        predicate=MovePredicate(threshold_pct=5.0, direction="either"),
        ranking="abs_move",
        limit=limit,
    )


# ----- backwards compat ----------------------------------------------------


class TestNoTradableRefIsBackwardsCompatible:
    def test_omitted_tradable_ref_returns_all_results(self, engine_with_universe) -> None:
        engine, _ = engine_with_universe
        results = engine.execute(_query(universe_ref=["NVDA", "PLTR", "AMD"]))
        assert {r.symbol for r in results} == {"NVDA", "PLTR", "AMD"}

    def test_default_is_none(self) -> None:
        # Query must not require tradable_ref.
        q = _query(universe_ref=["NVDA"])
        assert q.tradable_ref is None


# ----- filtering -----------------------------------------------------------


class TestTradableFilter:
    def test_explicit_list_drops_non_tradable_symbols(self, engine_with_universe) -> None:
        engine, _ = engine_with_universe
        results = engine.execute(
            _query(
                universe_ref=["NVDA", "PLTR", "AMD"],
                tradable_ref=["NVDA", "AMD"],
            )
        )
        symbols = {r.symbol for r in results}
        assert symbols == {"NVDA", "AMD"}
        assert "PLTR" not in symbols

    def test_filter_applies_after_ranking_not_before(self, engine_with_universe) -> None:
        """Dropping must happen *after* the predicate + rank + limit.
        Otherwise a top-K query with a tradable filter would silently
        pull in #K+1 to fill the slot left by the dropped non-tradable
        name — that's a different policy than ``ranked top K, exclude
        non-tradable`` which is what we promise."""
        engine, _ = engine_with_universe
        # limit=2 on a 3-result universe ranked by abs_move:
        #   PLTR (+15%) #1, NVDA (+10%) #2, AMD (+8%) #3
        # With limit=2 + ranking="abs_move", expected top-2 is
        # {PLTR, NVDA}. Dropping PLTR for tradable leaves {NVDA}.
        # If filtering ran before ranking, AMD would have backfilled.
        results = engine.execute(
            _query(
                universe_ref=["NVDA", "PLTR", "AMD"],
                tradable_ref=["NVDA", "AMD"],
                limit=2,
            )
        )
        symbols = {r.symbol for r in results}
        assert symbols == {"NVDA"}, (
            f"expected only NVDA after dropping PLTR from the top-2; got {symbols}"
        )

    def test_empty_tradable_intersection_returns_empty_list(self, engine_with_universe) -> None:
        engine, _ = engine_with_universe
        results = engine.execute(
            _query(
                universe_ref=["NVDA", "PLTR"],
                tradable_ref=["NOTHING-SHARED"],
            )
        )
        assert results == []


# ----- structured event ----------------------------------------------------


class TestTradableFilteredEvent:
    def test_event_fires_with_dropped_and_kept_counts(
        self, engine_with_universe, caplog: pytest.LogCaptureFixture
    ) -> None:
        engine, _ = engine_with_universe
        caplog.set_level(logging.INFO, logger="liq.scan.engine")
        engine.execute(
            _query(
                universe_ref=["NVDA", "PLTR", "AMD"],
                tradable_ref=["NVDA", "AMD"],
            )
        )
        events = [r for r in caplog.records if getattr(r, "event", None) == "tradable_filtered"]
        assert len(events) == 1
        assert events[0].kept == 2
        assert events[0].dropped == 1

    def test_event_does_not_fire_when_tradable_ref_omitted(
        self, engine_with_universe, caplog: pytest.LogCaptureFixture
    ) -> None:
        engine, _ = engine_with_universe
        caplog.set_level(logging.INFO, logger="liq.scan.engine")
        engine.execute(_query(universe_ref=["NVDA", "AMD"]))
        events = [r for r in caplog.records if getattr(r, "event", None) == "tradable_filtered"]
        assert events == []


# ----- validation -----------------------------------------------------------


class TestTradableRefValidation:
    def test_empty_string_rejected(self) -> None:
        with pytest.raises(ValueError, match="tradable_ref"):
            _query(universe_ref=["NVDA"], tradable_ref="")

    def test_empty_list_rejected(self) -> None:
        with pytest.raises(ValueError, match="tradable_ref"):
            _query(universe_ref=["NVDA"], tradable_ref=[])

    def test_decimal_field_preserves_precision(self, engine_with_universe) -> None:
        # Sanity that filtering doesn't corrupt the result row contents.
        engine, _ = engine_with_universe
        results = engine.execute(
            _query(
                universe_ref=["NVDA", "AMD"],
                tradable_ref=["NVDA"],
            )
        )
        assert all(isinstance(r.dollar_volume, Decimal) for r in results)
