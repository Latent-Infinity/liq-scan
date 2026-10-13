"""Cardinality controls use integers, not market observations or signals."""

import pytest

from liq.scan.classical_ranker import decile_count
from liq.scan.classical_ranker_types import SelectionRefused


@pytest.mark.parametrize(
    ("population", "expected"), [(2, 1), (90, 9), (100, 10), (101, 11), (110, 11), (111, 12)]
)
def test_ceiling_decile_when_population_has_a_boundary(population: int, expected: int) -> None:
    result = decile_count(population)
    assert result == expected


@pytest.mark.parametrize("population", [0, 1, -1, True])
def test_refusal_when_population_is_not_a_selectable_integer(population: int) -> None:
    with pytest.raises(SelectionRefused, match="decile_population"):
        decile_count(population)
