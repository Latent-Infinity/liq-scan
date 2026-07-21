"""Tests for the pure tape-resilience screen (contract + gates + scoring)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from liq.scan.resilience import (
    MarketBehaviorGateConfig,
    PriceShockScoreConfig,
    ResilienceResult,
    ResilienceScanInput,
    ShockScenarioConfig,
    build_result,
    classify,
    evaluate_market_behavior_gates,
    price_shock_score,
    rank_results,
    tape_drawdown,
)

AS_OF = datetime(2026, 6, 30, 20, 0, tzinfo=UTC)


def _row(**overrides: object) -> ResilienceScanInput:
    base: dict[str, object] = {
        "symbol": "TEST",
        "n_obs": 1000,
        "market_beta": 1.0,
        "downside_beta_qqq": 0.7,
        "downside_beta_soxx": None,
        "es95": 0.04,
        "es95_ratio_qqq": 0.7,
        "max_drawdown": 0.25,
        "recovery_periods": 100,
        "residual_volatility": 0.01,
        "is_chip": False,
    }
    base.update(overrides)
    return ResilienceScanInput(**base)  # type: ignore[arg-type]


class TestMarketBehaviorGates:
    def test_all_pass(self) -> None:
        passed, failed, outcomes = evaluate_market_behavior_gates(_row())
        assert passed is True
        assert failed == ()
        assert len(outcomes) == 6

    def test_market_beta_fail(self) -> None:
        passed, failed, _ = evaluate_market_behavior_gates(_row(market_beta=1.30))
        assert passed is False
        assert "market_beta_5y" in failed

    def test_downside_beta_qqq_fail(self) -> None:
        _, failed, _ = evaluate_market_behavior_gates(_row(downside_beta_qqq=0.95))
        assert "downside_beta_qqq" in failed

    def test_es_ratio_and_drawdown_fail(self) -> None:
        _, failed, _ = evaluate_market_behavior_gates(_row(es95_ratio_qqq=0.9, max_drawdown=0.5))
        assert "es95_ratio_qqq" in failed
        assert "max_drawdown_3y" in failed

    def test_recovery_never_recovered_fails(self) -> None:
        _, failed, _ = evaluate_market_behavior_gates(_row(recovery_periods=None))
        assert "recovery_duration" in failed

    def test_recovery_too_slow_fails(self) -> None:
        _, failed, _ = evaluate_market_behavior_gates(_row(recovery_periods=300))
        assert "recovery_duration" in failed

    def test_soxx_gate_not_applicable_for_non_chip(self) -> None:
        _, failed, outcomes = evaluate_market_behavior_gates(
            _row(is_chip=False, downside_beta_soxx=None)
        )
        soxx = next(o for o in outcomes if o.gate_id == "downside_beta_soxx")
        assert soxx.applicable is False
        assert "downside_beta_soxx" not in failed

    def test_soxx_gate_fails_for_chip_without_series(self) -> None:
        _, failed, _ = evaluate_market_behavior_gates(_row(is_chip=True, downside_beta_soxx=None))
        assert "downside_beta_soxx" in failed

    def test_soxx_gate_pass_and_fail_for_chip(self) -> None:
        _, failed_ok, _ = evaluate_market_behavior_gates(
            _row(is_chip=True, downside_beta_soxx=0.80)
        )
        assert "downside_beta_soxx" not in failed_ok
        _, failed_bad, _ = evaluate_market_behavior_gates(
            _row(is_chip=True, downside_beta_soxx=0.95)
        )
        assert "downside_beta_soxx" in failed_bad

    def test_custom_config_threshold(self) -> None:
        cfg = MarketBehaviorGateConfig(max_market_beta=0.9)
        _, failed, _ = evaluate_market_behavior_gates(_row(market_beta=1.0), cfg)
        assert "market_beta_5y" in failed


class TestTapeDrawdown:
    def test_qqq_scaled_dominates(self) -> None:
        row = _row(downside_beta_qqq=0.8, es95=0.05)
        assert tape_drawdown(row) == pytest.approx(0.8 * 0.25)

    def test_es_floor_dominates(self) -> None:
        row = _row(downside_beta_qqq=0.2, es95=0.12)
        assert tape_drawdown(row) == pytest.approx(0.12)

    def test_chip_soxx_term_included(self) -> None:
        row = _row(is_chip=True, downside_beta_qqq=0.5, downside_beta_soxx=0.9, es95=0.04)
        # max(0.5*0.25=0.125, 0.04, 0.9*0.35=0.315) = 0.315
        assert tape_drawdown(row) == pytest.approx(0.315)

    def test_non_chip_ignores_soxx(self) -> None:
        row = _row(is_chip=False, downside_beta_qqq=0.5, downside_beta_soxx=0.9, es95=0.04)
        assert tape_drawdown(row) == pytest.approx(0.125)

    def test_clamped_to_one(self) -> None:
        row = _row(downside_beta_qqq=8.0, es95=0.04)
        assert tape_drawdown(row) == 1.0

    def test_custom_shock(self) -> None:
        row = _row(downside_beta_qqq=0.8, es95=0.0)
        assert tape_drawdown(row, ShockScenarioConfig(qqq_shock=0.5)) == pytest.approx(0.4)


class TestPriceShockScore:
    def test_perfect_non_chip_scores_full(self) -> None:
        row = _row(
            is_chip=False,
            downside_beta_qqq=0.5,
            es95_ratio_qqq=0.5,
            max_drawdown=0.10,
            recovery_periods=21,
        )
        assert price_shock_score(row) == pytest.approx(25.0)

    def test_worst_scores_zero_except_na_soxx(self) -> None:
        row = _row(
            is_chip=False,
            downside_beta_qqq=0.9,
            es95_ratio_qqq=0.8,
            max_drawdown=0.35,
            recovery_periods=252,
        )
        # Non-chip still gets the 6 SOXX points as not-applicable.
        assert price_shock_score(row) == pytest.approx(6.0)

    def test_chip_missing_soxx_withholds_points(self) -> None:
        row = _row(
            is_chip=True,
            downside_beta_soxx=None,
            downside_beta_qqq=0.5,
            es95_ratio_qqq=0.5,
            max_drawdown=0.10,
            recovery_periods=21,
        )
        # 8 + 0(soxx withheld) + 4 + 4 + 3 = 19
        assert price_shock_score(row) == pytest.approx(19.0)

    def test_chip_with_soxx_scores(self) -> None:
        row = _row(
            is_chip=True,
            downside_beta_soxx=0.5,
            downside_beta_qqq=0.5,
            es95_ratio_qqq=0.5,
            max_drawdown=0.10,
            recovery_periods=21,
        )
        assert price_shock_score(row) == pytest.approx(25.0)

    def test_recovery_none_uses_worst(self) -> None:
        # A None recovery is scored as the worst band value (0 points), and
        # the full-pipeline score treats it that way via price_shock_score.
        cfg = PriceShockScoreConfig()
        assert cfg.recovery_periods.score(cfg.recovery_periods.worst) == 0.0
        assert price_shock_score(_row(recovery_periods=None)) < price_shock_score(
            _row(recovery_periods=21)
        )

    def test_midpoint_band(self) -> None:
        cfg = PriceShockScoreConfig()
        # downside_beta_qqq band best=0.5 worst=0.9 points=8 -> midpoint 0.7 => 4
        assert cfg.downside_beta_qqq.score(0.7) == pytest.approx(4.0)

    def test_total_points_is_25(self) -> None:
        assert PriceShockScoreConfig().total_points == pytest.approx(25.0)


class TestClassify:
    @pytest.mark.parametrize(
        ("asd25", "expected"),
        [
            (0.10, "price_fortress"),
            (0.15, "price_fortress"),
            (0.20, "resilient"),
            (0.35, "survivable_volatile"),
            (0.50, "highly_exposed"),
            (0.75, "speculative"),
        ],
    )
    def test_bands(self, asd25: float, expected: str) -> None:
        assert classify(asd25) == expected


class TestRankAndBuild:
    def test_build_result_shape(self) -> None:
        row = _row(downside_beta_qqq=0.6, es95=0.03)
        result = build_result(
            symbol="MSFT",
            as_of=AS_OF,
            stats=row,
            gate_pass=True,
            failed_gates=(),
        )
        assert isinstance(result, ResilienceResult)
        assert result.symbol == "MSFT"
        assert result.asd25_tape == pytest.approx(0.15)
        assert result.classification == "price_fortress"
        assert result.gate_pass is True
        assert result.price_shock_max == pytest.approx(25.0)

    def test_rank_orders_by_asd25_then_score(self) -> None:
        a = build_result(
            symbol="A",
            as_of=AS_OF,
            stats=_row(downside_beta_qqq=0.8, es95=0.0),
            gate_pass=True,
            failed_gates=(),
        )  # asd25 = 0.20
        b = build_result(
            symbol="B",
            as_of=AS_OF,
            stats=_row(downside_beta_qqq=0.4, es95=0.0),
            gate_pass=True,
            failed_gates=(),
        )  # asd25 = 0.10
        ranked = rank_results([a, b])
        assert [r.symbol for r in ranked] == ["B", "A"]

    def test_rank_tiebreak_by_score(self) -> None:
        # Same asd25 (via es floor), different scorecards.
        strong = build_result(
            symbol="STRONG",
            as_of=AS_OF,
            stats=_row(es95=0.20, downside_beta_qqq=0.5, max_drawdown=0.10, recovery_periods=21),
            gate_pass=True,
            failed_gates=(),
        )
        weak = build_result(
            symbol="WEAK",
            as_of=AS_OF,
            # db_qqq=0.8 -> qqq term 0.20 == es floor, so asd25 matches STRONG,
            # but the scorecard is worse across the board.
            stats=_row(es95=0.20, downside_beta_qqq=0.8, max_drawdown=0.35, recovery_periods=252),
            gate_pass=True,
            failed_gates=(),
        )
        assert strong.asd25_tape == pytest.approx(weak.asd25_tape)
        ranked = rank_results([weak, strong])
        assert ranked[0].symbol == "STRONG"
