"""Helpers for tests that must use real market data."""

from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from liq.data.manifest import CoverageManifest, CoverageRange

REAL_SYMBOL = "SPY"
REAL_PROVIDER = "databento"
REAL_DATASET = "EQUS.MINI"
REAL_TIMEFRAME = "1m"


def real_data_root() -> Path:
    workspace = Path(__file__).resolve().parents[2]
    return workspace / "liq-data" / "data" / "financial_data"


def real_spy_source() -> Path:
    return real_data_root() / REAL_PROVIDER / REAL_SYMBOL / "bars" / REAL_TIMEFRAME


def _materialize_store_file(source_file: Path, target_file: Path) -> None:
    target_file.parent.mkdir(parents=True, exist_ok=True)
    if target_file.exists():
        return
    schema = pl.read_parquet_schema(source_file)
    if "symbol" in schema:
        pl.read_parquet(source_file).drop("symbol").write_parquet(target_file)
        return
    try:
        os.link(source_file, target_file)
    except OSError:
        shutil.copy2(source_file, target_file)


def link_real_spy_data(work_root: Path) -> None:
    source = real_spy_source()
    if not source.exists():
        pytest.skip(f"real SPY data not found: {source}")

    target = work_root / REAL_PROVIDER / REAL_SYMBOL / "bars" / REAL_TIMEFRAME
    for source_file in source.rglob("*.parquet"):
        target_file = target / source_file.relative_to(source)
        _materialize_store_file(source_file, target_file)

    files = sorted(source.rglob("*.parquet"))
    row = (
        pl.scan_parquet([str(p) for p in files])
        .select(
            pl.col("timestamp").min().alias("start"),
            pl.col("timestamp").max().alias("end"),
        )
        .collect()
        .row(0, named=True)
    )
    start = row["start"]
    end = row["end"]
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        pytest.skip(f"real SPY data has invalid timestamp bounds: {source}")
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)

    manifest = CoverageManifest.load(
        root=work_root,
        provider=REAL_PROVIDER,
        dataset=REAL_DATASET,
        timeframe=REAL_TIMEFRAME,
        symbol=REAL_SYMBOL,
    )
    with manifest.transaction() as txn:
        txn.record(
            CoverageRange(
                start=start.astimezone(UTC),
                end=end.astimezone(UTC) + timedelta(minutes=1),
                fetched_at=datetime.now(tz=UTC),
                batch_job_id="real-spy-fixture",
            )
        )
