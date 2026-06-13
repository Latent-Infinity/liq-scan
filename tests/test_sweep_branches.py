"""Targeted tests filling in sweep cadence + CLI uncovered branches."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from liq.scan.cli import app
from liq.scan.sweep import SweepConfig, as_of_timestamps, filter_remaining

_runner = CliRunner()


class TestMinutesNCadence:
    def test_minutes_n_walks_grid(self) -> None:
        config = SweepConfig(
            query_name="qn",
            cadence="minutes_n",
            start=date(2024, 6, 3),
            end=date(2024, 6, 4),
            interval_minutes=720,
        )
        grid = as_of_timestamps(config)
        # 24 hours / 12-hour interval → 3 timestamps (00:00 6/3, 12:00 6/3, 00:00 6/4)
        assert len(grid) == 3
        assert grid[0] == datetime(2024, 6, 3, tzinfo=UTC)

    def test_minutes_n_requires_interval(self) -> None:
        config = SweepConfig(
            query_name="qn",
            cadence="minutes_n",
            start=date(2024, 6, 3),
            end=date(2024, 6, 4),
        )
        with pytest.raises(ValueError, match="interval_minutes"):
            as_of_timestamps(config)

    def test_minutes_n_zero_interval_rejected(self) -> None:
        config = SweepConfig(
            query_name="qn",
            cadence="minutes_n",
            start=date(2024, 6, 3),
            end=date(2024, 6, 4),
            interval_minutes=0,
        )
        with pytest.raises(ValueError, match="interval_minutes"):
            as_of_timestamps(config)


class TestFilterRemaining:
    def test_drops_already_done(self) -> None:
        ts = [datetime(2024, 6, 3, tzinfo=UTC), datetime(2024, 6, 4, tzinfo=UTC)]
        remaining = filter_remaining(ts, [ts[0]])
        assert remaining == [ts[1]]


class TestCliBranches:
    def test_sweep_unknown_cadence_exits_one(self, tmp_path: Path) -> None:
        query_path = tmp_path / "q.yaml"
        query_path.write_text(
            """\
universe_ref: ["AAPL"]
window:
  kind: trading_minutes
  n: 30
predicate:
  kind: move
  threshold_pct: 5.0
  direction: either
""",
            encoding="utf-8",
        )
        result = _runner.invoke(
            app,
            [
                "sweep",
                "--query",
                str(query_path),
                "--start",
                "2024-06-03",
                "--end",
                "2024-06-04",
                "--cadence",
                "nope",
                "--data-root",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 1


class TestTemplateLoaderEdges:
    def test_iso_duration_accepts_minutes(self, tmp_path: Path) -> None:
        from liq.scan.template_loader import load_query_template

        path = tmp_path / "m.yaml"
        path.write_text(
            """\
universe_ref: ["AAPL"]
window:
  kind: calendar
  duration: PT30M
predicate:
  kind: move
  threshold_pct: 5.0
  direction: either
""",
            encoding="utf-8",
        )
        template = load_query_template(path)
        from datetime import timedelta

        assert template.window.duration == timedelta(minutes=30)  # type: ignore[union-attr]

    def test_template_loader_passes_through_timedelta_object(self) -> None:
        from datetime import timedelta

        from liq.scan.template_loader import _parse_duration

        assert _parse_duration(timedelta(hours=1)) == timedelta(hours=1)
        assert _parse_duration(60) == timedelta(seconds=60)

    def test_template_loader_rejects_bad_duration(self) -> None:
        from liq.scan.template_loader import _parse_duration

        with pytest.raises(ValueError, match="malformed ISO-8601 duration"):
            _parse_duration("not-iso")

    def test_template_loader_rejects_nonscalar_duration(self) -> None:
        from liq.scan.template_loader import _parse_duration

        with pytest.raises(ValueError, match="unsupported duration value"):
            _parse_duration({"x": 1})

    def test_price_predicate_round_trips(self, tmp_path: Path) -> None:
        from liq.scan.predicates import PricePredicate
        from liq.scan.template_loader import load_query_template

        path = tmp_path / "p.yaml"
        path.write_text(
            """\
universe_ref: ["AAPL"]
window:
  kind: trading_minutes
  n: 30
predicate:
  kind: price
  min_usd: "5"
""",
            encoding="utf-8",
        )
        template = load_query_template(path)
        assert isinstance(template.predicate, PricePredicate)
