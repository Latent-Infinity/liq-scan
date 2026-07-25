"""Tests for the deterministic reverse/stressed DCF."""

from __future__ import annotations

import pytest

from liq.scan.resilience import (
    BUSINESS_PROFILES,
    DcfConfig,
    FundamentalScanInput,
    dcf_equity_value,
    fundamental_drawdown,
    implied_growth,
)

# Clean config for hand-checkable goldens: 10% WACC, 0% terminal growth, 10y.
CLEAN = DcfConfig(wacc_base=0.10, terminal_growth=0.0, horizon_years=10)


class TestDcfEquityValue:
    def test_zero_growth_zero_terminal_golden(self) -> None:
        # Flat FCF of 100 at 10% discount => annuity (614.46) + terminal
        # (1000 discounted 10y = 385.54) ≈ 1000.
        assert dcf_equity_value(100.0, 0.0, 0.10, 0.0, 10) == pytest.approx(1000.0, abs=0.5)

    def test_monotonic_increasing_in_growth(self) -> None:
        base = dcf_equity_value(100.0, 0.05, 0.10, 0.02, 10)
        higher = dcf_equity_value(100.0, 0.08, 0.10, 0.02, 10)
        assert higher > base

    def test_requires_wacc_above_terminal_growth(self) -> None:
        with pytest.raises(ValueError, match="wacc must exceed"):
            dcf_equity_value(100.0, 0.0, 0.02, 0.03, 10)


class TestImpliedGrowth:
    def test_recovers_zero_growth(self) -> None:
        # A market cap equal to the flat-FCF value implies ~0 growth.
        assert implied_growth(1000.0, 100.0, CLEAN) == pytest.approx(0.0, abs=1e-3)

    def test_higher_price_implies_higher_growth(self) -> None:
        low = implied_growth(1000.0, 100.0, CLEAN)
        high = implied_growth(2000.0, 100.0, CLEAN)
        assert low is not None and high is not None
        assert high > low

    def test_none_on_nonpositive_fcf(self) -> None:
        assert implied_growth(1000.0, 0.0, CLEAN) is None
        assert implied_growth(1000.0, -50.0, CLEAN) is None

    def test_clamps_to_ceiling_for_extreme_price(self) -> None:
        assert implied_growth(1e12, 100.0, CLEAN, hi=1.0) == 1.0

    def test_clamps_to_floor_for_tiny_price(self) -> None:
        assert implied_growth(1.0, 100.0, CLEAN, lo=-0.5) == -0.5


class TestFundamentalDrawdown:
    def _inp(self, fcf: float | None, market_cap: float = 1000.0) -> FundamentalScanInput:
        return FundamentalScanInput(
            symbol="X", business_type="ai_accelerator", market_cap=market_cap, fcf=fcf
        )

    def test_positive_drawdown_when_stressed(self) -> None:
        profile = BUSINESS_PROFILES["ai_accelerator"]
        dd, igb = fundamental_drawdown(self._inp(50.0), profile, DcfConfig())
        assert dd is not None and 0.0 < dd <= 1.0
        assert igb is not None

    def test_none_without_fcf(self) -> None:
        profile = BUSINESS_PROFILES["ai_accelerator"]
        dd, igb = fundamental_drawdown(self._inp(None), profile, DcfConfig())
        assert dd is None and igb is None

    def test_none_on_nonpositive_fcf(self) -> None:
        profile = BUSINESS_PROFILES["ai_accelerator"]
        dd, igb = fundamental_drawdown(self._inp(-10.0), profile, DcfConfig())
        assert dd is None and igb is None

    def test_larger_haircut_deepens_drawdown(self) -> None:
        mild = BUSINESS_PROFILES["diversified_tech"]
        severe = BUSINESS_PROFILES["ai_accelerator"]
        dd_mild, _ = fundamental_drawdown(self._inp(50.0), mild, DcfConfig())
        dd_severe, _ = fundamental_drawdown(self._inp(50.0), severe, DcfConfig())
        assert dd_mild is not None and dd_severe is not None
        assert dd_severe > dd_mild
