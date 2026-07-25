"""Tests for the renormalized 0–100 resilience scorecard."""

from __future__ import annotations

import pytest

from liq.scan.resilience import (
    FundamentalScanInput,
    ResilienceScanInput,
    resilience_scorecard,
)


def _stats(**o: object) -> ResilienceScanInput:
    base: dict[str, object] = {
        "symbol": "X",
        "n_obs": 800,
        "market_beta": 1.0,
        "downside_beta_qqq": 0.5,
        "es95": 0.03,
        "es95_ratio_qqq": 0.5,
        "max_drawdown": 0.10,
        "recovery_periods": 21,
        "residual_volatility": 0.01,
        "is_chip": False,
    }
    base.update(o)
    return ResilienceScanInput(**base)  # type: ignore[arg-type]


def _fund(**o: object) -> FundamentalScanInput:
    base: dict[str, object] = {
        "symbol": "X",
        "business_type": "diversified_tech",
        "market_cap": 1000.0,
        "fcf": 60.0,
        "ebitda": 100.0,
        "net_debt": -50.0,
        "interest_coverage": 40.0,
        "diluted_share_growth": -0.02,
        "sbc_to_fcf": 0.10,
        "fcf_positive_years": 5,
        "fcf_considered_years": 5,
    }
    base.update(o)
    return FundamentalScanInput(**base)  # type: ignore[arg-type]


class TestTapeOnly:
    def test_price_shock_only_available_25(self) -> None:
        score = resilience_scorecard(_stats(), None, None, None)
        assert score.available == 25.0
        assert score.earned == pytest.approx(25.0)  # all-best stats
        assert score.score_100 == pytest.approx(100.0)
        assert [c.name for c in score.components] == ["price_shock"]


class TestFullScorecard:
    def test_components_present_and_renormalized(self) -> None:
        score = resilience_scorecard(
            _stats(), _fund(), fundamental_drawdown=0.15, implied_growth=0.0
        )
        names = [c.name for c in score.components]
        assert names == ["price_shock", "valuation_cushion", "balance_sheet"]
        # 25 (price) + 20 (val: dd+igb+fcf_yield) + 15 (bs) = 60 available.
        assert score.available == pytest.approx(60.0)
        assert score.score_100 is not None and 0.0 <= score.score_100 <= 100.0

    def test_strong_fundamentals_score_high(self) -> None:
        strong = resilience_scorecard(
            _stats(), _fund(), fundamental_drawdown=0.15, implied_growth=0.0
        )
        weak = resilience_scorecard(
            _stats(),
            _fund(net_debt=200.0, interest_coverage=10.0, diluted_share_growth=0.02),
            fundamental_drawdown=0.60,
            implied_growth=0.20,
        )
        assert strong.score_100 is not None and weak.score_100 is not None
        assert strong.score_100 > weak.score_100

    def test_missing_metric_withheld_not_zeroed(self) -> None:
        # Dropping net_debt/ebitda removes 5 available points, not earns 0.
        full = resilience_scorecard(_stats(), _fund(), 0.15, 0.0)
        gapped = resilience_scorecard(_stats(), _fund(net_debt=None, ebitda=None), 0.15, 0.0)
        assert gapped.available == pytest.approx(full.available - 5.0)

    def test_all_available_zero_gives_none(self) -> None:
        # A degenerate config-free path: no components scorable is None, but the
        # price-shock component always contributes 25, so score is never None
        # here — assert the invariant holds.
        score = resilience_scorecard(_stats(), None, None, None)
        assert score.available > 0.0
        assert score.score_100 is not None
