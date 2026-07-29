"""Domain models and configuration for the tape-resilience screen.

All thresholds and calibration bounds default to the analyst's initial
specification (Appendix A of the ASD-25 requirements doc). They are
overridable config, frozen at first backtest — never hardcoded in the
evaluation logic.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Classification = Literal[
    "price_fortress",
    "resilient",
    "survivable_volatile",
    "highly_exposed",
    "speculative",
]


class ResilienceScanInput(BaseModel):
    """Per-symbol tape statistics fed to the market-behavior gates and score.

    Every field is computed from real daily bars over an as-of-``T``
    lookback. ``downside_beta_soxx`` is ``None`` when no semiconductor
    benchmark series is available for the symbol's window; the SOXX gate
    treats a missing value for a chip name as a failure (cannot verify),
    and as not-applicable for a non-chip name.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    n_obs: int = Field(ge=0)
    market_beta: float
    downside_beta_qqq: float
    downside_beta_soxx: float | None = None
    es95: float
    es95_ratio_qqq: float
    max_drawdown: float
    recovery_periods: int | None = None
    residual_volatility: float
    is_chip: bool = False


class MarketBehaviorGateConfig(BaseModel):
    """Hard-gate thresholds for the market-behavior layer (Appendix A.4)."""

    model_config = ConfigDict(frozen=True)

    max_market_beta: float = 1.15
    max_downside_beta_qqq: float = 0.90
    max_downside_beta_soxx: float = 0.85
    # Recalibrated from 0.80 → 1.25: the original bar required a single stock to
    # be *less* tail-risky than 80% of the (diversified) QQQ index, which almost
    # no individual name is. 1.25 flags names materially more tail-risky than the
    # market while passing market-like ones. (Initial spec, frozen at backtest.)
    max_es95_ratio_qqq: float = 1.25
    max_drawdown: float = 0.35
    max_recovery_periods: int = 252  # ~12 months of trading days


class ShockScenarioConfig(BaseModel):
    """Standardized shock magnitudes for the tape drawdown (Appendix A.1)."""

    model_config = ConfigDict(frozen=True)

    qqq_shock: float = Field(default=0.25, ge=0.0, le=1.0)
    soxx_shock: float = Field(default=0.35, ge=0.0, le=1.0)


class _MetricBand(BaseModel):
    """Piecewise-linear score band: full ``points`` at ``best`` (or better),
    zero at ``worst`` (or worse), linear in between. ``lower_is_better``
    flips the direction for metrics like recovery duration where a smaller
    value earns more points as well."""

    model_config = ConfigDict(frozen=True)

    best: float
    worst: float
    points: float = Field(gt=0.0)

    def score(self, value: float) -> float:
        lo, hi = self.best, self.worst
        span = hi - lo
        if span == 0:  # pragma: no cover - guarded by config validation
            return self.points if value <= lo else 0.0
        frac = (hi - value) / span
        return self.points * min(1.0, max(0.0, frac))


class PriceShockScoreConfig(BaseModel):
    """Calibration for the 25-point price-shock scorecard component (A.6).

    Points: QQQ downside beta 8, SOXX downside beta 6, ES-95 4, max DD 4,
    recovery 3. Each band's (best, worst) bounds are the initial spec —
    full points at the "best" end, zero at the gate boundary — and are
    tunable/ablatable before freezing.
    """

    model_config = ConfigDict(frozen=True)

    downside_beta_qqq: _MetricBand = _MetricBand(best=0.5, worst=0.9, points=8.0)
    downside_beta_soxx: _MetricBand = _MetricBand(best=0.5, worst=0.85, points=6.0)
    es95_ratio_qqq: _MetricBand = _MetricBand(best=0.5, worst=0.8, points=4.0)
    max_drawdown: _MetricBand = _MetricBand(best=0.10, worst=0.35, points=4.0)
    recovery_periods: _MetricBand = _MetricBand(best=21.0, worst=252.0, points=3.0)

    @property
    def total_points(self) -> float:
        return (
            self.downside_beta_qqq.points
            + self.downside_beta_soxx.points
            + self.es95_ratio_qqq.points
            + self.max_drawdown.points
            + self.recovery_periods.points
        )


BusinessType = Literal[
    "ai_accelerator",
    "semi_equipment",
    "foundry_memory",
    "diversified_semi",
    "hyperscaler",
    "saas_software",
    "diversified_tech",
    "diversified_market",
]

# Coarse sector, used only to switch off gates whose metric is economically
# meaningless for that sector (FCF/EBITDA/leverage for financials, REITs, and
# capex-heavy utilities). Classified from SEC SIC codes.
Sector = Literal["operating", "financial", "reit", "utility"]


