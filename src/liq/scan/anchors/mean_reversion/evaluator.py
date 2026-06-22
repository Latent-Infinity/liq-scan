"""Evaluator that turns bar windows into mean-reversion anchors."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal

import polars as pl
from pydantic import BaseModel, ConfigDict

from liq.scan.anchors.mean_reversion.models import (
    AnchorVolSource,
    MeanReversionAnchor,
    compute_anchor_event_id,
)
from liq.scan.predicates import (
    AndPredicate,
    MeanReversionExcursionPredicate,
    MeanReversionPredicateInput,
    RegimePredicate,
)


def _high_low(
    bars: pl.DataFrame | Mapping[str, Sequence[float]],
) -> tuple[list[float], list[float]]:
    if isinstance(bars, pl.DataFrame):
        return [float(v) for v in bars["high"].to_list()], [float(v) for v in bars["low"].to_list()]
    return [float(v) for v in bars["high"]], [float(v) for v in bars["low"]]


def _roll_extreme_midrange(
    bars: pl.DataFrame | Mapping[str, Sequence[float]],
    lookback: int,
) -> list[float]:
    high, low = _high_low(bars)
    out: list[float] = []
    for end in range(len(high)):
        start = end - lookback + 1
        if start < 0:
            out.append(float("nan"))
            continue
        out.append((max(high[start : end + 1]) + min(low[start : end + 1])) / 2.0)
    return out


def _roll_mean_midrange(
    bars: pl.DataFrame | Mapping[str, Sequence[float]],
    lookback: int,
) -> list[float]:
    high, low = _high_low(bars)
    mids = [(h + lo) / 2.0 for h, lo in zip(high, low, strict=True)]
    out: list[float] = []
    for end in range(len(mids)):
        start = end - lookback + 1
        if start < 0:
            out.append(float("nan"))
            continue
        out.append(sum(mids[start : end + 1]) / lookback)
    return out


def _trailing_range_vol(
    bars: pl.DataFrame | Mapping[str, Sequence[float]],
    lookback: int,
) -> list[float]:
    high, low = _high_low(bars)
    ranges = [h - lo for h, lo in zip(high, low, strict=True)]
    out: list[float] = []
    for end in range(len(ranges)):
        start = end - lookback + 1
        if start < 0:
            out.append(float("nan"))
            continue
        out.append(sum(ranges[start : end + 1]) / lookback)
    return out


class AnchorEvaluator(BaseModel):
    """Build mean-reversion anchors from preloaded per-symbol bars."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    predicate: MeanReversionExcursionPredicate | AndPredicate
    scan_run_id: str
    scan_query_version: str
    metric_version: str
    resolved_universe_version: str
    calendar_policy: str = "bar_count"

    def evaluate_symbol(self, symbol: str, bars: pl.DataFrame) -> list[MeanReversionAnchor]:
        if bars.is_empty():
            return []
        ordered = bars.sort("timestamp")
        midrange_now = [
            (float(h) + float(lo)) / 2.0
            for h, lo in zip(ordered["high"].to_list(), ordered["low"].to_list(), strict=True)
        ]
        excursion_predicate = self._excursion_predicate()
        regime_predicate = self._regime_predicate()
        base_series = self._base_series(ordered, excursion_predicate)
        vol_series = _trailing_range_vol(ordered, excursion_predicate.L_vol)
        anchors: list[MeanReversionAnchor] = []
        timestamps = ordered["timestamp"].to_list()
        for index in range(ordered.height):
            if index == 0:
                continue
            base = base_series[index - 1]
            vol_t = vol_series[index - 1]
            now = midrange_now[index]
            flags = self._quality_flags(base=base, vol_t=vol_t)
            if flags or vol_t == 0.0:
                continue
            excursion_units = (now - base) / vol_t
            anchor_ts = timestamps[index]
            if not isinstance(anchor_ts, datetime):
                continue
            direction = "up" if excursion_units > 0 else "down" if excursion_units < 0 else "up"
            row = MeanReversionPredicateInput(
                symbol=symbol,
                move_pct=0.0,
                dollar_volume=Decimal("0"),
                price=Decimal(str(now)),
                bar_count=ordered.height,
                midrange_now=Decimal(str(now)),
                midrange_base=Decimal(str(base)),
                excursion_units=Decimal(str(excursion_units)),
                vol_t=Decimal(str(vol_t)),
                bar_index=index,
                anchor_ts=anchor_ts,
                quality_flags=flags,
            )
            if not self.predicate.evaluate(row):
                continue
            regime_at_anchor = (
                regime_predicate.label_at(index) if regime_predicate is not None else None
            )
            anchor_id = compute_anchor_event_id(
                self.scan_run_id,
                symbol,
                anchor_ts,
                self.scan_query_version,
                direction,
            )
            anchors.append(
                MeanReversionAnchor(
                    symbol=symbol,
                    anchor_ts=anchor_ts,
                    direction=direction,
                    anchor_event_id=anchor_id,
                    scan_run_id=self.scan_run_id,
                    scan_query_version=self.scan_query_version,
                    metric_version=self.metric_version,
                    resolved_universe_version=self.resolved_universe_version,
                    quality_flags=flags,
                    excursion_units=Decimal(str(excursion_units)),
                    midrange_now=Decimal(str(now)),
                    midrange_base=Decimal(str(base)),
                    reversion_target=Decimal(str(base)),
                    vol_t=Decimal(str(vol_t)),
                    anchor_vol_source=AnchorVolSource(
                        estimator="range_mean",
                        lookback=excursion_predicate.L_vol,
                        min_periods=excursion_predicate.L_vol,
                        calendar_policy=self.calendar_policy,
                        availability_ts=anchor_ts,
                    ),
                    regime_at_anchor=regime_at_anchor,
                )
            )
        return anchors

    def _base_series(
        self,
        bars: pl.DataFrame,
        predicate: MeanReversionExcursionPredicate,
    ) -> list[float]:
        if predicate.base_kind == "roll_extreme":
            return _roll_extreme_midrange(bars, predicate.L_base)
        return _roll_mean_midrange(bars, predicate.L_base)

    def _excursion_predicate(self) -> MeanReversionExcursionPredicate:
        if isinstance(self.predicate, MeanReversionExcursionPredicate):
            return self.predicate
        for child in self.predicate.predicates:
            if isinstance(child, MeanReversionExcursionPredicate):
                return child
        raise ValueError("AnchorEvaluator requires MeanReversionExcursionPredicate")

    def _regime_predicate(self) -> RegimePredicate | None:
        if isinstance(self.predicate, RegimePredicate):
            return self.predicate
        if isinstance(self.predicate, AndPredicate):
            for child in self.predicate.predicates:
                if isinstance(child, RegimePredicate):
                    return child
        return None

    @staticmethod
    def _quality_flags(*, base: float, vol_t: float) -> tuple[str, ...]:
        flags: list[str] = []
        if math.isnan(base):
            flags.append("base_warmup")
        if math.isnan(vol_t):
            flags.append("vol_warmup")
        if vol_t == 0.0:
            flags.append("zero_vol")
        return tuple(flags)


__all__ = ["AnchorEvaluator"]
