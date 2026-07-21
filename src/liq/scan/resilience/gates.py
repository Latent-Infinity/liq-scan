"""Market-behavior hard gates (Appendix A.4).

Each gate is a pure function of a :class:`ResilienceScanInput` row and a
:class:`MarketBehaviorGateConfig`. A symbol passes the layer only if every
applicable gate passes. Gate ids are stable strings so a failing gate can
be reported and persisted.
"""

from __future__ import annotations

from dataclasses import dataclass

from liq.scan.resilience.models import MarketBehaviorGateConfig, ResilienceScanInput


@dataclass(frozen=True)
class GateOutcome:
    """Result of one gate: its id, whether it passed, and whether it applied."""

    gate_id: str
    passed: bool
    applicable: bool = True


def _market_beta(row: ResilienceScanInput, cfg: MarketBehaviorGateConfig) -> GateOutcome:
    return GateOutcome("market_beta_5y", row.market_beta <= cfg.max_market_beta)


def _downside_beta_qqq(row: ResilienceScanInput, cfg: MarketBehaviorGateConfig) -> GateOutcome:
    return GateOutcome("downside_beta_qqq", row.downside_beta_qqq <= cfg.max_downside_beta_qqq)


def _downside_beta_soxx(row: ResilienceScanInput, cfg: MarketBehaviorGateConfig) -> GateOutcome:
    # Applies only to chip names. A chip name with no SOXX series cannot be
    # verified -> fail (conservative). Non-chip names are not-applicable.
    if not row.is_chip:
        return GateOutcome("downside_beta_soxx", passed=True, applicable=False)
    if row.downside_beta_soxx is None:
        return GateOutcome("downside_beta_soxx", passed=False)
    return GateOutcome("downside_beta_soxx", row.downside_beta_soxx <= cfg.max_downside_beta_soxx)


def _es95_ratio_qqq(row: ResilienceScanInput, cfg: MarketBehaviorGateConfig) -> GateOutcome:
    return GateOutcome("es95_ratio_qqq", row.es95_ratio_qqq <= cfg.max_es95_ratio_qqq)


def _max_drawdown(row: ResilienceScanInput, cfg: MarketBehaviorGateConfig) -> GateOutcome:
    return GateOutcome("max_drawdown_3y", row.max_drawdown <= cfg.max_drawdown)


def _recovery(row: ResilienceScanInput, cfg: MarketBehaviorGateConfig) -> GateOutcome:
    # Never recovering within the window (None) fails the recovery gate.
    if row.recovery_periods is None:
        return GateOutcome("recovery_duration", passed=False)
    return GateOutcome("recovery_duration", row.recovery_periods <= cfg.max_recovery_periods)


_GATES = (
    _market_beta,
    _downside_beta_qqq,
    _downside_beta_soxx,
    _es95_ratio_qqq,
    _max_drawdown,
    _recovery,
)


def evaluate_market_behavior_gates(
    row: ResilienceScanInput,
    cfg: MarketBehaviorGateConfig | None = None,
) -> tuple[bool, tuple[str, ...], tuple[GateOutcome, ...]]:
    """Evaluate all market-behavior gates for one symbol.

    Returns ``(passed_all, failed_gate_ids, outcomes)``. Only applicable,
    failing gates count toward ``failed_gate_ids``.
    """
    cfg = cfg or MarketBehaviorGateConfig()
    outcomes = tuple(gate(row, cfg) for gate in _GATES)
    failed = tuple(o.gate_id for o in outcomes if o.applicable and not o.passed)
    return (len(failed) == 0, failed, outcomes)


__all__ = ["GateOutcome", "evaluate_market_behavior_gates"]