class BusinessProfile(BaseModel):
    """Per-business-type stress and threshold parameters.

    ``fcf_haircut``/``growth_haircut`` translate the Appendix A.1 revenue and
    margin shocks into a stressed FCF and a stressed growth used by the DCF.
    They are a modeling choice (initial spec, tunable/ablatable, frozen at
    first backtest). ``max_net_debt_to_ebitda`` and ``min_fcf_yield`` carry
    the business-model-specific gate thresholds (Appendix A.2/A.3).
    """

    model_config = ConfigDict(frozen=True)

    fcf_haircut: float = Field(ge=0.0, le=1.0)
    growth_haircut: float = Field(ge=0.0)
    max_net_debt_to_ebitda: float = Field(gt=0.0)
    min_fcf_yield: float = Field(ge=0.0)


# Defaults derived from Appendix A.1/A.2/A.3 (software & fabless leverage ≤1.5×,
# foundry/equipment ≤2.0×; software FCF-yield floor 3%, cyclical semi 4%).
BUSINESS_PROFILES: dict[str, BusinessProfile] = {
    "ai_accelerator": BusinessProfile(
        fcf_haircut=0.40, growth_haircut=0.10, max_net_debt_to_ebitda=1.5, min_fcf_yield=0.04
    ),
    "semi_equipment": BusinessProfile(
        fcf_haircut=0.35, growth_haircut=0.08, max_net_debt_to_ebitda=2.0, min_fcf_yield=0.04
    ),
    "foundry_memory": BusinessProfile(
        fcf_haircut=0.40, growth_haircut=0.08, max_net_debt_to_ebitda=2.0, min_fcf_yield=0.04
    ),
    "diversified_semi": BusinessProfile(
        fcf_haircut=0.25, growth_haircut=0.05, max_net_debt_to_ebitda=1.5, min_fcf_yield=0.04
    ),
    "hyperscaler": BusinessProfile(
        fcf_haircut=0.25, growth_haircut=0.06, max_net_debt_to_ebitda=1.5, min_fcf_yield=0.03
    ),
    "saas_software": BusinessProfile(
        fcf_haircut=0.30, growth_haircut=0.08, max_net_debt_to_ebitda=1.5, min_fcf_yield=0.03
    ),
    "diversified_tech": BusinessProfile(
        fcf_haircut=0.20, growth_haircut=0.03, max_net_debt_to_ebitda=1.5, min_fcf_yield=0.03
    ),
    # Generic, non-AI profile for a *general* market shock: only a mild uniform
    # earnings haircut (the WACC/terminal macro shocks drive most of the
    # valuation compression), so the fundamental drawdown is dominated by a
    # name's implied-growth richness rather than an AI-specific revenue collapse.
    # Defensives (low implied growth) barely compress; rich multiples compress.
    "diversified_market": BusinessProfile(
        fcf_haircut=0.10, growth_haircut=0.02, max_net_debt_to_ebitda=2.5, min_fcf_yield=0.03
    ),
}


class DcfConfig(BaseModel):
    """Base discounted-cash-flow assumptions (before stress)."""

    model_config = ConfigDict(frozen=True)

    wacc_base: float = Field(default=0.09, gt=0.0)
    terminal_growth: float = Field(default=0.025, ge=0.0)
    horizon_years: int = Field(default=10, gt=0)
    wacc_stress_add: float = Field(default=0.02, ge=0.0)  # +200 bps
    terminal_growth_stress_cut: float = Field(default=0.01, ge=0.0)  # −100 bps


BacklogRisk = Literal["low", "modeled", "high"]


class AiDependencyInput(BaseModel):
    """AI-dependency metrics (Appendix A.5), mostly 10-K-prose-sourced.

    Only ``inventory_growth_vs_sales`` is derivable from structured XBRL; the
    rest come from a real, provenance-tracked overlay and are ``None`` when not
    sourced (the gate and scorecard treat them as ``UNSCORED`` — never
    fabricated). Shares are fractions of the relevant base (0.25 == 25%).
    """

    model_config = ConfigDict(frozen=True)

    inventory_growth_vs_sales: float | None = None  # structured (XBRL)
    ai_infra_revenue_share: float | None = None
    hyperscaler_capex_dependence: float | None = None  # look-through
    largest_customer_share: float | None = None
    top5_customer_share: float | None = None
    single_product_gross_profit_share: float | None = None
    non_ai_revenue_share: float | None = None
    backlog_cancellation_risk: BacklogRisk | None = None


