"""Runtime PIT assertions for mean-reversion anchors."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

import polars as pl

from liq.scan.anchors.mean_reversion.models import MeanReversionAnchor
from liq.scan.exceptions import LeakageViolation

LookbackReferences = Mapping[str, Sequence[int] | range | tuple[int, int]]


def _timestamps(bars: pl.DataFrame | Mapping[str, Sequence[datetime]]) -> list[datetime]:
    if isinstance(bars, pl.DataFrame):
        values = bars["timestamp"].to_list()
    else:
        values = list(bars["timestamp"])
    return [value for value in values if isinstance(value, datetime)]


def _anchor_index(
    anchor: MeanReversionAnchor, bars: pl.DataFrame | Mapping[str, Sequence[datetime]]
) -> int:
    timestamps = _timestamps(bars)
    for index, timestamp in enumerate(timestamps):
        if timestamp == anchor.anchor_ts:
            return index
    raise LeakageViolation(
        f"anchor_event_id={anchor.anchor_event_id} symbol={anchor.symbol}: anchor_ts not found"
    )


def _referenced_indices(reference: Sequence[int] | range | tuple[int, int]) -> list[int]:
    if isinstance(reference, tuple) and len(reference) == 2:
        start, stop = reference
        return list(range(start, stop))
    return [int(index) for index in reference]


def assert_anchor_pit(
    anchor: MeanReversionAnchor,
    bars: pl.DataFrame | Mapping[str, Sequence[datetime]],
    lookbacks: LookbackReferences,
) -> None:
    """Raise if anchor features reference the anchor bar or later bars."""
    anchor_index = _anchor_index(anchor, bars)
    if anchor.anchor_vol_source.availability_ts > anchor.anchor_ts:
        raise LeakageViolation(
            f"anchor_event_id={anchor.anchor_event_id} symbol={anchor.symbol}: "
            f"availability_ts {anchor.anchor_vol_source.availability_ts.isoformat()} exceeds "
            f"anchor_ts {anchor.anchor_ts.isoformat()}"
        )
    for name, reference in lookbacks.items():
        indices = _referenced_indices(reference)
        if any(index >= anchor_index for index in indices):
            raise LeakageViolation(
                f"anchor_event_id={anchor.anchor_event_id} symbol={anchor.symbol}: "
                f"{name} references bar index >= anchor_index ({anchor_index})"
            )


__all__ = ["LookbackReferences", "assert_anchor_pit"]
