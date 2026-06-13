"""Targeted tests filling in the engine + CLI uncovered branches.

Coverage gaps surfaced by the default ``pytest --cov`` run; each test
here narrows a specific branch so the global gate stays at >=90 %.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from liq.scan.cli import _json_default, app
from liq.scan.engine import ScanEngine, _to_float
from liq.scan.predicates import AndPredicate, MovePredicate
from liq.scan.query import ScanQuery
from liq.scan.window import TradingMinutesWindow, parse_window_spec

_runner = CliRunner()


# ----- CLI ------------------------------------------------------------------


class TestCliBranches:
    def test_json_default_serializes_decimal(self) -> None:
        assert _json_default(Decimal("1.5")) == "1.5"

    def test_json_default_serializes_datetime(self) -> None:
        dt = datetime(2024, 6, 3, 13, 30, tzinfo=UTC)
        assert _json_default(dt).startswith("2024-06-03T13:30")

    def test_json_default_raises_on_unknown_type(self) -> None:
        with pytest.raises(TypeError):
            _json_default(object())

    def test_cli_rejects_bogus_direction(self, tmp_path: Path) -> None:
        result = _runner.invoke(
            app,
            [
                "execute",
                "--universe",
                "AAPL",
                "--as-of",
                "2024-06-03T14:00:00+00:00",
                "--window",
                "trading_minutes:30",
                "--direction",
                "sideways",
                "--data-root",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 1


# ----- engine -------------------------------------------------------------


class TestEngineBranches:
    def test_to_float_handles_none(self) -> None:
        assert _to_float(None) == 0.0

    def test_to_float_handles_decimal(self) -> None:
        assert _to_float(Decimal("3.5")) == 3.5

    def test_to_float_returns_zero_for_unknown_types(self) -> None:
        assert _to_float(object()) == 0.0

    def test_infer_k_drills_through_and_predicate(self) -> None:
        and_predicate = AndPredicate(
            predicates=[
                MovePredicate(threshold_pct=1.0, direction="either", k=7),
            ]
        )
        # Build a minimal-shape ScanQuery just to exercise _infer_k.
        query = ScanQuery(
            universe_ref=["AAPL"],
            as_of=datetime(2024, 6, 3, 20, 0, tzinfo=UTC),
            window=TradingMinutesWindow(kind="trading_minutes", n=30),
            predicate=and_predicate,
            ranking="abs_move",
            limit=None,
        )
        assert ScanEngine._infer_k(query) == 7

    def test_infer_k_defaults_to_5_for_non_move_predicate(self) -> None:
        from liq.scan.predicates import DollarVolumePredicate

        query = ScanQuery(
            universe_ref=["AAPL"],
            as_of=datetime(2024, 6, 3, 20, 0, tzinfo=UTC),
            window=TradingMinutesWindow(kind="trading_minutes", n=30),
            predicate=DollarVolumePredicate(min_usd=Decimal("1")),
            ranking="abs_move",
            limit=None,
        )
        assert ScanEngine._infer_k(query) == 5


# ----- window parsing -------------------------------------------------------


class TestParseWindowMalformed:
    def test_unknown_unit_in_duration_raises(self) -> None:
        with pytest.raises(ValueError, match="malformed calendar duration"):
            parse_window_spec("calendar:2x")
