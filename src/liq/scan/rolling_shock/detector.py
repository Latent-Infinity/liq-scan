"""Causal threshold-entry detection on supplied normalized regular-session bars."""

from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from math import isfinite

import polars as pl

from liq.data.session_clock import SessionClockError, shift_session_minute
from liq.scan.rolling_shock.models import (
    EVENT_SCHEMA,
    EXCLUSION_SCHEMA,
    Bar,
    DetectionInputError,
    DetectionResult,
)


def _parse_bars(bars: pl.DataFrame) -> tuple[Bar, ...]:
    if not {"symbol", "timestamp", "close"} <= set(bars.columns):
        raise DetectionInputError("required columns: symbol, timestamp, close")
    timestamp_type = bars.schema["timestamp"]
    close_type = bars.schema["close"]
    if (
        bars.schema["symbol"] != pl.String
        or not isinstance(timestamp_type, pl.Datetime)
        or timestamp_type.time_zone != "UTC"
        or not (close_type.is_float() or close_type.is_decimal())
    ):
        raise DetectionInputError(
            "bars require string symbol, UTC datetime, and float or Decimal close"
        )
    selected = bars.select("symbol", "timestamp", "close")
    if any(selected.null_count().row(0)):
        raise DetectionInputError("bar columns must not contain nulls")
    if selected.select(pl.struct("symbol", "timestamp").is_duplicated().any()).item():
        raise DetectionInputError("duplicate symbol timestamp")
    finite_close = pl.col("close").is_finite() if close_type.is_float() else pl.lit(True)
    if selected.filter(
        (pl.col("symbol").str.len_chars() == 0) | ~finite_close | (pl.col("close") <= 0)
    ).height:
        raise DetectionInputError("symbols must be nonempty and closes finite and positive")
    if selected.filter(pl.col("timestamp").dt.epoch("ns") % 60_000_000_000 != 0).height:
        raise DetectionInputError("bar timestamps must have minute precision")
    return tuple(Bar(*row) for row in selected.sort("symbol", "timestamp").iter_rows())


def detect_rolling_shocks(bars: pl.DataFrame, threshold: float) -> DetectionResult:
    """Emit first strict threshold entries against the prior session's same clock.

    Input timestamps identify bar starts; events become available one minute later.
    An observed valid inside-band minute rearms an episode. Missing references do
    not rearm it; a sign reversal begins a new episode. No threshold is implied.
    This function only measures supplied bars and performs no data access.
    """
    if not isfinite(threshold) or threshold <= 0:
        raise DetectionInputError("threshold must be finite and positive")
    parsed = _parse_bars(bars)
    prices: dict[tuple[str, datetime], float | Decimal] = {}
    references: dict[datetime, datetime | None] = {}
    active: dict[str, int] = {}
    event_rows: list[tuple[str, str, datetime, datetime, datetime, float, float, float, str]] = []
    exclusion_rows: list[tuple[str, datetime, str]] = []
    threshold_num, threshold_den = Decimal(str(threshold)).as_integer_ratio()
    for bar in parsed:
        if bar.timestamp not in references:
            try:
                references[bar.timestamp] = shift_session_minute(bar.timestamp, -1)
            except SessionClockError as error:
                raise DetectionInputError(error.reason) from error
        reference_ts = references[bar.timestamp]
        prices[bar.symbol, bar.timestamp] = bar.close
        if reference_ts is None:
            exclusion_rows.append((bar.symbol, bar.timestamp, "target_clock_unavailable"))
            continue
        reference_price = prices.get((bar.symbol, reference_ts))
        if reference_price is None:
            exclusion_rows.append((bar.symbol, bar.timestamp, "missing_reference"))
            continue
        price_num, price_den = Decimal(str(bar.close)).as_integer_ratio()
        reference_num, reference_den = Decimal(str(reference_price)).as_integer_ratio()
        change = price_num * reference_den - reference_num * price_den
        outside = abs(change) * threshold_den > threshold_num * reference_num * price_den
        direction = (1 if change > 0 else -1) if outside else 0
        if direction and direction != active.get(bar.symbol, 0):
            shock_return = float(bar.close) / float(reference_price) - 1.0
            if not isfinite(shock_return):
                raise DetectionInputError("shock return exceeds finite float range")
            event_key = f"{len(bar.symbol)}:{bar.symbol}|{reference_ts.isoformat()}|{bar.timestamp.isoformat()}"
            event_rows.append(
                (
                    sha256(event_key.encode()).hexdigest(),
                    bar.symbol,
                    reference_ts,
                    bar.timestamp,
                    bar.timestamp + timedelta(minutes=1),
                    float(reference_price),
                    float(bar.close),
                    shock_return,
                    "up" if direction > 0 else "down",
                )
            )
        active[bar.symbol] = direction
    return DetectionResult(
        events=pl.DataFrame(event_rows, schema=EVENT_SCHEMA, orient="row"),
        exclusions=pl.DataFrame(exclusion_rows, schema=EXCLUSION_SCHEMA, orient="row"),
    )
