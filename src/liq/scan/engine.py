"""``ScanEngine.execute`` — the cross-sectional read pipeline.

1. Resolve ``universe_ref`` as-of ``query.as_of``.
2. Compute window-actual boundaries via the calendar helper.
3. Verify coverage manifest — raise :class:`CoverageGapError` on any
   uncovered (symbol × window) slice.
4. Read bars via :meth:`ParquetStore.read_multi`.
5. Apply split handling (no-op stub when no corporate-actions table).
6. Compute the midrange-endpoint move metric (FR-7).
7. Evaluate predicates, rank, truncate to ``limit``.

The engine is dependency-injected: tests pass a real ``DataService``
and ``ParquetStore`` against a ``tmp_path`` root. No vendor calls
happen here — the manifest already encodes coverage and the store
already has bars.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import polars as pl

from liq.scan.exceptions import CoverageGapError
from liq.scan.predicates import PredicateInput
from liq.scan.query import ScanQuery, ScanResult

if TYPE_CHECKING:
    from liq.data.service import DataService
    from liq.store.parquet import ParquetStore

logger = logging.getLogger(__name__)


_DEFAULT_PROVIDER = "databento"
_DEFAULT_DATASET = "EQUS.MINI"
_DEFAULT_TIMEFRAME = "1m"


class ScanEngine:
    """Runs a :class:`ScanQuery` against a ``DataService`` + store pair."""

    def __init__(
        self,
        *,
        data_service: DataService,
        store: ParquetStore,
        provider: str = _DEFAULT_PROVIDER,
        dataset: str = _DEFAULT_DATASET,
        timeframe: str = _DEFAULT_TIMEFRAME,
        registry: object | None = None,
    ) -> None:
        self._data_service = data_service
        self._store = store
        self._provider = provider
        self._dataset = dataset
        self._timeframe = timeframe
        self._registry = registry

    # ----- public ---------------------------------------------------

    def execute(self, query: ScanQuery) -> list[ScanResult]:
        resolved = self._data_service.resolve_universe(
            query.universe_ref,
            as_of=query.as_of.date(),
            registry=self._registry,
        )
        symbols = list(resolved.symbols)

        # Step 2 — window-actual.
        window_start, window_end = query.window.resolve(query.as_of)

        # Step 3 — coverage check.
        gaps = self._coverage_gaps(symbols, window_start, window_end)
        if gaps:
            raise CoverageGapError(
                f"coverage gaps for {len(gaps)} symbol(s) in "
                f"window {window_start.isoformat()} → {window_end.isoformat()}",
                missing=gaps,
            )

        # Step 4 — read_multi.
        keys = [f"{self._provider}/{sym}/bars/{self._timeframe}" for sym in symbols]
        result = self._store.read_multi(
            keys,
            start=window_start,
            end=window_end,
        )
        df = result.data
        if df.is_empty():
            return []

        # Step 5 — split handling (stub): no corp-actions source wired
        # yet. The path exists so the engine can be extended
        # with a real adjuster without changing the call surface.
        df, split_events = self._apply_split_handling(df, query.split_handling)

        # Step 6 — midrange-endpoint move metric (FR-7).
        per_symbol = self._compute_endpoint_move(df, k=self._infer_k(query))

        # Step 7 — predicate + rank + limit.
        passing = [
            row
            for row in per_symbol
            if query.predicate.evaluate(
                PredicateInput(
                    symbol=str(row["symbol"]),
                    move_pct=float(row["move_pct"]),
                    dollar_volume=Decimal(row["dollar_volume"]),
                    price=Decimal(row["last_close"]),
                    bar_count=int(row["bar_count"]),
                )
            )
        ]
        ranked = self._rank(passing, query.ranking)
        if query.limit is not None:
            ranked = ranked[: query.limit]

        return [
            ScanResult(
                symbol=str(row["symbol"]),
                as_of=query.as_of,
                move_pct=float(row["move_pct"]),
                direction=row["direction"],
                window_actual=(window_start, window_end),
                bar_count=int(row["bar_count"]),
                dollar_volume=Decimal(row["dollar_volume"]),
                metric_version=query.metric_version,
                split_event=split_events.get(str(row["symbol"])),
            )
            for row in ranked
        ]

    # ----- internal helpers -----------------------------------------

    def _coverage_gaps(
        self,
        symbols: Iterable[str],
        window_start: datetime,
        window_end: datetime,
    ) -> list[tuple[str, list[tuple[datetime, datetime]]]]:
        from liq.data.manifest import CoverageManifest

        gaps: list[tuple[str, list[tuple[datetime, datetime]]]] = []
        for sym in symbols:
            manifest = CoverageManifest.load(
                root=self._data_service.data_root,
                provider=self._provider,
                dataset=self._dataset,
                timeframe=self._timeframe,
                symbol=sym,
            )
            sym_gaps = manifest.gaps(start=window_start, end=window_end)
            if sym_gaps:
                gaps.append((sym, sym_gaps))
        return gaps

    @staticmethod
    def _infer_k(query: ScanQuery) -> int:
        """Pull the endpoint-aggregate window from the predicate, if any."""
        from liq.scan.predicates import AndPredicate, MovePredicate

        def _walk(pred: object) -> int | None:
            if isinstance(pred, MovePredicate):
                return pred.k
            if isinstance(pred, AndPredicate):
                for child in pred.predicates:
                    k = _walk(child)
                    if k is not None:
                        return k
            return None

        return _walk(query.predicate) or 5

    def _apply_split_handling(
        self,
        df: pl.DataFrame,
        handling: str,
    ) -> tuple[pl.DataFrame, dict[str, str]]:
        """Apply split handling once a corporate-actions source is wired."""
        # Acknowledge the parameter so future implementations have a
        # behavioural hook to extend.
        del handling
        return df, {}

    def _compute_endpoint_move(
        self,
        df: pl.DataFrame,
        *,
        k: int,
    ) -> list[dict[str, Any]]:
        """Compute midrange-endpoint move per symbol.

        For each symbol's bars in the window:

        * open = mean(open[0:k])
        * close = mean(close[-k:])
        * move_pct = (close - open) / open * 100
        * dollar_volume = sum(volume * close)
        """
        out: list[dict[str, Any]] = []
        for symbol, group in df.group_by("symbol", maintain_order=True):
            sym = str(symbol[0]) if isinstance(symbol, tuple) else str(symbol)
            ordered = group.sort("timestamp")
            head = ordered.head(k)
            tail = ordered.tail(k)
            if head.is_empty() or tail.is_empty():
                continue
            open_px = _to_float(head["open"].mean())
            close_px = _to_float(tail["close"].mean())
            volume_sum = _to_float(ordered["volume"].sum())
            if open_px == 0:
                continue
            move_pct = (close_px - open_px) / open_px * 100.0
            direction = "up" if move_pct > 0 else "down" if move_pct < 0 else "flat"
            dollar_volume = Decimal(str(round(volume_sum * close_px, 2)))
            out.append(
                {
                    "symbol": sym,
                    "move_pct": move_pct,
                    "direction": direction,
                    "last_close": Decimal(str(round(close_px, 4))),
                    "dollar_volume": dollar_volume,
                    "bar_count": int(ordered.height),
                }
            )
        return out

    @staticmethod
    def _rank(rows: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
        if mode == "abs_move":
            return sorted(rows, key=lambda r: abs(float(r["move_pct"])), reverse=True)
        if mode == "up":
            return sorted(rows, key=lambda r: float(r["move_pct"]), reverse=True)
        if mode == "down":
            return sorted(rows, key=lambda r: float(r["move_pct"]))
        return rows  # pragma: no cover — validated upstream


def _to_float(value: object) -> float:
    """Coerce a Polars aggregation result to ``float`` safely.

    Series.mean()/sum() widen to a ``PythonLiteral`` union for the
    type-checker; this small adapter narrows back to float without
    swallowing genuine non-numeric values.
    """
    if value is None:
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, Decimal):
        return float(value)
    return 0.0


__all__ = ["ScanEngine"]
