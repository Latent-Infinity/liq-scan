"""DEV_SMOKE_ONLY arithmetic fixtures; these are not financial evidence."""

from datetime import UTC, datetime, timedelta
from math import nextafter

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from liq.scan.rolling_shock import DetectionInputError, detect_rolling_shocks

UTC_DTYPE = pl.Datetime("us", "UTC")
START = datetime(2024, 3, 11, 13, 30, tzinfo=UTC)
REFERENCE = datetime(2024, 3, 8, 14, 30, tzinfo=UTC)


def bars(prices: list[float], missing_reference: int | None = None) -> pl.DataFrame:
    reference = [
        ("DEV", REFERENCE + timedelta(minutes=i), 100.0)
        for i in range(len(prices))
        if i != missing_reference
    ]
    current = [("DEV", START + timedelta(minutes=i), price) for i, price in enumerate(prices)]
    return pl.DataFrame(
        reference + current,
        schema={
            "symbol": pl.String,
            "timestamp": UTC_DTYPE,
            "close": pl.Float64,
        },
        orient="row",
    )


@pytest.mark.parametrize(
    ("prices", "directions", "offsets"),
    [
        ([106.0, 107.0, 104.0, 106.0], ["up", "up"], [0, 3]),
        ([94.0, 93.0, 96.0, 94.0], ["down", "down"], [0, 3]),
        ([106.0, 94.0, 93.0, 106.0], ["up", "down", "up"], [0, 1, 3]),
    ],
)
def test_first_entries_and_rearming(
    prices: list[float], directions: list[str], offsets: list[int]
) -> None:
    given = bars(prices)
    actual = detect_rolling_shocks(given, 0.05).events
    assert actual["direction"].to_list() == directions
    assert actual["trigger_ts"].to_list() == [START + timedelta(minutes=i) for i in offsets]


@pytest.mark.parametrize("price", [105.0, 95.0])
def test_exact_five_percent_is_inside_band(price: float) -> None:
    given = bars([price])
    actual = detect_rolling_shocks(given, 0.05)
    assert actual.events.is_empty()


@pytest.mark.parametrize("price", [nextafter(105.0, float("inf")), nextafter(95.0, 0.0)])
def test_next_representable_price_outside_boundary_triggers(price: float) -> None:
    given = bars([price])
    actual = detect_rolling_shocks(given, 0.05)
    assert actual.events.height == 1


def test_missing_reference_does_not_rearm_active_episode() -> None:
    given = bars([106.0, 100.0, 107.0], missing_reference=1)
    actual = detect_rolling_shocks(given, 0.05)
    assert actual.events["trigger_ts"].to_list() == [START]
    assert actual.exclusions.filter(pl.col("timestamp") == START + timedelta(minutes=1))[
        "reason"
    ].to_list() == ["missing_reference"]


def test_early_close_reference_has_explicit_exclusion() -> None:
    given = pl.DataFrame(
        {"symbol": ["DEV"], "timestamp": [datetime(2024, 7, 5, 17, tzinfo=UTC)], "close": [1.0]}
    )
    actual = detect_rolling_shocks(given, 0.05)
    assert actual.exclusions["reason"].to_list() == ["target_clock_unavailable"]


def test_events_are_available_after_trigger_bar_completes() -> None:
    given = bars([106.0])
    actual = detect_rolling_shocks(given, 0.05).events.row(0, named=True)
    assert actual["reference_ts"] == REFERENCE
    assert actual["available_at"] == START + timedelta(minutes=1)
    assert actual["reference_price"] == 100.0
    assert actual["trigger_price"] == 106.0
    assert actual["shock_return"] == pytest.approx(0.06)


def test_all_prefixes_preserve_earlier_events() -> None:
    given = bars([106.0, 108.0, 94.0, 100.0, 94.0])
    full = detect_rolling_shocks(given, 0.05).events
    for cutoff in given["timestamp"].sort().to_list():
        prefix = detect_rolling_shocks(given.filter(pl.col("timestamp") <= cutoff), 0.05).events
        assert_frame_equal(prefix, full.filter(pl.col("trigger_ts") <= cutoff))


def test_future_price_changes_do_not_change_earlier_events() -> None:
    given = bars([106.0, 100.0, 94.0])
    earlier = detect_rolling_shocks(given, 0.05).events.filter(pl.col("trigger_ts") == START)
    changed = given.with_columns(
        pl.when(pl.col("timestamp") > START).then(1000.0).otherwise(pl.col("close")).alias("close")
    )
    actual = detect_rolling_shocks(changed, 0.05).events.filter(pl.col("trigger_ts") == START)
    assert_frame_equal(actual, earlier)


