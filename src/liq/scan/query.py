"""``ScanQuery`` and ``ScanResult`` domain types.

Both are frozen Pydantic models so they hash, compare, and round-trip
through JSON without ceremony. ``ScanQuery`` is the operator's input
to ``ScanEngine.execute``; ``ScanResult`` is the per-symbol output.
``ScanQueryTemplate`` is the as-of-less shape used by sweeps and the
``--query queries/*.yaml`` CLI flag.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from liq.scan.predicates import AnyPredicate
from liq.scan.window import WindowSpec

Ranking = Literal["abs_move", "up", "down"]
SplitHandling = Literal["adjust", "exclude"]


class ScanQuery(BaseModel):
    """Operator-facing query passed to ``ScanEngine.execute``.

    ``universe_ref`` is the **research scope** — what we study and pull
    data for. ``tradable_ref`` (optional) is the **execution policy** —
    what we'd actually act on. Rank + limit run on the research set
    first; only after that are non-tradable names dropped. This keeps
    the two concerns separable: changing your broker doesn't change
    your research universe, and tightening research scope doesn't
    silently change your trade set.
    """

    model_config = ConfigDict(frozen=True)

    universe_ref: Any
    as_of: datetime
    window: WindowSpec
    predicate: AnyPredicate
    ranking: Ranking = "abs_move"
    limit: int | None = Field(default=None, gt=0)
    include_extended_hours: bool = False
    metric_version: str = "midrange-endpoint-v1"
    split_handling: SplitHandling = "adjust"
    tradable_ref: Any | None = None

    @field_validator("universe_ref")
    @classmethod
    def _non_empty_universe(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            raise ValueError("universe_ref string must be non-empty")
        if isinstance(value, list) and not value:
            raise ValueError("universe_ref list must be non-empty")
        return value

    @field_validator("tradable_ref")
    @classmethod
    def _non_empty_tradable(cls, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, str) and not value.strip():
            raise ValueError("tradable_ref string must be non-empty when set")
        if isinstance(value, list) and not value:
            raise ValueError("tradable_ref list must be non-empty when set")
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


class ScanQueryTemplate(BaseModel):
    """A ``ScanQuery`` shape with ``as_of`` removed — fed to a sweep.

    The sweep iterates as-of timestamps per cadence and builds one
    concrete ``ScanQuery`` per iteration via :meth:`with_as_of`.
    """

    model_config = ConfigDict(frozen=True)

    universe_ref: Any
    window: WindowSpec
    predicate: AnyPredicate
    ranking: Ranking = "abs_move"
    limit: int | None = Field(default=None, gt=0)
    include_extended_hours: bool = False
    metric_version: str = "midrange-endpoint-v1"
    split_handling: SplitHandling = "adjust"
    tradable_ref: Any | None = None

    def with_as_of(self, as_of: datetime) -> ScanQuery:
        return ScanQuery(
            universe_ref=self.universe_ref,
            as_of=as_of,
            window=self.window,
            predicate=self.predicate,
            ranking=self.ranking,
            limit=self.limit,
            include_extended_hours=self.include_extended_hours,
            metric_version=self.metric_version,
            split_handling=self.split_handling,
            tradable_ref=self.tradable_ref,
        )


__all__ = ["Ranking", "ScanQuery", "ScanQueryTemplate", "ScanResult", "SplitHandling"]
