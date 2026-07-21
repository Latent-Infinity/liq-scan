"""Tape-behavior resilience screening (pure contract + gates + scoring).

Ranks a universe by an estimated price drawdown under a standardized
market shock. This package is the *market-behavior* half of the AI-Shock
Drawdown screen; the number computed here is the **tape** drawdown
estimate (fundamental drawdown is folded in by a higher layer).

Boundary
--------
``liq-scan`` may not depend on ``liq-validation`` (the statistics layer).
So this package is pure: it defines the :class:`ResilienceScanInput`
statistics **contract**, the hard **gates**, and the **scoring/ranking**
logic — all pure functions of already-computed statistics. The pilot
harness that computes those statistics from real bars (using
``liq-validation``) lives in ``liq-experiments``.
"""

from liq.scan.resilience.gates import (
    GateOutcome,
    evaluate_market_behavior_gates,
)
from liq.scan.resilience.models import (
    Classification,
    MarketBehaviorGateConfig,
    PriceShockScoreConfig,
    ResilienceResult,
    ResilienceScanInput,
    ShockScenarioConfig,
)
from liq.scan.resilience.scoring import (
    build_result,
    classify,
    price_shock_score,
    rank_results,
    tape_drawdown,
)

__all__ = [
    "Classification",
    "GateOutcome",
    "MarketBehaviorGateConfig",
    "PriceShockScoreConfig",
    "ResilienceResult",
    "ResilienceScanInput",
    "ShockScenarioConfig",
    "build_result",
    "classify",
    "evaluate_market_behavior_gates",
    "price_shock_score",
    "rank_results",
    "tape_drawdown",
]