class AiDependencyGateConfig(BaseModel):
    """Hard/preferred-gate thresholds for the AI-dependency layer (A.5)."""

    model_config = ConfigDict(frozen=True)

    max_ai_infra_revenue_share: float = 0.25
    max_hyperscaler_capex_dependence: float = 0.30
    max_largest_customer_share: float = 0.15
    max_top5_customer_share: float = 0.40
    max_single_product_gross_profit_share: float = 0.40
    max_inventory_growth_vs_sales: float = 0.10  # "must not materially exceed sales"


class FundamentalGateConfig(BaseModel):
    """Hard-gate thresholds for the financial-survival + valuation layers (A.2/A.3)."""

    model_config = ConfigDict(frozen=True)

    min_interest_coverage: float = 10.0
    # Regulated utilities carry structurally high leverage (stable cash flows
    # support ~5–6× net-debt/EBITDA and ~2.5–4× coverage), so the operating
    # bars would fail every one. These sector-appropriate bars replace them.
    utility_max_net_debt_to_ebitda: float = 6.0
    utility_min_interest_coverage: float = 2.5
    max_share_count_growth: float = 0.02
    max_sbc_to_fcf: float = 0.30
    historical_fcf_positive_min: int = 4  # of last 5 years
    historical_fcf_years: int = 5
    max_implied_growth: float = 0.15  # IGB-10
    max_stressed_dcf_downside: float = 0.20
    # Stressed DCF is informational by default: it already *is* the fundamental
    # drawdown feeding ASD-25 (so gating on it double-counts), and under the
    # severe standardized shock it fails almost every valued name — a
    # non-discriminating gate. Set True to re-enable it as a hard gate.
    stressed_dcf_gates: bool = False
    min_cash_runway_years: float = 4.0  # unprofitable names
    ai_dependency: AiDependencyGateConfig = AiDependencyGateConfig()
    profiles: dict[str, BusinessProfile] = Field(default_factory=lambda: dict(BUSINESS_PROFILES))


class FundamentalScanInput(BaseModel):
    """Per-symbol fundamentals contract fed to the fundamental gates + DCF.

    Built by the harness from a real ``FundamentalsSnapshot`` (``liq-data``)
    plus a market cap (shares × price). Any field may be ``None`` when the
    filer did not tag it; the gate layer marks such checks ``UNSCORED`` rather
    than fabricate a value. ``liq-scan`` never imports ``liq-data`` — this is
    the boundary contract.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    business_type: BusinessType
    sector: Sector = "operating"
    market_cap: float = Field(gt=0.0)
    fcf: float | None = None
    ebitda: float | None = None
    net_debt: float | None = None
    interest_coverage: float | None = None
    diluted_share_growth: float | None = None
    sbc_to_fcf: float | None = None
    fcf_positive_years: int = 0
    fcf_considered_years: int = 0
    is_profitable: bool = True
    cash_runway_years: float | None = None
    ai_dependency: AiDependencyInput | None = None

    @property
    def fcf_yield(self) -> float | None:
        if self.fcf is None:
            return None
        return self.fcf / self.market_cap

    @property
    def net_debt_to_ebitda(self) -> float | None:
        if self.net_debt is None or self.ebitda is None or self.ebitda == 0.0:
            return None
        return self.net_debt / self.ebitda


class ResilienceResult(BaseModel):
    """One ranked row of resilience output.

    ``asd25`` is ``max(fundamental_drawdown, tape_drawdown)`` when a
    fundamental drawdown is available, else the tape estimate alone
    (``asd25_tape``). ``gate_pass`` is ``False`` when any applicable hard gate
    (tape and/or fundamental) fails, with failing gate ids in ``failed_gates``.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    as_of: datetime
    asd25: float
    asd25_tape: float
    tape_drawdown: float
    fundamental_drawdown: float | None = None
    implied_growth: float | None = None
    classification: Classification
    price_shock_score: float
    price_shock_max: float
    resilience_score: float | None = None  # renormalized 0–100 scorecard
    score_earned: float | None = None
    score_available: float | None = None
    gate_pass: bool
    failed_gates: tuple[str, ...] = ()
    stats: ResilienceScanInput
    fundamentals: FundamentalScanInput | None = None


__all__ = [
    "BUSINESS_PROFILES",
    "AiDependencyGateConfig",
    "AiDependencyInput",
    "BacklogRisk",
    "BusinessProfile",
    "BusinessType",
    "Classification",
    "DcfConfig",
    "FundamentalGateConfig",
    "FundamentalScanInput",
    "MarketBehaviorGateConfig",
    "PriceShockScoreConfig",
    "ResilienceResult",
    "ResilienceScanInput",
    "Sector",
    "ShockScenarioConfig",
]
