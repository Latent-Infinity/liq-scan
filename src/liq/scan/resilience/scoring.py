"""Pure tape-drawdown, price-shock scoring, classification, and ranking.

Everything here is a pure function of a :class:`ResilienceScanInput` (the
statistics contract) and config — no bars, no vendor calls, no statistics
implementation (those live in ``liq-validation`` and are invoked by the
pilot harness that builds the inputs). This keeps ``liq-scan`` within its
declared dependency boundary (``liq-core``/``liq-store``/``liq-data``).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime

from liq.scan.resilience.models import (
    Classification,
    FundamentalScanInput,
    PriceShockScoreConfig,
    ResilienceResult,
    ResilienceScanInput,
    ShockScenarioConfig,
)


def tape_drawdown(
    row: ResilienceScanInput,
    shock: ShockScenarioConfig | None = None,
) -> float:
    """Top-down historical-behavior drawdown estimate.

    ``max`` of the downside-beta-scaled benchmark shocks and the expected
    shortfall floor — deliberately conservative. The SOXX term is included
    only for chip names with a real SOXX downside beta. Clamped to [0, 1].
    """
    shock = shock or ShockScenarioConfig()
    candidates = [
        row.downside_beta_qqq * shock.qqq_shock,
        row.es95,
    ]
    if row.is_chip and row.downside_beta_soxx is not None:
        candidates.append(row.downside_beta_soxx * shock.soxx_shock)
    return min(1.0, max(0.0, max(candidates)))


def price_shock_score(
    row: ResilienceScanInput,
    cfg: PriceShockScoreConfig | None = None,
) -> float:
    """25-point price-shock scorecard component (Appendix A.6).

    For a chip name lacking a SOXX downside beta, the SOXX sub-score is
    withheld (0) rather than fabricated; the missing points simply do not
    accrue. Non-chip names receive full SOXX points as not-applicable
    (they carry no semiconductor-benchmark risk to penalize).
    """
    cfg = cfg or PriceShockScoreConfig()
    score = cfg.downside_beta_qqq.score(row.downside_beta_qqq)
    if not row.is_chip:
        score += cfg.downside_beta_soxx.points
    elif row.downside_beta_soxx is not None:
        score += cfg.downside_beta_soxx.score(row.downside_beta_soxx)
    score += cfg.es95_ratio_qqq.score(row.es95_ratio_qqq)
    score += cfg.max_drawdown.score(row.max_drawdown)
    recovery = (
        float(row.recovery_periods)
        if row.recovery_periods is not None
        else cfg.recovery_periods.worst
    )
    score += cfg.recovery_periods.score(recovery)
    return score


def classify(asd25: float) -> Classification:
    """Map an ASD-25 drawdown fraction to its classification band (§1)."""
    if asd25 <= 0.15:
        return "price_fortress"
    if asd25 <= 0.25:
        return "resilient"
    if asd25 <= 0.40:
        return "survivable_volatile"
    if asd25 <= 0.60:
        return "highly_exposed"
    return "speculative"


def rank_results(results: Iterable[ResilienceResult]) -> list[ResilienceResult]:
    """Order results by ASD-25 ascending, resilience score descending.

    Lower estimated drawdown ranks first; the 0–100 resilience score (or the
    price-shock score when the scorecard is absent) breaks ties as the
    confidence signal (§1 ranking tuple).
    """
    return sorted(
        results,
        key=lambda r: (
            r.asd25,
            -(r.resilience_score if r.resilience_score is not None else r.price_shock_score),
        ),
    )


def build_result(
    *,
    symbol: str,
    as_of: datetime,
    stats: ResilienceScanInput,
    gate_pass: bool,
    failed_gates: Sequence[str],
    shock: ShockScenarioConfig | None = None,
    score_cfg: PriceShockScoreConfig | None = None,
    fundamental_drawdown: float | None = None,
    implied_growth: float | None = None,
    fundamentals: FundamentalScanInput | None = None,
    resilience_score: float | None = None,
    score_earned: float | None = None,
    score_available: float | None = None,
) -> ResilienceResult:
    """Assemble a :class:`ResilienceResult`.

    ``asd25 = max(fundamental_drawdown, tape_drawdown)`` when a fundamental
    drawdown is supplied (the deliberately conservative worse-of estimate),
    else the tape estimate alone. Classification bands off the full ASD-25.
    The renormalized 0–100 ``resilience_score`` is computed by the caller
    (``resilience_scorecard``) and passed in to avoid an import cycle.
    """
    score_cfg = score_cfg or PriceShockScoreConfig()
    td = tape_drawdown(stats, shock)
    asd25 = td if fundamental_drawdown is None else max(td, fundamental_drawdown)
    return ResilienceResult(
        symbol=symbol,
        as_of=as_of,
        asd25=asd25,
        asd25_tape=td,
        tape_drawdown=td,
        fundamental_drawdown=fundamental_drawdown,
        implied_growth=implied_growth,
        classification=classify(asd25),
        price_shock_score=price_shock_score(stats, score_cfg),
        price_shock_max=score_cfg.total_points,
        resilience_score=resilience_score,
        score_earned=score_earned,
        score_available=score_available,
        gate_pass=gate_pass,
        failed_gates=tuple(failed_gates),
        stats=stats,
        fundamentals=fundamentals,
    )


__all__ = [
    "build_result",
    "classify",
    "price_shock_score",
    "rank_results",
    "tape_drawdown",
]
