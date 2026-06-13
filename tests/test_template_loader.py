"""Tests for the YAML ``ScanQueryTemplate`` loader.

A sweep is parameterized by a yaml file that captures the as-of-less
query shape. The loader needs to round-trip every supported
predicate + window kind without inventing extra ceremony.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from liq.scan.predicates import (
    AndPredicate,
    DollarVolumePredicate,
    MovePredicate,
)
from liq.scan.query import ScanQueryTemplate
from liq.scan.template_loader import load_query_template
from liq.scan.window import CalendarWindow, SessionsWindow, TradingMinutesWindow


class TestLoadQueryTemplate:
    def test_loads_simple_trading_minutes_template(self, tmp_path: Path) -> None:
        path = tmp_path / "sp500_5pct.yaml"
        path.write_text(
            """\
universe_ref: ["AAPL", "MSFT"]
window:
  kind: trading_minutes
  n: 390
predicate:
  kind: move
  threshold_pct: 5.0
  direction: either
ranking: abs_move
""",
            encoding="utf-8",
        )

        template = load_query_template(path)

        assert isinstance(template, ScanQueryTemplate)
        assert template.universe_ref == ["AAPL", "MSFT"]
        assert isinstance(template.window, TradingMinutesWindow)
        assert template.window.n == 390
        assert isinstance(template.predicate, MovePredicate)
        assert template.predicate.threshold_pct == 5.0

    def test_loads_calendar_window_with_iso_duration(self, tmp_path: Path) -> None:
        path = tmp_path / "calendar.yaml"
        path.write_text(
            """\
universe_ref: sp500
window:
  kind: calendar
  duration: PT2H
predicate:
  kind: move
  threshold_pct: 3.0
  direction: up
""",
            encoding="utf-8",
        )
        template = load_query_template(path)
        assert isinstance(template.window, CalendarWindow)
        assert template.window.duration == timedelta(hours=2)

    def test_loads_sessions_window(self, tmp_path: Path) -> None:
        path = tmp_path / "sessions.yaml"
        path.write_text(
            """\
universe_ref: ["AAPL"]
window:
  kind: sessions
  n: 3
predicate:
  kind: move
  threshold_pct: 2.0
  direction: down
""",
            encoding="utf-8",
        )
        template = load_query_template(path)
        assert isinstance(template.window, SessionsWindow)
        assert template.window.n == 3

    def test_loads_and_predicate(self, tmp_path: Path) -> None:
        path = tmp_path / "and.yaml"
        path.write_text(
            """\
universe_ref: ["AAPL"]
window:
  kind: trading_minutes
  n: 30
predicate:
  kind: and
  predicates:
    - kind: move
      threshold_pct: 5.0
      direction: either
    - kind: dollar_volume
      min_usd: "1000000"
""",
            encoding="utf-8",
        )
        template = load_query_template(path)
        assert isinstance(template.predicate, AndPredicate)
        assert len(template.predicate.predicates) == 2
        assert isinstance(template.predicate.predicates[0], MovePredicate)
        assert isinstance(template.predicate.predicates[1], DollarVolumePredicate)
        assert template.predicate.predicates[1].min_usd == Decimal("1000000")

    def test_rejects_unknown_kind(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text(
            """\
universe_ref: ["AAPL"]
window:
  kind: trading_minutes
  n: 30
predicate:
  kind: nope
""",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="unknown predicate kind"):
            load_query_template(path)

    def test_rejects_unknown_window_kind(self, tmp_path: Path) -> None:
        path = tmp_path / "badwin.yaml"
        path.write_text(
            """\
universe_ref: ["AAPL"]
window:
  kind: weeks
  n: 1
predicate:
  kind: move
  threshold_pct: 5.0
  direction: either
""",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="unknown window kind"):
            load_query_template(path)
