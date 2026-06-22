from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

from liq.scan.anchors.mean_reversion import (
    AnchorVolSource,
    MeanReversionAnchor,
    compute_anchor_event_id,
)
from liq.scan.persistence import (
    MEAN_REVERSION_ANCHOR_SCHEMA_VERSION,
    read_mean_reversion_anchors,
    write_mean_reversion_anchors,
)
from liq.store.parquet import ParquetStore


def _anchor() -> MeanReversionAnchor:
    ts = datetime(2024, 6, 3, 14, 0, tzinfo=UTC)
    return MeanReversionAnchor(
        symbol="SPY",
        anchor_ts=ts,
        direction="up",
        anchor_event_id=compute_anchor_event_id("run-1", "SPY", ts, "query-v1", "up"),
        scan_run_id="run-1",
        scan_query_version="query-v1",
        metric_version="midrange-excursion-v1",
        resolved_universe_version="universe-v1",
        quality_flags=("warm",),
        excursion_units=Decimal("2.5"),
        midrange_now=Decimal("105"),
        midrange_base=Decimal("100"),
        reversion_target=Decimal("100"),
        vol_t=Decimal("2"),
        anchor_vol_source=AnchorVolSource(
            estimator="range_mean",
            lookback=3,
            min_periods=3,
            calendar_policy="bar_count",
            availability_ts=ts,
        ),
    )


def test_mean_reversion_anchor_round_trip_writes_schema_manifest(tmp_path) -> None:
    store = ParquetStore(str(tmp_path))
    anchor = _anchor()

    key = write_mean_reversion_anchors([anchor], store=store, table_name="anchors/test")
    restored = read_mean_reversion_anchors(store=store, table_name="anchors/test")

    assert key == "anchors/test"
    assert restored == [anchor]

    payload = json.loads((tmp_path / "anchors" / "test" / "meta.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == MEAN_REVERSION_ANCHOR_SCHEMA_VERSION
    assert payload["table_name"] == "mean_reversion_anchors"
    assert "anchor_event_id" in payload["fields"]
