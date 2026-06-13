"""Window specifications for ``ScanQuery``.

Three kinds of windows are supported, declared as a Pydantic
discriminated union so the same input shape works for both the CLI
parser and a programmatic ``ScanQuery(...)`` call:

* ``trading_minutes:N`` — last ``N`` trading minutes (skips weekends
  / holidays). Resolved against the NYSE calendar via
  :mod:`liq.data.calendar`.
* ``calendar:DURATION`` — wall-clock duration subtraction
  (e.g., ``2h``, ``30m``, ``1d``). No calendar awareness.
* ``sessions:N`` — open of session ``N-1`` back through ``end``.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from liq.data.calendar import trading_minutes_window, trading_sessions_window


class _BaseWindow(BaseModel):
    model_config = ConfigDict(frozen=True)

    def resolve(self, end: datetime) -> tuple[datetime, datetime]:  # pragma: no cover
        raise NotImplementedError


class TradingMinutesWindow(_BaseWindow):
    kind: Literal["trading_minutes"]
    n: int = Field(gt=0, description="Number of trading minutes in the window")

    def resolve(self, end: datetime) -> tuple[datetime, datetime]:
        return trading_minutes_window(end, self.n)


class CalendarWindow(_BaseWindow):
    kind: Literal["calendar"]
    duration: timedelta = Field(description="Wall-clock duration")

    def resolve(self, end: datetime) -> tuple[datetime, datetime]:
        return (end - self.duration, end)

    def model_post_init(self, _ctx: object) -> None:
        if self.duration <= timedelta(0):
            from pydantic import ValidationError
            from pydantic_core import PydanticCustomError

            raise ValidationError.from_exception_data(
                "CalendarWindow",
                [
                    {
                        "type": PydanticCustomError("value_error", "duration must be positive"),
                        "loc": ("duration",),
                        "input": self.duration,
                    }
                ],
            )


class SessionsWindow(_BaseWindow):
    kind: Literal["sessions"]
    n: int = Field(gt=0, description="Number of trading sessions in the window")

    def resolve(self, end: datetime) -> tuple[datetime, datetime]:
        return trading_sessions_window(end, self.n)


WindowSpec = Annotated[
    TradingMinutesWindow | CalendarWindow | SessionsWindow,
    Field(discriminator="kind"),
]
"""Discriminated union over the three window kinds."""


# ----- string parsing -------------------------------------------------------


_DURATION_RE = re.compile(r"^(?P<value>\d+)(?P<unit>[smhd])$")
_DURATION_UNITS = {
    "s": "seconds",
    "m": "minutes",
    "h": "hours",
    "d": "days",
}


def _parse_duration(spec: str) -> timedelta:
    m = _DURATION_RE.match(spec)
    if not m:
        raise ValueError(f"malformed calendar duration {spec!r}; expected '<int><s|m|h|d>'")
    return timedelta(**{_DURATION_UNITS[m["unit"]]: int(m["value"])})


def parse_window_spec(text: str) -> TradingMinutesWindow | CalendarWindow | SessionsWindow:
    """Parse a CLI-style ``"kind:value"`` window spec.

    ``trading_minutes:390`` / ``sessions:3`` / ``calendar:2h``.
    """
    if ":" not in text:
        raise ValueError(f"window spec {text!r} must be of the form 'kind:value'")
    kind, _, value = text.partition(":")
    if kind == "trading_minutes":
        return TradingMinutesWindow(kind="trading_minutes", n=int(value))
    if kind == "sessions":
        return SessionsWindow(kind="sessions", n=int(value))
    if kind == "calendar":
        return CalendarWindow(kind="calendar", duration=_parse_duration(value))
    raise ValueError(f"unknown window kind {kind!r}; expected trading_minutes|sessions|calendar")


__all__ = [
    "CalendarWindow",
    "SessionsWindow",
    "TradingMinutesWindow",
    "WindowSpec",
    "parse_window_spec",
]
