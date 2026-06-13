"""Tests for the predicate combinator hierarchy.

The predicate evaluates a per-symbol summary row (move_pct,
dollar_volume, price) and returns ``True`` if the symbol should
appear in the ranked output.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from liq.scan.predicates import (
    AndPredicate,
    DollarVolumePredicate,
    MovePredicate,
    PredicateInput,
    PricePredicate,
)


def _row(
    *,
    move_pct: float = 0.0,
    dollar_volume: float = 0.0,
    price: float = 0.0,
    bar_count: int = 100,
) -> PredicateInput:
    return PredicateInput(
        symbol="X",
        move_pct=move_pct,
        dollar_volume=Decimal(str(dollar_volume)),
        price=Decimal(str(price)),
        bar_count=bar_count,
    )


# ----- MovePredicate --------------------------------------------------------


class TestMovePredicate:
    def test_passes_when_abs_move_meets_threshold_either(self) -> None:
        p = MovePredicate(threshold_pct=5.0, direction="either")
        assert p.evaluate(_row(move_pct=5.0))
        assert p.evaluate(_row(move_pct=-6.0))
        assert not p.evaluate(_row(move_pct=4.99))

    def test_up_direction_excludes_negative_moves(self) -> None:
        p = MovePredicate(threshold_pct=3.0, direction="up")
        assert p.evaluate(_row(move_pct=3.0))
        assert not p.evaluate(_row(move_pct=-3.0))

    def test_down_direction_excludes_positive_moves(self) -> None:
        p = MovePredicate(threshold_pct=3.0, direction="down")
        assert p.evaluate(_row(move_pct=-3.0))
        assert not p.evaluate(_row(move_pct=3.0))

    def test_negative_threshold_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MovePredicate(threshold_pct=-1.0, direction="either")

    def test_invalid_direction_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MovePredicate(threshold_pct=1.0, direction="sideways")


# ----- DollarVolumePredicate ------------------------------------------------


class TestDollarVolumePredicate:
    def test_meets_minimum(self) -> None:
        p = DollarVolumePredicate(min_usd=Decimal("1000000"))
        assert p.evaluate(_row(dollar_volume=1_000_000))
        assert not p.evaluate(_row(dollar_volume=999_999))


# ----- PricePredicate -------------------------------------------------------


class TestPricePredicate:
    def test_meets_minimum_price(self) -> None:
        p = PricePredicate(min_usd=Decimal("5"))
        assert p.evaluate(_row(price=5.01))
        assert not p.evaluate(_row(price=4.99))


# ----- AndPredicate ---------------------------------------------------------


class TestAndPredicate:
    def test_all_must_pass(self) -> None:
        p = AndPredicate(
            predicates=[
                MovePredicate(threshold_pct=5.0, direction="either"),
                DollarVolumePredicate(min_usd=Decimal("1000000")),
                PricePredicate(min_usd=Decimal("5")),
            ]
        )
        passing = _row(move_pct=6.0, dollar_volume=2_000_000, price=10.0)
        failing_volume = _row(move_pct=6.0, dollar_volume=500_000, price=10.0)
        failing_price = _row(move_pct=6.0, dollar_volume=2_000_000, price=3.0)
        assert p.evaluate(passing)
        assert not p.evaluate(failing_volume)
        assert not p.evaluate(failing_price)

    def test_empty_chain_is_universal(self) -> None:
        # An AndPredicate with no children passes every row.
        p = AndPredicate(predicates=[])
        assert p.evaluate(_row())

    def test_short_circuit_on_first_fail(self) -> None:
        """Behavioural: the chain returns False as soon as a child fails;
        we observe by sending in an obviously short-circuit-friendly case."""
        p = AndPredicate(
            predicates=[
                MovePredicate(threshold_pct=10.0, direction="up"),
                # This one would also fail but should never be reached:
                DollarVolumePredicate(min_usd=Decimal("999999999999")),
            ]
        )
        assert not p.evaluate(_row(move_pct=1.0, dollar_volume=10**12))
