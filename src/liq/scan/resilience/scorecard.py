"""0–100 resilience scorecard (Appendix A.6), with transparent renormalization.

Each component earns points from the metrics that are actually present; a
metric that is unavailable (no real data) contributes **no** available points
rather than a zero — so a name is never penalized for a gap it can't fill
(Requirement F8). The final score renormalizes to the points that were
scorable: ``score = 100 × earned / available``.

Components carried here are the ones with real data today: price-shock (25),
valuation cushion (20 of 25), balance-sheet & FCF strength (15 of 20). The
AI-dependency component (20) and the historical-multiple / consensus-gap /
business-quality sub-items are ``available=0`` until their data lands (S5+).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from liq.scan.resilience.models import (
    FundamentalScanInput,
    PriceShockScoreConfig,
    ResilienceScanInput,
    _MetricBand,
)
from liq.scan.resilience.scoring import price_shock_score


class ScoreComponent(BaseModel):
    """One scorecard component's earned and available points."""

    model_config = ConfigDict(frozen=True)

    name: str
    earned: float
    available: float


class ResilienceScore(BaseModel):
    """The full scorecard: components plus a renormalized 0–100 score."""

    model_config = ConfigDict(frozen=True)

    components: tuple[ScoreComponent, ...]
    earned: float
    available: float

    @property
    def score_100(self) -> float | None:
        """Renormalized 0–100 score, or ``None`` when nothing was scorable."""
        if self.available <= 0.0:
            return None
        return 100.0 * self.earned / self.available


class ScorecardConfig(BaseModel):
    """Calibration bands for the scorecard (initial spec; tunable/ablatable)."""

    model_config = ConfigDict(frozen=True)

    # Valuation cushion (of 25): fundamental DD 10, IGB-10 6, FCF yield 4.
    fundamental_drawdown: _MetricBand = _MetricBand(best=0.15, worst=0.60, points=10.0)
    implied_growth: _MetricBand = _MetricBand(best=0.0, worst=0.20, points=6.0)
    fcf_yield: _MetricBand = _MetricBand(best=0.06, worst=0.02, points=4.0)  # higher better
    # Balance-sheet & FCF strength (of 20): net leverage 5, coverage 4,
    # FCF consistency 4, dilution 2.
    net_debt_to_ebitda: _MetricBand = _MetricBand(best=-1.0, worst=2.0, points=5.0)
    interest_coverage: _MetricBand = _MetricBand(best=40.0, worst=10.0, points=4.0)  # higher better
    fcf_consistency: _MetricBand = _MetricBand(best=1.0, worst=0.6, points=4.0)  # higher better
    dilution: _MetricBand = _MetricBand(best=-0.02, worst=0.02, points=2.0)
    # AI dependency (of 20): look-through capex 7, customer 4, product 3,
    # inventory/backlog 3, non-AI diversification 3. Mostly overlay-sourced;
    # unavailable metrics simply do not accrue available points.
    ai_hyperscaler_capex: _MetricBand = _MetricBand(best=0.10, worst=0.30, points=7.0)
    ai_customer_concentration: _MetricBand = _MetricBand(best=0.20, worst=0.40, points=4.0)
    ai_product_concentration: _MetricBand = _MetricBand(best=0.20, worst=0.40, points=3.0)
    ai_inventory_backlog: _MetricBand = _MetricBand(best=-0.10, worst=0.10, points=3.0)
    ai_non_ai_diversification: _MetricBand = _MetricBand(best=0.75, worst=0.25, points=3.0)
    price_shock: PriceShockScoreConfig = Field(default_factory=PriceShockScoreConfig)


def _band(component_metrics: list[tuple[_MetricBand, float | None]]) -> tuple[float, float]:
    """Sum (earned, available) over (band, value) pairs; skip absent values."""
    earned = 0.0
    available = 0.0
    for band, value in component_metrics:
        if value is None:
            continue
        available += band.points
        earned += band.score(value)
    return earned, available


def resilience_scorecard(
    stats: ResilienceScanInput,
    fundamentals: FundamentalScanInput | None,
    fundamental_drawdown: float | None,
    implied_growth: float | None,
    cfg: ScorecardConfig | None = None,
) -> ResilienceScore:
    """Compute the renormalized 0–100 scorecard for one symbol."""
    cfg = cfg or ScorecardConfig()

    price = price_shock_score(stats, cfg.price_shock)
    components = [ScoreComponent(name="price_shock", earned=price, available=25.0)]

    if fundamentals is not None:
        val_earned, val_avail = _band(
            [
                (cfg.fundamental_drawdown, fundamental_drawdown),
                (cfg.implied_growth, implied_growth),
                (cfg.fcf_yield, fundamentals.fcf_yield),
            ]
        )
        components.append(
            ScoreComponent(name="valuation_cushion", earned=val_earned, available=val_avail)
        )

        fcf_consistency = (
            fundamentals.fcf_positive_years / fundamentals.fcf_considered_years
            if fundamentals.fcf_considered_years > 0
            else None
        )
        bs_earned, bs_avail = _band(
            [
                (cfg.net_debt_to_ebitda, fundamentals.net_debt_to_ebitda),
                (cfg.interest_coverage, fundamentals.interest_coverage),
                (cfg.fcf_consistency, fcf_consistency),
                (cfg.dilution, fundamentals.diluted_share_growth),
            ]
        )
        components.append(
            ScoreComponent(name="balance_sheet", earned=bs_earned, available=bs_avail)
        )

        ai = fundamentals.ai_dependency
        if ai is not None:
            ai_earned, ai_avail = _band(
                [
                    (cfg.ai_hyperscaler_capex, ai.hyperscaler_capex_dependence),
                    (cfg.ai_customer_concentration, ai.top5_customer_share),
                    (cfg.ai_product_concentration, ai.single_product_gross_profit_share),
                    (cfg.ai_inventory_backlog, ai.inventory_growth_vs_sales),
                    (cfg.ai_non_ai_diversification, ai.non_ai_revenue_share),
                ]
            )
            components.append(
                ScoreComponent(name="ai_dependency", earned=ai_earned, available=ai_avail)
            )

    total_earned = sum(c.earned for c in components)
    total_available = sum(c.available for c in components)
    return ResilienceScore(
        components=tuple(components), earned=total_earned, available=total_available
    )


__all__ = ["ResilienceScore", "ScoreComponent", "ScorecardConfig", "resilience_scorecard"]
