"""DEV_SMOKE_ONLY Decimal boundary checks; no market observations."""

from datetime import UTC, datetime
from decimal import Decimal, localcontext

import polars as pl
import pytest

from liq.scan.rolling_shock import DetectionInputError, detect_rolling_shocks


@pytest.mark.parametrize(
    ("price", "directions"),
    [
        ("105.000000000000000000", []),
        ("95.000000000000000000", []),
        ("105.000000000000000001", ["up"]),
        ("94.999999999999999999", ["down"]),
        ("104.999999999999999999", []),
        ("95.000000000000000001", []),
    ],
)
def test_decimal_boundary_is_preserved(price: str, directions: list[str]) -> None:
    given = pl.DataFrame(
        {
            "symbol": ["DEV", "DEV"],
            "timestamp": [
                datetime(2024, 3, 8, 14, 30, tzinfo=UTC),
                datetime(2024, 3, 11, 13, 30, tzinfo=UTC),
            ],
            "close": [Decimal("100.000000000000000000"), Decimal(price)],
        }
    )
    with localcontext() as context:
        context.prec = 4
        actual = detect_rolling_shocks(given, 0.05)
    assert actual.events["direction"].to_list() == directions
    assert actual.events.schema["trigger_price"] == pl.Float64


@pytest.mark.parametrize("price", [Decimal("0"), Decimal("-1")])
def test_nonpositive_decimal_close_is_rejected(price: Decimal) -> None:
    given = pl.DataFrame(
        {
            "symbol": ["DEV"],
            "timestamp": [datetime(2024, 3, 11, 13, 30, tzinfo=UTC)],
            "close": [price],
        }
    )
    with pytest.raises(DetectionInputError):
        detect_rolling_shocks(given, 0.05)


def test_large_finite_float_prices_remain_supported() -> None:
    given = pl.DataFrame(
        {
            "symbol": ["DEV", "DEV"],
            "timestamp": [
                datetime(2024, 3, 8, 14, 30, tzinfo=UTC),
                datetime(2024, 3, 11, 13, 30, tzinfo=UTC),
            ],
            "close": [1e307, 1e308],
        }
    )
    actual = detect_rolling_shocks(given, 0.05)
    assert actual.events["shock_return"].to_list() == [9.0]
