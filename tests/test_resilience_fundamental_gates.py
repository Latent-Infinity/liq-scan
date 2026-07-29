"""Tests for the fundamental (financial-survival + valuation) gates and the
full ASD-25 = max(fundamental, tape) combination."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from liq.scan.resilience import (
    FundamentalScanInput,
    ResilienceScanInput,
    build_result,
    evaluate_fundamental_gates,
)

AS_OF = datetime(2026, 6, 30, tzinfo=UTC)


def _fund(**overrides: object) -> FundamentalScanInput:
    base: dict[str, object] = {
        "symbol": "TEST",
        "business_type": "diversified_tech",
        "market_cap": 1_000.0,
        "fcf": 60.0,  # 6% FCF yield
        "ebitda": 100.0,
        "net_debt": 100.0,  # 1.0x
        "interest_coverage": 20.0,
        "diluted_share_growth": 0.0,
        "sbc_to_fcf": 0.10,
        "fcf_positive_years": 5,
        "fcf_considered_years": 5,
        "is_profitable": True,
    }
    base.update(overrides)
    return FundamentalScanInput(**base)  # type: ignore[arg-type]


def _ids(outcomes) -> dict[str, tuple[bool, bool]]:
    return {o.gate_id: (o.passed, o.applicable) for o in outcomes}


_SURVIVAL_GATES = frozenset(
    {
        "ttm_fcf_positive",
        "historical_fcf",
        "net_debt_to_ebitda",
        "interest_coverage",
        "share_count_growth",
        "sbc_to_fcf",
    }
)


class TestFinancialSurvivalGates:
    def test_healthy_financials_pass_survival_gates(self) -> None:
        # A financially healthy name clears every financial-survival gate.
        # (Valuation gates are a separate, deliberately strict layer — under the
        # standardized severe stress almost nothing clears stressed_dcf ≤ 20%.)
        _, failed, _, _, _ = evaluate_fundamental_gates(_fund())
        assert _SURVIVAL_GATES.isdisjoint(failed), failed

    def test_net_debt_leverage_fail(self) -> None:
        _, failed, _, _, _ = evaluate_fundamental_gates(_fund(net_debt=200.0, ebitda=100.0))
        assert "net_debt_to_ebitda" in failed  # 2.0x > 1.5x default

    def test_interest_coverage_fail(self) -> None:
        _, failed, _, _, _ = evaluate_fundamental_gates(_fund(interest_coverage=5.0))
        assert "interest_coverage" in failed

    def test_share_dilution_fail(self) -> None:
        _, failed, _, _, _ = evaluate_fundamental_gates(_fund(diluted_share_growth=0.05))
        assert "share_count_growth" in failed

    def test_sbc_fail(self) -> None:
        _, failed, _, _, _ = evaluate_fundamental_gates(_fund(sbc_to_fcf=0.5))
        assert "sbc_to_fcf" in failed

    def test_historical_fcf_fail(self) -> None:
        _, failed, _, _, _ = evaluate_fundamental_gates(
            _fund(fcf_positive_years=3, fcf_considered_years=5)
        )
        assert "historical_fcf" in failed

    def test_net_cash_passes_leverage(self) -> None:
        _, failed, _, _, _ = evaluate_fundamental_gates(_fund(net_debt=-50.0, market_cap=250.0))
        assert "net_debt_to_ebitda" not in failed


class TestUnscored:
    def test_missing_metrics_are_not_applicable(self) -> None:
        _, failed, outcomes, _, _ = evaluate_fundamental_gates(
            _fund(net_debt=None, interest_coverage=None, sbc_to_fcf=None, market_cap=250.0)
        )
        ids = _ids(outcomes)
        assert ids["net_debt_to_ebitda"] == (True, False)
        assert ids["interest_coverage"] == (True, False)
        assert ids["sbc_to_fcf"] == (True, False)
        assert "net_debt_to_ebitda" not in failed

    def test_short_history_unscored(self) -> None:
        _, _, outcomes, _, _ = evaluate_fundamental_gates(
            _fund(fcf_positive_years=2, fcf_considered_years=2, market_cap=250.0)
        )
        assert _ids(outcomes)["historical_fcf"] == (True, False)


class TestUnprofitablePath:
    def test_cash_runway_applies_only_when_unprofitable(self) -> None:
        _, _, outcomes, _, _ = evaluate_fundamental_gates(_fund(is_profitable=True))
        assert _ids(outcomes)["cash_runway"][1] is False  # not applicable

    def test_unprofitable_short_runway_fails(self) -> None:
        _, failed, _, _, _ = evaluate_fundamental_gates(
            _fund(is_profitable=False, cash_runway_years=2.0, fcf=None, market_cap=250.0)
        )
        assert "cash_runway" in failed


class TestValuationGates:
    def test_rich_valuation_fails_igb_and_yield(self) -> None:
        # Expensive: 1% FCF yield -> high implied growth, low yield.
        _, failed, _, dd, igb = evaluate_fundamental_gates(_fund(market_cap=6_000.0, fcf=60.0))
        assert igb is not None and igb > 0.15
        assert "implied_growth_igb10" in failed
        assert "fcf_yield" in failed
        # Stressed DCF still computes (feeds ASD-25) but is informational by
        # default — not a hard gate (avoids double-counting the drawdown).
        assert dd is not None and dd > 0.20
        assert "stressed_dcf_downside" not in failed

    def test_stressed_dcf_can_be_re_enabled_as_gate(self) -> None:
        from liq.scan.resilience import FundamentalGateConfig

        cfg = FundamentalGateConfig(stressed_dcf_gates=True)
        _, failed, _, _, _ = evaluate_fundamental_gates(_fund(market_cap=6_000.0, fcf=60.0), cfg)
        assert "stressed_dcf_downside" in failed


class TestSectorAwareGates:
    def test_financial_unscored_on_fcf_and_leverage(self) -> None:
        # A bank-like name: FCF/EBITDA/leverage gates are not applicable.
        _, _, outcomes, _, _ = evaluate_fundamental_gates(
            _fund(sector="financial", fcf=-50.0, net_debt=None, interest_coverage=None)
        )
        ids = {o.gate_id: o.applicable for o in outcomes}
        for g in (
            "ttm_fcf_positive",
            "historical_fcf",
            "fcf_yield",
            "net_debt_to_ebitda",
            "interest_coverage",
        ):
            assert ids[g] is False  # UNSCORED, not failed

    def test_utility_keeps_leverage_drops_fcf(self) -> None:
        _, _, outcomes, _, _ = evaluate_fundamental_gates(
            _fund(sector="utility", fcf=-30.0, net_debt=200.0, ebitda=100.0, interest_coverage=6.0)
        )
        ids = {o.gate_id: o for o in outcomes}
        assert ids["ttm_fcf_positive"].applicable is False  # FCF dropped
        assert ids["net_debt_to_ebitda"].applicable is True  # leverage kept
        assert ids["interest_coverage"].applicable is True

    def test_operating_negative_fcf_still_fails(self) -> None:
        _, failed, _, _, _ = evaluate_fundamental_gates(_fund(sector="operating", fcf=-10.0))
        assert "ttm_fcf_positive" in failed

    def test_utility_leverage_uses_sector_bar(self) -> None:
        # 5× net-debt/EBITDA + 3× coverage: normal for a regulated utility →
        # passes under the utility bars, but fails under the operating bars.
        util = _fund(
            sector="utility", fcf=-30.0, net_debt=500.0, ebitda=100.0, interest_coverage=3.0
        )
        _, failed_u, _, _, _ = evaluate_fundamental_gates(util)
        assert "net_debt_to_ebitda" not in failed_u
        assert "interest_coverage" not in failed_u

        op = _fund(sector="operating", net_debt=500.0, ebitda=100.0, interest_coverage=3.0)
        _, failed_o, _, _, _ = evaluate_fundamental_gates(op)
        assert "net_debt_to_ebitda" in failed_o
        assert "interest_coverage" in failed_o


class TestAsd25Combination:
    def _stats(self, **o: object) -> ResilienceScanInput:
        base: dict[str, object] = {
            "symbol": "X",
            "n_obs": 800,
            "market_beta": 1.0,
            "downside_beta_qqq": 0.6,
            "es95": 0.03,
            "es95_ratio_qqq": 0.6,
            "max_drawdown": 0.2,
            "recovery_periods": 100,
            "residual_volatility": 0.01,
            "is_chip": False,
        }
        base.update(o)
        return ResilienceScanInput(**base)  # type: ignore[arg-type]

    def test_asd25_is_worse_of_tape_and_fundamental(self) -> None:
        stats = self._stats(downside_beta_qqq=0.6, es95=0.0)  # tape dd = 0.15
        result = build_result(
            symbol="X",
            as_of=AS_OF,
            stats=stats,
            gate_pass=False,
            failed_gates=("fcf_yield",),
            fundamental_drawdown=0.55,
            implied_growth=0.22,
        )
        assert result.tape_drawdown == pytest.approx(0.15)
        assert result.asd25 == pytest.approx(0.55)  # fundamental dominates
        assert result.classification == "highly_exposed"

    def test_asd25_tape_only_when_no_fundamental(self) -> None:
        stats = self._stats(downside_beta_qqq=0.6, es95=0.0)
        result = build_result(symbol="X", as_of=AS_OF, stats=stats, gate_pass=True, failed_gates=())
        assert result.asd25 == pytest.approx(result.tape_drawdown)
        assert result.fundamental_drawdown is None
