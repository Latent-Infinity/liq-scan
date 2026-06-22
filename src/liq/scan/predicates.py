"""Predicate combinators for ``ScanQuery``.

Each predicate is a frozen Pydantic model with one ``evaluate(row)``
method returning ``bool``. They compose through :class:`AndPredicate`
so a query can mix a move threshold with a dollar-volume floor
without growing query keywords.

Predicates are pure: they read fields off :class:`PredicateInput` and
never touch the store. The engine assembles the inputs per symbol.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PredicateInput(BaseModel):
    """One row of per-symbol summary fed to ``predicate.evaluate``."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    move_pct: float
    dollar_volume: Decimal
    price: Decimal
    bar_count: int


class MeanReversionPredicateInput(PredicateInput):
    """Precomputed mean-reversion row fed to the anchor predicate."""

    midrange_now: Decimal
    midrange_base: Decimal
    excursion_units: Decimal
    vol_t: Decimal
    bar_index: int = Field(ge=0)
    anchor_ts: datetime
    quality_flags: tuple[str, ...] = ()


Direction = Literal["up", "down", "either"]


class _BasePredicate(BaseModel):
    model_config = ConfigDict(frozen=True)

    def evaluate(self, row: PredicateInput) -> bool:  # pragma: no cover
        raise NotImplementedError


class MovePredicate(_BasePredicate):
    """Pass when the (signed) move clears ``threshold_pct``.

    ``direction="either"`` compares ``abs(move_pct) >= threshold``;
    ``"up"`` requires ``move_pct >= threshold``; ``"down"`` requires
    ``move_pct <= -threshold``.
    """

    threshold_pct: float = Field(ge=0.0)
    direction: Direction
    k: int = Field(default=5, ge=1, description="Endpoint k-bar aggregate size")

    def evaluate(self, row: PredicateInput) -> bool:
        if self.direction == "up":
            return row.move_pct >= self.threshold_pct
        if self.direction == "down":
            return row.move_pct <= -self.threshold_pct
        return abs(row.move_pct) >= self.threshold_pct


class DollarVolumePredicate(_BasePredicate):
    """Pass when ``dollar_volume >= min_usd``."""

    min_usd: Decimal

    def evaluate(self, row: PredicateInput) -> bool:
        return row.dollar_volume >= self.min_usd


class PricePredicate(_BasePredicate):
    """Pass when ``price >= min_usd`` (typical penny-stock filter)."""

    min_usd: Decimal

    def evaluate(self, row: PredicateInput) -> bool:
        return row.price >= self.min_usd


class MeanReversionExcursionPredicate(_BasePredicate):
    """Threshold/sign decision over precomputed excursion units."""

    K: float = Field(gt=0.0)
    L_vol: int = Field(gt=0)
    L_base: int = Field(gt=0)
    base_kind: Literal["roll_extreme", "roll_mean"]
    direction: Literal["up", "down", "both"]
    metric_version: str

    def evaluate(self, row: PredicateInput) -> bool:
        if not isinstance(row, MeanReversionPredicateInput):
            return False
        units = float(row.excursion_units)
        if self.direction == "up":
            return units >= self.K
        if self.direction == "down":
            return units <= -self.K
        return abs(units) >= self.K


class AndPredicate(_BasePredicate):
    """Logical AND across a list of child predicates.

    An empty child list is the universal predicate (passes every row).
    Evaluation short-circuits on the first ``False``.
    """

    predicates: list[AnyPredicate]

    def evaluate(self, row: PredicateInput) -> bool:
        return all(child.evaluate(row) for child in self.predicates)


AnyPredicate = (
    MovePredicate
    | DollarVolumePredicate
    | PricePredicate
    | MeanReversionExcursionPredicate
    | AndPredicate
)
"""Union of every concrete predicate. Used as the field type on ``ScanQuery``."""


AndPredicate.model_rebuild()


__all__ = [
    "AndPredicate",
    "AnyPredicate",
    "Direction",
    "DollarVolumePredicate",
    "MeanReversionExcursionPredicate",
    "MeanReversionPredicateInput",
    "MovePredicate",
    "PredicateInput",
    "PricePredicate",
]
