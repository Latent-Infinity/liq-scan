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
    max_es95_ratio_qqq: float = 0.80
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


class ResilienceResult(BaseModel):
    """One ranked row of tape-resilience output.

    ``asd25_tape`` is the tape half of ASD-25 (a positive drawdown
    fraction); ``fundamental`` drawdown is folded in later. ``gate_pass``
    is ``False`` when any hard gate fails, with the failing gate ids in
    ``failed_gates``.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    as_of: datetime
    asd25_tape: float
    tape_drawdown: float
    classification: Classification
    price_shock_score: float
    price_shock_max: float
    gate_pass: bool
    failed_gates: tuple[str, ...] = ()
    stats: ResilienceScanInput


__all__ = [
    "Classification",
    "MarketBehaviorGateConfig",
    "PriceShockScoreConfig",
    "ResilienceResult",
    "ResilienceScanInput",
    "ShockScenarioConfig",
]
