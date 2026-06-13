"""``ScanEngine.sweep`` — historical sweep config + iteration helpers.

The sweep iterates as-of timestamps across a date range, re-resolves
the universe per timestamp (so composite-universe membership changes
are honored), runs :meth:`ScanEngine.execute` per as-of, and streams
results into a single persisted sweep folder under the store.

The cadence + persistence shapes live here; the orchestration (and
the per-as-of resolve / PIT enforcement) lives on the engine itself
in :mod:`liq.scan.engine`.
"""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from liq.data.calendar import nyse_session_close

Cadence = Literal["session_close", "minutes_n"]


@dataclass(frozen=True)
class SweepConfig:
    """Configuration for one historical sweep.

    Keep the shape small so two sweeps over identical inputs always
    produce the same ``sweep_id`` (and therefore the same on-disk
    layout) — that's the basis of reproducibility.
    """

    query_name: str
    cadence: Cadence
    start: date
    end: date
    interval_minutes: int | None = None


def as_of_timestamps(config: SweepConfig) -> list[datetime]:
    """Materialize the as-of timestamps for ``config``'s cadence.

    ``session_close`` walks each NYSE regular session in
    ``[start, end]`` and takes its UTC close. ``minutes_n`` walks
    every ``interval_minutes`` from ``start`` through ``end``.
    """
    if config.cadence == "session_close":
        return _session_close_grid(config.start, config.end)
    if config.cadence == "minutes_n":
        if not config.interval_minutes or config.interval_minutes <= 0:
            raise ValueError("minutes_n cadence requires interval_minutes >= 1")
        return _minutes_grid(config.start, config.end, config.interval_minutes)
    raise ValueError(f"unknown cadence {config.cadence!r}")  # pragma: no cover


def _session_close_grid(start: date, end: date) -> list[datetime]:
    cursor = start
    closes: list[datetime] = []
    while cursor <= end:
        with suppress(Exception):
            closes.append(nyse_session_close(cursor))
        cursor += timedelta(days=1)
    return closes


def _minutes_grid(start: date, end: date, interval: int) -> list[datetime]:
    cursor = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
    stop = datetime.combine(end, datetime.min.time(), tzinfo=UTC)
    out: list[datetime] = []
    while cursor <= stop:
        out.append(cursor)
        cursor += timedelta(minutes=interval)
    return out


def filter_remaining(
    as_ofs: Iterable[datetime], already_done: Iterable[datetime]
) -> list[datetime]:
    """Return as-of timestamps not yet in ``already_done``."""
    done = set(already_done)
    return [ts for ts in as_ofs if ts not in done]


__all__ = [
    "Cadence",
    "SweepConfig",
    "as_of_timestamps",
    "filter_remaining",
]
