"""``ScanEngine.execute`` — the cross-sectional read pipeline.

1. Resolve ``universe_ref`` as-of ``query.as_of``.
2. Compute window-actual boundaries via the calendar helper.
3. Verify coverage manifest — raise :class:`CoverageGapError` on any
   uncovered (symbol × window) slice.
4. Read bars via :meth:`ParquetStore.read_multi`.
5. Apply split handling (no-op stub when no corporate-actions table).
6. Compute the midrange-endpoint move metric.
7. Evaluate predicates, rank, truncate to ``limit``.

The engine is dependency-injected: tests pass a real ``DataService``
and ``ParquetStore`` against a ``tmp_path`` root. No vendor calls
happen here — the manifest already encodes coverage and the store
already has bars.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid5

import polars as pl

from liq.scan.exceptions import CoverageGapError, NonPITUniverseError
from liq.scan.persistence import (
    SweepArtifacts,
    append_runs,
    compute_sweep_id,
    list_persisted_as_ofs,
    load_runs,
    mark_as_of_complete,
    results_to_frame,
    sweep_data_version_hash,
    upsert_universe_snapshot,
    write_meta,
)
from liq.scan.predicates import PredicateInput
from liq.scan.query import ScanQuery, ScanQueryTemplate, ScanResult
from liq.scan.sweep import SweepConfig, as_of_timestamps, filter_remaining
from liq.scan.window import TradingMinutesWindow

if TYPE_CHECKING:
    from liq.data.service import DataService
    from liq.store.parquet import ParquetStore

logger = logging.getLogger(__name__)


_DEFAULT_PROVIDER = "databento"
_DEFAULT_DATASET = "EQUS.MINI"
_DEFAULT_TIMEFRAME = "1m"


@dataclass(frozen=True)
class _SplitEvent:
    symbol: str
    timestamp: datetime
    ratio: float

    @property
    def label(self) -> str:
        ratio = f"{self.ratio:g}"
        return f"split:{ratio}@{self.timestamp.isoformat()}"


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

    def execute(
        self,
        query: ScanQuery,
        *,
        correlation_id: str | None = None,
        sweep_id: str | None = None,
    ) -> list[ScanResult]:
        scan_run_id = _scan_run_id(query)
        self._log_event(
            "scan_started",
            scan_run_id=scan_run_id,
            correlation_id=correlation_id,
            sweep_id=sweep_id,
            as_of=query.as_of.isoformat(),
            include_extended_hours=query.include_extended_hours,
        )
        resolved = self._data_service.resolve_universe(
            query.universe_ref,
            as_of=query.as_of.date(),
            registry=self._registry,
        )
        symbols = list(resolved.symbols)
        self._log_event(
            "universe_resolved",
            scan_run_id=scan_run_id,
            correlation_id=correlation_id,
            sweep_id=sweep_id,
            symbols_count=len(symbols),
            pit=bool(getattr(resolved, "pit", False)),
        )

        if not symbols:
            self._log_event(
                "empty_universe",
                scan_run_id=scan_run_id,
                correlation_id=correlation_id,
                sweep_id=sweep_id,
                level=logging.WARNING,
            )
            self._log_event(
                "scan_completed",
                scan_run_id=scan_run_id,
                correlation_id=correlation_id,
                sweep_id=sweep_id,
                result_count=0,
            )
            return []

        # Step 2 — window-actual.
        window_start, window_end = self._resolve_window(query)

        # Step 3 — coverage check.
        gaps = self._coverage_gaps(symbols, window_start, window_end)
        self._log_event(
            "coverage_verified",
            scan_run_id=scan_run_id,
            correlation_id=correlation_id,
            sweep_id=sweep_id,
            missing_count=len(gaps),
            window_start=window_start.isoformat(),
            window_end=window_end.isoformat(),
        )
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
        self._log_event(
            "read_multi",
            scan_run_id=scan_run_id,
            correlation_id=correlation_id,
            sweep_id=sweep_id,
            keys_count=len(keys),
            missing_count=len(result.missing_keys),
            window_start=window_start.isoformat(),
            window_end=window_end.isoformat(),
        )
        if result.missing_keys:
            missing = [
                (key.split("/")[1], [(window_start, window_end)]) for key in result.missing_keys
            ]
            raise CoverageGapError(
                f"store returned no files for {len(result.missing_keys)} covered key(s)",
                missing=missing,
            )
        df = result.data
        if df.is_empty():
            self._log_event(
                "predicate_evaluated",
                scan_run_id=scan_run_id,
                correlation_id=correlation_id,
                sweep_id=sweep_id,
                evaluated_count=0,
                passed_count=0,
            )
            self._log_event(
                "scan_completed",
                scan_run_id=scan_run_id,
                correlation_id=correlation_id,
                sweep_id=sweep_id,
                result_count=0,
            )
            return []

        # Step 5 — split handling from stored corporate-action rows.
        df, split_events = self._apply_split_handling(
            df,
            query.split_handling,
            window_start=window_start,
            window_end=window_end,
        )

        # Step 6 — midrange-endpoint move metric.
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
        self._log_event(
            "predicate_evaluated",
            scan_run_id=scan_run_id,
            correlation_id=correlation_id,
            sweep_id=sweep_id,
            evaluated_count=len(per_symbol),
            passed_count=len(passing),
        )
        ranked = self._rank(passing, query.ranking)
        if query.limit is not None:
            ranked = ranked[: query.limit]

        results = [
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

        # Tradable-set filter (optional). Runs *after* rank + limit so
        # the top-K shape is preserved minus the non-tradable rows —
        # consistent with the "rank on research, gate on execution"
        # separation in docs/scan-query.md.
        if query.tradable_ref is not None:
            tradable = self._data_service.resolve_universe(
                query.tradable_ref,
                as_of=query.as_of.date(),
                registry=self._registry,
            )
            tradable_set = {str(s).upper() for s in tradable.symbols}
            before = len(results)
            results = [r for r in results if r.symbol.upper() in tradable_set]
            self._log_event(
                "tradable_filtered",
                scan_run_id=scan_run_id,
                correlation_id=correlation_id,
                sweep_id=sweep_id,
                kept=len(results),
                dropped=before - len(results),
                tradable_universe=(
                    query.tradable_ref if isinstance(query.tradable_ref, str) else "<inline-list>"
                ),
            )

        self._log_event(
            "scan_completed",
            scan_run_id=scan_run_id,
            correlation_id=correlation_id,
            sweep_id=sweep_id,
            result_count=len(results),
        )
        return results

    def sweep(
        self,
        template: ScanQueryTemplate,
        config: SweepConfig,
    ) -> SweepArtifacts:
        """Historical sweep of ``template`` across ``config``'s as-ofs.

        For each as-of, re-resolves the universe (so composite-kind
        membership changes through time are honored), refuses non-PIT
        universes immediately, and runs :meth:`execute`.
        Results stream into the persisted sweep folder under the
        store; a resumed sweep skips already-persisted as-ofs.
        """
        sweep_id = compute_sweep_id(config)
        sweep_run_id = f"sweep:{sweep_id}"
        self._log_event(
            "sweep_started",
            scan_run_id=sweep_run_id,
            query_name=config.query_name,
            sweep_id=sweep_id,
            cadence=config.cadence,
            start=config.start.isoformat(),
            end=config.end.isoformat(),
        )

        as_ofs = as_of_timestamps(config)
        already_done = list_persisted_as_ofs(self._store, config)
        remaining = filter_remaining(as_ofs, already_done)
        resumed_from = max(already_done) if already_done else None
        if resumed_from is not None:
            self._log_event(
                "sweep_resumed",
                scan_run_id=sweep_run_id,
                sweep_id=sweep_id,
                resumed_from=resumed_from.isoformat(),
                remaining=len(remaining),
            )

        template_json = template.model_dump_json()
        query_hash = _template_hash(template)
        write_meta(
            self._store,
            config,
            query_hash=query_hash,
            template_json=template_json,
        )

        runs_count = 0
        for as_of in remaining:
            query = template.with_as_of(as_of)
            resolved = self._data_service.resolve_universe(
                query.universe_ref,
                as_of=as_of.date(),
                registry=self._registry,
            )
            if not bool(getattr(resolved, "pit", False)):
                raise NonPITUniverseError(
                    f"sweep refuses non-PIT universe at as_of={as_of.isoformat()}"
                )

            symbols = list(resolved.symbols)
            uni_hash = upsert_universe_snapshot(self._store, config, as_of=as_of, symbols=symbols)
            results = self.execute(query, correlation_id=sweep_run_id, sweep_id=sweep_id)
            frame = results_to_frame(results, universe_hash_value=uni_hash)
            append_runs(self._store, config, frame)
            mark_as_of_complete(self._store, config, as_of)
            self._log_event(
                "sweep_as_of",
                scan_run_id=sweep_run_id,
                sweep_id=sweep_id,
                as_of=as_of.isoformat(),
                universe_hash=uni_hash,
            )
            runs_count += frame.height

        # Re-count total persisted rows so resume counts correctly.
        persisted_runs = load_runs(self._store, config)
        total_runs = persisted_runs.height
        total_sessions_with_hits = (
            len({row["as_of"] for row in persisted_runs.select("as_of").iter_rows(named=True)})
            if not persisted_runs.is_empty()
            else 0
        )
        write_meta(
            self._store,
            config,
            query_hash=query_hash,
            template_json=template_json,
            data_version_hash=sweep_data_version_hash(self._store, config),
        )
        self._log_event(
            "sweep_completed",
            scan_run_id=sweep_run_id,
            sweep_id=sweep_id,
            sessions_scanned=len(as_ofs),
            sessions_with_hits=total_sessions_with_hits,
            runs_count=total_runs,
        )
        return SweepArtifacts(
            query_name=config.query_name,
            sweep_id=sweep_id,
            runs_count=total_runs,
            sessions_scanned=len(as_ofs),
            sessions_with_hits=total_sessions_with_hits,
            resumed_from=resumed_from,
        )

    @property
    def store(self) -> ParquetStore:
        """Expose the injected store for sweep persistence helpers + tests."""
        return self._store

    # ----- internal helpers -----------------------------------------

    @staticmethod
    def _log_event(
        event: str,
        *,
        scan_run_id: str,
        correlation_id: str | None = None,
        level: int = logging.INFO,
        **fields: object,
    ) -> None:
        extra = {
            "event": event,
            "scan_run_id": scan_run_id,
            "correlation_id": correlation_id or scan_run_id,
            "timestamp": datetime.now(tz=UTC).isoformat(),
            **{k: v for k, v in fields.items() if v is not None},
        }
        logger.log(
            level,
            event,
            extra=extra,
        )

    @staticmethod
    def _resolve_window(query: ScanQuery) -> tuple[datetime, datetime]:
        if query.include_extended_hours and isinstance(query.window, TradingMinutesWindow):
            from liq.data.calendar import extended_trading_minutes_window

            return extended_trading_minutes_window(query.as_of, query.window.n)
        return query.window.resolve(query.as_of)

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
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> tuple[pl.DataFrame, dict[str, str]]:
        """Adjust or exclude windows spanning split events."""
        events = self._split_events(df, window_start=window_start, window_end=window_end)
        if not events:
            return df, {}

        split_events = {event.symbol: event.label for event in events}
        if handling == "exclude":
            return df.filter(~pl.col("symbol").is_in(list(split_events))), split_events

        adjusted = df
        price_cols = [c for c in ("open", "high", "low", "close") if c in adjusted.columns]
        for event in events:
            for col in price_cols:
                adjusted = adjusted.with_columns(
                    pl.when(
                        (pl.col("symbol") == event.symbol) & (pl.col("timestamp") < event.timestamp)
                    )
                    .then(pl.col(col) * event.ratio)
                    .otherwise(pl.col(col))
                    .alias(col)
                )
        return adjusted, split_events

    def _split_events(
        self,
        df: pl.DataFrame,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> list[_SplitEvent]:
        events: list[_SplitEvent] = []
        symbols = (
            [str(s) for s in df["symbol"].unique().to_list()] if "symbol" in df.columns else []
        )
        for symbol in symbols:
            event = self._first_split_event(
                symbol, window_start=window_start, window_end=window_end
            )
            if event is not None:
                events.append(event)
        return events

    def _first_split_event(
        self,
        symbol: str,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> _SplitEvent | None:
        key = f"{self._provider}/{symbol}/corp_actions"
        if not self._store.exists(key):
            return None
        actions = self._store.read(key)
        if actions.is_empty():
            return None
        for row in actions.iter_rows(named=True):
            if not _is_split_action(row):
                continue
            timestamp = _action_timestamp(row)
            ratio = _action_ratio(row)
            if timestamp is None or ratio is None or ratio <= 0:
                continue
            if window_start <= timestamp < window_end:
                return _SplitEvent(symbol=symbol, timestamp=timestamp, ratio=ratio)
        return None

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


def _scan_run_id(query: ScanQuery) -> str:
    return str(uuid5(NAMESPACE_URL, query.model_dump_json()))


def _template_hash(template: ScanQueryTemplate) -> str:
    """Stable SHA-256 of the template's JSON form."""
    return hashlib.sha256(template.model_dump_json().encode("utf-8")).hexdigest()[:16]


