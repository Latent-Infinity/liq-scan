"""Tests for ``WindowSpec`` discriminated union.

Three kinds — trading minutes, calendar duration, sessions count.
Each resolves to a concrete ``(start, end)`` half-open window given
an as-of timestamp.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from liq.scan.window import (
    CalendarWindow,
    SessionsWindow,
    TradingMinutesWindow,
    WindowSpec,
    parse_window_spec,
)


class TestTradingMinutesWindow:
    def test_resolve_returns_session_boundary(self) -> None:
        w = TradingMinutesWindow(kind="trading_minutes", n=390)
        start, end = w.resolve(datetime(2024, 6, 3, 20, 0, tzinfo=UTC))
        assert start == datetime(2024, 6, 3, 13, 30, tzinfo=UTC)
        assert end == datetime(2024, 6, 3, 20, 0, tzinfo=UTC)

    def test_rejects_zero_or_negative_n(self) -> None:
        with pytest.raises(ValidationError):
            TradingMinutesWindow(kind="trading_minutes", n=0)


class TestCalendarWindow:
    def test_calendar_duration_subtracts_directly(self) -> None:
        w = CalendarWindow(kind="calendar", duration=timedelta(hours=2))
        end = datetime(2024, 6, 3, 20, 0, tzinfo=UTC)
        start, resolved_end = w.resolve(end)
        assert start == datetime(2024, 6, 3, 18, 0, tzinfo=UTC)
        assert resolved_end == end

    def test_rejects_non_positive_duration(self) -> None:
        with pytest.raises(ValidationError):
            CalendarWindow(kind="calendar", duration=timedelta(0))


class TestSessionsWindow:
    def test_three_sessions_walks_back(self) -> None:
        w = SessionsWindow(kind="sessions", n=3)
        start, end = w.resolve(datetime(2024, 6, 5, 20, 0, tzinfo=UTC))
        assert start == datetime(2024, 6, 3, 13, 30, tzinfo=UTC)

    def test_rejects_zero_n(self) -> None:
        with pytest.raises(ValidationError):
            SessionsWindow(kind="sessions", n=0)


class TestWindowSpecParse:
    """``parse_window_spec`` accepts the CLI-style ``"trading_minutes:390"``
    syntax and dispatches to the right subclass."""

    def test_trading_minutes_spec(self) -> None:
        spec = parse_window_spec("trading_minutes:390")
        assert isinstance(spec, TradingMinutesWindow)
        assert spec.n == 390

    def test_sessions_spec(self) -> None:
        spec = parse_window_spec("sessions:3")
        assert isinstance(spec, SessionsWindow)
        assert spec.n == 3

    def test_calendar_spec(self) -> None:
        spec = parse_window_spec("calendar:2h")
        assert isinstance(spec, CalendarWindow)
        assert spec.duration == timedelta(hours=2)

    def test_unknown_kind_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown window kind"):
            parse_window_spec("nope:5")

    def test_malformed_spec_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_window_spec("nocolon")


class TestWindowSpecDiscriminator:
    """The union accepts ``model_validate({"kind": ..., ...})``
    payloads and dispatches to the right subclass — used by ScanQuery."""

    def test_validates_via_dict_payload(self) -> None:
        from pydantic import TypeAdapter

        adapter: TypeAdapter[WindowSpec] = TypeAdapter(WindowSpec)
        spec = adapter.validate_python({"kind": "trading_minutes", "n": 30})
        assert isinstance(spec, TradingMinutesWindow)
