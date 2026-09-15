"""Stable tables for bar-start rolling shock measurement."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final, NamedTuple

import polars as pl

UTC_DATETIME: Final = pl.Datetime("us", "UTC")
EVENT_SCHEMA: Final = {
    "event_id": pl.String,
    "symbol": pl.String,
    "reference_ts": UTC_DATETIME,
    "trigger_ts": UTC_DATETIME,
    "available_at": UTC_DATETIME,
    "reference_price": pl.Float64,
    "trigger_price": pl.Float64,
    "shock_return": pl.Float64,
    "direction": pl.String,
}
EXCLUSION_SCHEMA: Final = {
    "symbol": pl.String,
    "timestamp": UTC_DATETIME,
    "reason": pl.String,
}


@dataclass(frozen=True, slots=True)
class DetectionResult:
    events: pl.DataFrame
    exclusions: pl.DataFrame


class DetectionInputError(ValueError):
    reason: str

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class Bar(NamedTuple):
    symbol: str
    timestamp: datetime
    close: float | Decimal
