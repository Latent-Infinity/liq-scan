"""Mean-reversion anchor fact models."""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AnchorDirection = Literal["up", "down"]
RegimeLabel = Literal["trend", "chop", "indeterminate"]


class AnchorVolSource(BaseModel):
    """Provenance for the volatility value used by an anchor."""

    model_config = ConfigDict(frozen=True)

    estimator: str
    lookback: int = Field(gt=0)
    min_periods: int = Field(gt=0)
    calendar_policy: str
    availability_ts: datetime


class AnchorEvent(BaseModel):
    """Stable identity and query provenance for a scanner anchor."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    anchor_ts: datetime
    direction: AnchorDirection
    anchor_event_id: str
    scan_run_id: str
    scan_query_version: str
    metric_version: str
    resolved_universe_version: str
    quality_flags: tuple[str, ...] = ()


class MeanReversionAnchor(AnchorEvent):
    """Mean-reversion excursion anchor emitted by the dedicated evaluator."""

    excursion_units: Decimal
    midrange_now: Decimal
    midrange_base: Decimal
    reversion_target: Decimal
    vol_t: Decimal
    anchor_vol_source: AnchorVolSource
    regime_at_anchor: RegimeLabel | None = None


def compute_anchor_event_id(
    scan_run_id: str,
    symbol: str,
    anchor_ts: datetime,
    scan_query_version: str,
    direction: AnchorDirection,
) -> str:
    """Deterministic SHA-256 id for an anchor identity tuple."""
    payload = "|".join(
        (
            scan_run_id,
            symbol.upper(),
            anchor_ts.isoformat(),
            scan_query_version,
            direction,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "AnchorDirection",
    "AnchorEvent",
    "AnchorVolSource",
    "MeanReversionAnchor",
    "RegimeLabel",
    "compute_anchor_event_id",
]
