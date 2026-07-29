"""Financial-survival + valuation hard gates (Appendix A.2/A.3).

Pure predicates over a :class:`FundamentalScanInput`. A check whose input is
absent is reported ``applicable=False`` (``UNSCORED``) rather than failed —
missing data never fabricates a pass or a fail (the exclusion report carries
the gaps). Some spec checks (near-term maturities, EV/Sales ceiling, multiple
premium to historical median) need data not in free XBRL and are deferred.
"""

from __future__ import annotations

from liq.scan.resilience.gates import GateOutcome
from liq.scan.resilience.models import DcfConfig, FundamentalGateConfig, FundamentalScanInput
from liq.scan.resilience.valuation import fundamental_drawdown


def _na(gate_id: str) -> GateOutcome:
    return GateOutcome(gate_id, passed=True, applicable=False)


def _le(gate_id: str, value: float | None, threshold: float) -> GateOutcome:
    if value is None:
        return _na(gate_id)
    return GateOutcome(gate_id, value <= threshold)


def _ge(gate_id: str, value: float | None, threshold: float) -> GateOutcome:
    if value is None:
        return _na(gate_id)
    return GateOutcome(gate_id, value >= threshold)


def evaluate_fundamental_gates(
    inp: FundamentalScanInput,
    cfg: FundamentalGateConfig | None = None,
    dcf_cfg: DcfConfig | None = None,
) -> tuple[bool, tuple[str, ...], tuple[GateOutcome, ...], float | None, float | None]:
    """Evaluate all fundamental gates for one symbol.

    Returns ``(passed_all, failed_ids, outcomes, fundamental_drawdown,
    implied_growth)``. Only applicable, failing gates count toward
    ``failed_ids``.
    """
    cfg = cfg or FundamentalGateConfig()
    dcf_cfg = dcf_cfg or DcfConfig()
    profile = cfg.profiles[inp.business_type]
    drawdown, igb = fundamental_drawdown(inp, profile, dcf_cfg)

    # Sector-aware applicability: free-cash-flow gates are meaningful only for
    # operating companies; leverage/coverage additionally for utilities (real
    # regulated debt). Financials and REITs have no comparable FCF/EBITDA, so
    # those gates are UNSCORED for them (not failed) — the tape, valuation, and
    # dilution gates still apply.
    fcf_applies = inp.sector == "operating"
    leverage_applies = inp.sector in ("operating", "utility")

    outcomes: list[GateOutcome] = []

    # -- financial survival (A.2) --
    if fcf_applies and inp.fcf is not None:
        outcomes.append(GateOutcome("ttm_fcf_positive", inp.fcf > 0.0))
    else:
        outcomes.append(_na("ttm_fcf_positive"))
    if fcf_applies and inp.fcf_considered_years >= cfg.historical_fcf_years:
        outcomes.append(
            GateOutcome("historical_fcf", inp.fcf_positive_years >= cfg.historical_fcf_positive_min)
        )
    else:
        outcomes.append(_na("historical_fcf"))
    if leverage_applies:
        if inp.sector == "utility":
            max_leverage = cfg.utility_max_net_debt_to_ebitda
            min_coverage = cfg.utility_min_interest_coverage
        else:
            max_leverage = profile.max_net_debt_to_ebitda
            min_coverage = cfg.min_interest_coverage
        outcomes.append(_le("net_debt_to_ebitda", inp.net_debt_to_ebitda, max_leverage))
        outcomes.append(_ge("interest_coverage", inp.interest_coverage, min_coverage))
    else:
        outcomes.append(_na("net_debt_to_ebitda"))
        outcomes.append(_na("interest_coverage"))
    # Cash runway applies only to currently-unprofitable names.
    if inp.is_profitable:
        outcomes.append(_na("cash_runway"))
    else:
        outcomes.append(_ge("cash_runway", inp.cash_runway_years, cfg.min_cash_runway_years))
    outcomes.append(_le("share_count_growth", inp.diluted_share_growth, cfg.max_share_count_growth))
    outcomes.append(_le("sbc_to_fcf", inp.sbc_to_fcf, cfg.max_sbc_to_fcf))

    # -- valuation (A.3) --
    # IGB and FCF-yield auto-UNSCORE for non-FCF sectors (their inputs are None).
    outcomes.append(_le("implied_growth_igb10", igb, cfg.max_implied_growth))
    outcomes.append(
        _ge("fcf_yield", inp.fcf_yield, profile.min_fcf_yield) if fcf_applies else _na("fcf_yield")
    )
    # Stressed DCF is informational unless explicitly enabled (it already feeds
    # ASD-25); demoting it avoids double-counting the fundamental drawdown.
    if cfg.stressed_dcf_gates:
        outcomes.append(_le("stressed_dcf_downside", drawdown, cfg.max_stressed_dcf_downside))
    else:
        outcomes.append(_na("stressed_dcf_downside"))

    # -- AI dependency (A.5) --
    outcomes.extend(_ai_dependency_gates(inp, cfg))

    failed = tuple(o.gate_id for o in outcomes if o.applicable and not o.passed)
    return len(failed) == 0, failed, tuple(outcomes), drawdown, igb


def _ai_dependency_gates(
    inp: FundamentalScanInput,
    cfg: FundamentalGateConfig,
) -> list[GateOutcome]:
    """AI-dependency gates (A.5). All ``UNSCORED`` when no overlay is attached."""
    ai = inp.ai_dependency
    acfg = cfg.ai_dependency
    if ai is None:
        return [_na(g) for g in _AI_GATE_IDS]
    outcomes = [
        _le("ai_infra_revenue", ai.ai_infra_revenue_share, acfg.max_ai_infra_revenue_share),
        _le(
            "hyperscaler_capex_dependence",
            ai.hyperscaler_capex_dependence,
            acfg.max_hyperscaler_capex_dependence,
        ),
        _le("largest_customer", ai.largest_customer_share, acfg.max_largest_customer_share),
        _le("top5_customers", ai.top5_customer_share, acfg.max_top5_customer_share),
        _le(
            "single_product_concentration",
            ai.single_product_gross_profit_share,
            acfg.max_single_product_gross_profit_share,
        ),
        _le(
            "inventory_vs_sales",
            ai.inventory_growth_vs_sales,
            acfg.max_inventory_growth_vs_sales,
        ),
    ]
    if ai.backlog_cancellation_risk is None:
        outcomes.append(_na("backlog_cancellation"))
    else:
        outcomes.append(GateOutcome("backlog_cancellation", ai.backlog_cancellation_risk != "high"))
    return outcomes


_AI_GATE_IDS = (
    "ai_infra_revenue",
    "hyperscaler_capex_dependence",
    "largest_customer",
    "top5_customers",
    "single_product_concentration",
    "inventory_vs_sales",
    "backlog_cancellation",
)


__all__ = ["evaluate_fundamental_gates"]
