"""Direct tests for the private evaluator helpers.

The evaluator carries its own ``_high_low`` / ``_roll_*`` / ``_trailing_range_vol``
helpers so it stays self-contained (no cross-import to ``liq.features``).
These tests cover both the ``pl.DataFrame`` and ``Mapping`` input paths so
the helpers do not silently lose Mapping support to a coverage gap.
"""

from __future__ import annotations

import math

import polars as pl
import pytest

from liq.scan.anchors.mean_reversion.evaluator import (
    _high_low,
    _roll_extreme_midrange,
    _roll_mean_midrange,
    _trailing_range_vol,
)


def test_high_low_dataframe_path() -> None:
    bars = pl.DataFrame({"high": [10.0, 11.0], "low": [9.0, 9.5]})

    high, low = _high_low(bars)

    assert high == pytest.approx([10.0, 11.0])
    assert low == pytest.approx([9.0, 9.5])


def test_high_low_mapping_path() -> None:
    high, low = _high_low({"high": [12.0, 13.0], "low": [11.0, 11.5]})

    assert high == pytest.approx([12.0, 13.0])
    assert low == pytest.approx([11.0, 11.5])


def test_roll_extreme_midrange_mapping_path() -> None:
    out = _roll_extreme_midrange(
        {"high": [10.0, 12.0, 11.0, 15.0], "low": [8.0, 9.0, 7.0, 14.0]},
        lookback=3,
    )

    assert math.isnan(out[0])
    assert math.isnan(out[1])
    assert out[2] == pytest.approx((12.0 + 7.0) / 2.0)
    assert out[3] == pytest.approx((15.0 + 7.0) / 2.0)


def test_roll_mean_midrange_mapping_path() -> None:
    out = _roll_mean_midrange(
        {"high": [10.0, 12.0, 14.0, 16.0], "low": [8.0, 8.0, 10.0, 12.0]},
        lookback=2,
    )

    assert math.isnan(out[0])
    assert out[1] == pytest.approx((9.0 + 10.0) / 2.0)
    assert out[2] == pytest.approx((10.0 + 12.0) / 2.0)
    assert out[3] == pytest.approx((12.0 + 14.0) / 2.0)


def test_trailing_range_vol_mapping_path() -> None:
    out = _trailing_range_vol(
        {"high": [10.0, 13.0, 15.0, 18.0], "low": [8.0, 10.0, 11.0, 13.0]},
        lookback=3,
    )

    assert math.isnan(out[0])
    assert math.isnan(out[1])
    assert out[2] == pytest.approx((2.0 + 3.0 + 4.0) / 3.0)
    assert out[3] == pytest.approx((3.0 + 4.0 + 5.0) / 3.0)
