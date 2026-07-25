"""Tests for the AI-dependency gates and 20-point scorecard component."""

from __future__ import annotations

import pytest

from liq.scan.resilience import (
    AiDependencyInput,
    FundamentalScanInput,
    ResilienceScanInput,
    evaluate_fundamental_gates,
    resilience_scorecard,
)

_AI_GATE_IDS = frozenset(
    {
        "ai_infra_revenue",
        "hyperscaler_capex_dependence",
        "largest_customer",
        "top5_customers",
        "single_product_concentration",
        "inventory_vs_sales",
        "backlog_cancellation",
    }
)


def _fund(ai: AiDependencyInput | None, **o: object) -> FundamentalScanInput:
    base: dict[str, object] = {
        "symbol": "X",
        "business_type": "diversified_semi",
        "market_cap": 1000.0,
        "fcf": 60.0,
        "ebitda": 100.0,
        "net_debt": -50.0,
        "interest_coverage": 40.0,
        "diluted_share_growth": 0.0,
        "sbc_to_fcf": 0.1,
        "fcf_positive_years": 5,
        "fcf_considered_years": 5,
        "ai_dependency": ai,
    }
    base.update(o)
    return FundamentalScanInput(**base)  # type: ignore[arg-type]


def _outcomes(fund: FundamentalScanInput) -> dict[str, tuple[bool, bool]]:
    _, _, outcomes, _, _ = evaluate_fundamental_gates(fund)
    return {o.gate_id: (o.passed, o.applicable) for o in outcomes}


class TestAiDependencyGates:
    def test_all_unscored_without_overlay(self) -> None:
        ids = _outcomes(_fund(None))
        for gid in _AI_GATE_IDS:
            assert ids[gid] == (True, False)  # not applicable

    def test_concentration_and_capex_fail(self) -> None:
        ai = AiDependencyInput(
            hyperscaler_capex_dependence=0.60,
            largest_customer_share=0.25,
            top5_customer_share=0.70,
        )
        _, failed, _, _, _ = evaluate_fundamental_gates(_fund(ai))
        assert {"hyperscaler_capex_dependence", "largest_customer", "top5_customers"} <= set(failed)

    def test_inventory_buildup_fails(self) -> None:
        ai = AiDependencyInput(inventory_growth_vs_sales=0.47)  # NVDA-like buildup
        _, failed, _, _, _ = evaluate_fundamental_gates(_fund(ai))
        assert "inventory_vs_sales" in failed

    def test_inventory_in_line_passes(self) -> None:
        ai = AiDependencyInput(inventory_growth_vs_sales=-0.07)
        ids = _outcomes(_fund(ai))
        assert ids["inventory_vs_sales"] == (True, True)

    def test_backlog_high_fails_others_unscored(self) -> None:
        ai = AiDependencyInput(backlog_cancellation_risk="high")
        ids = _outcomes(_fund(ai))
        assert ids["backlog_cancellation"] == (False, True)
        assert ids["largest_customer"] == (True, False)  # unscored

    def test_backlog_modeled_passes(self) -> None:
        ai = AiDependencyInput(backlog_cancellation_risk="modeled")
        ids = _outcomes(_fund(ai))
        assert ids["backlog_cancellation"] == (True, True)


def _stats() -> ResilienceScanInput:
    return ResilienceScanInput(
        symbol="X",
        n_obs=800,
        market_beta=1.0,
        downside_beta_qqq=0.5,
        es95=0.03,
        es95_ratio_qqq=0.5,
        max_drawdown=0.10,
        recovery_periods=21,
        residual_volatility=0.01,
    )


class TestAiDependencyScorecard:
    def test_component_absent_without_overlay(self) -> None:
        score = resilience_scorecard(_stats(), _fund(None), 0.15, 0.0)
        assert "ai_dependency" not in [c.name for c in score.components]
        assert score.available == pytest.approx(60.0)

    def test_only_inventory_adds_three_available(self) -> None:
        ai = AiDependencyInput(inventory_growth_vs_sales=-0.10)  # best band
        score = resilience_scorecard(_stats(), _fund(ai), 0.15, 0.0)
        comp = next(c for c in score.components if c.name == "ai_dependency")
        assert comp.available == pytest.approx(3.0)  # only inventory/backlog band
        assert comp.earned == pytest.approx(3.0)
        assert score.available == pytest.approx(63.0)

    def test_full_overlay_adds_twenty_available(self) -> None:
        ai = AiDependencyInput(
            hyperscaler_capex_dependence=0.10,
            top5_customer_share=0.20,
            single_product_gross_profit_share=0.20,
            inventory_growth_vs_sales=-0.10,
            non_ai_revenue_share=0.75,
        )
        score = resilience_scorecard(_stats(), _fund(ai), 0.15, 0.0)
        comp = next(c for c in score.components if c.name == "ai_dependency")
        assert comp.available == pytest.approx(20.0)
        assert comp.earned == pytest.approx(20.0)  # all at best bands
        assert score.available == pytest.approx(80.0)