def test_input_order_and_symbol_episodes_are_independent() -> None:
    given = pl.concat(
        [bars([106.0, 107.0]), bars([94.0, 93.0]).with_columns(pl.lit("OTHER").alias("symbol"))]
    )
    expected = detect_rolling_shocks(given, 0.05)
    actual = detect_rolling_shocks(given.reverse(), 0.05)
    assert_frame_equal(actual.events, expected.events)
    assert_frame_equal(actual.exclusions, expected.exclusions)
    assert actual.events["symbol"].to_list() == ["DEV", "OTHER"]
    assert actual.events["event_id"].n_unique() == 2


@pytest.mark.parametrize("threshold", [0.0, -0.01, float("inf"), float("nan")])
def test_invalid_threshold_is_rejected(threshold: float) -> None:
    with pytest.raises(DetectionInputError):
        detect_rolling_shocks(bars([106.0]), threshold)


@pytest.mark.parametrize("price", [0.0, -1.0, float("inf"), float("nan"), None])
def test_invalid_close_is_rejected(price: float | None) -> None:
    given = bars([106.0]).with_columns(pl.lit(price).cast(pl.Float64).alias("close"))
    with pytest.raises(DetectionInputError):
        detect_rolling_shocks(given, 0.05)


def test_duplicate_symbol_timestamp_is_rejected() -> None:
    given = bars([106.0])
    with pytest.raises(DetectionInputError):
        detect_rolling_shocks(pl.concat([given, given]), 0.05)


@pytest.mark.parametrize(
    "timestamp",
    [
        datetime(2024, 3, 11, 20, 0, tzinfo=UTC),
        datetime(2024, 3, 11, 13, 30, 1, tzinfo=UTC),
        datetime(2024, 3, 11, 13, 30),
        None,
    ],
)
def test_invalid_timestamp_is_rejected(timestamp: datetime | None) -> None:
    given = bars([106.0]).head(1).with_columns(pl.lit(timestamp).alias("timestamp"))
    with pytest.raises(DetectionInputError):
        detect_rolling_shocks(given, 0.05)


def test_empty_results_retain_schema() -> None:
    given = bars([])
    actual = detect_rolling_shocks(given, 0.05)
    assert actual.events.schema == {
        "event_id": pl.String,
        "symbol": pl.String,
        "reference_ts": UTC_DTYPE,
        "trigger_ts": UTC_DTYPE,
        "available_at": UTC_DTYPE,
        "reference_price": pl.Float64,
        "trigger_price": pl.Float64,
        "shock_return": pl.Float64,
        "direction": pl.String,
    }
    assert actual.exclusions.schema == {
        "symbol": pl.String,
        "timestamp": UTC_DTYPE,
        "reason": pl.String,
    }


def test_schema_requires_normalized_bar_columns() -> None:
    given = bars([106.0]).drop("close")
    with pytest.raises(DetectionInputError):
        detect_rolling_shocks(given, 0.05)


def test_decimal_context_does_not_change_strict_threshold() -> None:
    from decimal import localcontext

    given = bars([nextafter(105.0, float("inf"))])
    with localcontext() as context:
        context.prec = 4
        actual = detect_rolling_shocks(given, 0.05)
    assert actual.events.height == 1


def test_unrepresentable_return_is_rejected() -> None:
    given = bars([1e308]).with_columns(
        pl.when(pl.col("timestamp") == REFERENCE)
        .then(1e-308)
        .otherwise(pl.col("close"))
        .alias("close")
    )
    with pytest.raises(DetectionInputError):
        detect_rolling_shocks(given, 0.05)


def test_nanosecond_precision_is_rejected_before_python_conversion() -> None:
    given = bars([106.0]).with_columns(
        (pl.col("timestamp").cast(pl.Datetime("ns", "UTC")) + pl.duration(nanoseconds=1)).alias(
            "timestamp"
        )
    )
    with pytest.raises(DetectionInputError):
        detect_rolling_shocks(given, 0.05)


@pytest.mark.parametrize("symbol", [None, ""])
def test_missing_symbol_is_rejected(symbol: str | None) -> None:
    given = bars([106.0]).with_columns(pl.lit(symbol).cast(pl.String).alias("symbol"))
    with pytest.raises(DetectionInputError):
        detect_rolling_shocks(given, 0.05)


def test_exact_boundary_rearms_an_episode() -> None:
    given = bars([106.0, 105.0, 106.0])
    actual = detect_rolling_shocks(given, 0.05).events
    assert actual["trigger_ts"].to_list() == [START, START + timedelta(minutes=2)]
