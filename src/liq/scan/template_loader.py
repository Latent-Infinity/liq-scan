"""Load a :class:`ScanQueryTemplate` from a yaml file.

The yaml shape matches the model JSON-schema closely but with a
``kind`` discriminator on ``window`` and ``predicate``. ISO-8601
durations (``PT2H``, ``PT30M``) are accepted for
``calendar.duration``; ``timedelta`` round-trips through pydantic
without an explicit override.

The loader is intentionally narrow — it understands the same
predicates and windows liq-scan ships and rejects anything unknown
so a typo doesn't silently become a different query.
"""

from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from liq.scan.predicates import (
    AndPredicate,
    AnyPredicate,
    DollarVolumePredicate,
    MovePredicate,
    PricePredicate,
)
from liq.scan.query import ScanQueryTemplate
from liq.scan.window import CalendarWindow, SessionsWindow, TradingMinutesWindow, WindowSpec

_ISO_DURATION = re.compile(
    r"^P(?:T(?P<hours>\d+)H)?(?:T?(?P<minutes>\d+)M)?(?:T?(?P<seconds>\d+)S)?$"
)


def load_query_template(path: Path) -> ScanQueryTemplate:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    window = _build_window(data.get("window", {}))
    predicate = _build_predicate(data.get("predicate", {}))
    return ScanQueryTemplate(
        universe_ref=data["universe_ref"],
        window=window,
        predicate=predicate,
        ranking=data.get("ranking", "abs_move"),
        limit=data.get("limit"),
        include_extended_hours=bool(data.get("include_extended_hours", False)),
        metric_version=data.get("metric_version", "midrange-endpoint-v1"),
        split_handling=data.get("split_handling", "adjust"),
    )


# ----- builders -------------------------------------------------------------


def _build_window(spec: dict[str, Any]) -> WindowSpec:
    kind = spec.get("kind")
    if kind == "trading_minutes":
        return TradingMinutesWindow(kind="trading_minutes", n=int(spec["n"]))
    if kind == "calendar":
        return CalendarWindow(kind="calendar", duration=_parse_duration(spec["duration"]))
    if kind == "sessions":
        return SessionsWindow(kind="sessions", n=int(spec["n"]))
    raise ValueError(f"unknown window kind {kind!r}")


def _build_predicate(spec: dict[str, Any]) -> AnyPredicate:
    kind = spec.get("kind")
    if kind == "move":
        return MovePredicate(
            threshold_pct=float(spec["threshold_pct"]),
            direction=spec["direction"],
            k=int(spec.get("k", 5)),
        )
    if kind == "dollar_volume":
        return DollarVolumePredicate(min_usd=Decimal(str(spec["min_usd"])))
    if kind == "price":
        return PricePredicate(min_usd=Decimal(str(spec["min_usd"])))
    if kind == "and":
        children = [_build_predicate(child) for child in spec.get("predicates", [])]
        return AndPredicate(predicates=children)
    raise ValueError(f"unknown predicate kind {kind!r}")


def _parse_duration(text: Any) -> timedelta:
    if isinstance(text, timedelta):
        return text
    if isinstance(text, int | float):
        return timedelta(seconds=float(text))
    if not isinstance(text, str):
        raise ValueError(f"unsupported duration value {text!r}")
    match = _ISO_DURATION.match(text)
    if not match:
        raise ValueError(f"malformed ISO-8601 duration {text!r}")
    return timedelta(
        hours=int(match.group("hours") or 0),
        minutes=int(match.group("minutes") or 0),
        seconds=int(match.group("seconds") or 0),
    )


__all__ = ["load_query_template"]