def _is_split_action(row: dict[str, Any]) -> bool:
    kind = row.get("type") or row.get("event") or row.get("action")
    if kind is None:
        return True
    return "split" in str(kind).lower()


def _action_timestamp(row: dict[str, Any]) -> datetime | None:
    raw = row.get("timestamp") or row.get("ex_date") or row.get("date")
    if isinstance(raw, datetime):
        return raw.astimezone(UTC) if raw.tzinfo is not None else raw.replace(tzinfo=UTC)
    if isinstance(raw, date):
        return datetime.combine(raw, datetime.min.time(), tzinfo=UTC)
    if isinstance(raw, str):
        parsed = datetime.fromisoformat(raw)
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None


def _action_ratio(row: dict[str, Any]) -> float | None:
    for key in ("ratio", "split_ratio", "adjustment_factor"):
        value = row.get(key)
        if isinstance(value, int | float | Decimal):
            return float(value)
        if isinstance(value, str):
            if ":" in value:
                left, _, right = value.partition(":")
                numerator = float(left)
                denominator = float(right)
                return denominator / numerator
            return float(value)
    numerator = row.get("numerator")
    denominator = row.get("denominator")
    if isinstance(numerator, int | float | Decimal) and isinstance(
        denominator, int | float | Decimal
    ):
        return float(denominator) / float(numerator)
    return None


__all__ = ["ScanEngine"]
