"""``ScanQuery`` and ``ScanResult`` domain types.

Both are frozen Pydantic models so they hash, compare, and round-trip
through JSON without ceremony. ``ScanQuery`` is the operator's input
to ``ScanEngine.execute``; ``ScanResult`` is the per-symbol output.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from liq.scan.predicates import AnyPredicate
from liq.scan.window import WindowSpec

Ranking = Literal["abs_move", "up", "down"]
SplitHandling = Literal["adjust", "exclude"]


class ScanQuery(BaseModel):
    """Operator-facing query passed to ``ScanEngine.execute``."""

    model_config = ConfigDict(frozen=True)

    universe_ref: str | list[str]
    as_of: datetime
    window: WindowSpec
    predicate: AnyPredicate
    ranking: Ranking = "abs_move"
    limit: int | None = Field(default=None, gt=0)
    include_extended_hours: bool = False
    metric_version: str = "midrange-endpoint-v1"
    split_handling: SplitHandling = "adjust"

    @field_validator("universe_ref")
    @classmethod
    def _non_empty_universe(cls, value: str | list[str]) -> str | list[str]:
        if isinstance(value, str):
            if not value.strip():
                raise ValueError("universe_ref string must be non-empty")
        elif not value:
            raise ValueError("universe_ref list must be non-empty")
        return value

    @field_validator("as_of")
    @classmethod
    def _require_tz_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        return value


class ScanResult(BaseModel):
    """One row of ``ScanEngine.execute`` output."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    as_of: datetime
    move_pct: float
    direction: Literal["up", "down", "flat"]
    window_actual: tuple[datetime, datetime]
    bar_count: int
    dollar_volume: Decimal
    metric_version: str
    split_event: str | None = None


__all__ = ["Ranking", "ScanQuery", "ScanResult", "SplitHandling"]
