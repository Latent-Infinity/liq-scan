"""AI-Shock Drawdown resilience screening (pure contract + gates + scoring).

Ranks a universe by ASD-25 = max(fundamental drawdown, tape drawdown). This
package is pure: it defines the input **contracts** (tape statistics and
fundamentals), the hard **gates** (market-behavior + financial-survival +
valuation), the deterministic **DCF/valuation** math, and the
**scoring/ranking** logic — all pure functions of already-computed inputs.

Boundary
--------
``liq-scan`` may not depend on ``liq-validation`` (statistics) or import
``liq-data`` (fundamentals). The pilot harness in ``liq-experiments`` computes
the tape statistics (via ``liq-validation``) and builds the fundamentals
contract (from ``liq-data``), then feeds both here.
"""

from liq.scan.resilience.fundamental_gates import evaluate_fundamental_gates
from liq.scan.resilience.gates import (
    GateOutcome,
    evaluate_market_behavior_gates,
)
from liq.scan.resilience.models import (
    BUSINESS_PROFILES,
    AiDependencyInput,
    BusinessProfile,
    BusinessType,
    Classification,
    DcfConfig,
    FundamentalGateConfig,
    FundamentalScanInput,
    MarketBehaviorGateConfig,
    PriceShockScoreConfig,
    ResilienceResult,
    ResilienceScanInput,
    Sector,
    ShockScenarioConfig,
)
from liq.scan.resilience.scorecard import (
    ResilienceScore,
    ScorecardConfig,
    ScoreComponent,
    resilience_scorecard,
)
from liq.scan.resilience.scoring import (
    build_result,
    classify,
    price_shock_score,
    rank_results,
    tape_drawdown,
)
from liq.scan.resilience.valuation import (
    dcf_equity_value,
    fundamental_drawdown,
    implied_growth,
)

__all__ = [
    "BUSINESS_PROFILES",
    "AiDependencyInput",
    "BusinessProfile",
    "BusinessType",
    "Classification",
    "DcfConfig",
    "FundamentalGateConfig",
    "FundamentalScanInput",
    "GateOutcome",
    "MarketBehaviorGateConfig",
    "PriceShockScoreConfig",
    "ResilienceResult",
    "ResilienceScanInput",
    "ResilienceScore",
    "ScoreComponent",
    "ScorecardConfig",
    "Sector",
    "ShockScenarioConfig",
    "build_result",
    "classify",
    "dcf_equity_value",
    "evaluate_fundamental_gates",
    "evaluate_market_behavior_gates",
    "fundamental_drawdown",
    "implied_growth",
    "price_shock_score",
    "rank_results",
    "resilience_scorecard",
    "tape_drawdown",
]
