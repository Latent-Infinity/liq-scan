"""Tests for sweep schema versioning.

Persistence invariants:

* Every persisted artifact carries the writer's ``SCHEMA_VERSION``.
* Reads tolerate the same version (and lower, when older data still
  meets the contract) but **refuse** unknown future versions with
  :class:`ScanSchemaError`.
* The version lives in two places per artifact — the parquet row
  schema (for column-wise reads) and ``meta.json`` (for operators).
  Both must agree.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from liq.data.service import DataService
from liq.scan.engine import ScanEngine
from liq.scan.exceptions import ScanSchemaError
from liq.scan.persistence import (
    SCHEMA_VERSION,
    load_meta,
    load_meta_json,
    load_runs,
    meta_json_path,
    runs_schema,
    sweep_key_prefix,
)
from liq.scan.predicates import MovePredicate
from liq.scan.query import ScanQueryTemplate
from liq.scan.sweep import SweepConfig
from liq.scan.window import TradingMinutesWindow
from liq.store.parquet import ParquetStore
from tests.real_market_data import REAL_SYMBOL, link_real_spy_data


@pytest.fixture
def engine_with_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from liq.data.settings import get_settings, get_store

    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    get_settings.cache_clear()
    get_store.cache_clear()

    store = ParquetStore(str(tmp_path))
    link_real_spy_data(tmp_path)

    engine = ScanEngine(data_service=DataService(), store=store)
    template = ScanQueryTemplate(
        universe_ref=[REAL_SYMBOL],
        window=TradingMinutesWindow(kind="trading_minutes", n=30),
        predicate=MovePredicate(threshold_pct=0.1, direction="either"),
        ranking="abs_move",
        limit=None,
    )
    config = SweepConfig(
        query_name="sp500_5pct",
        cadence="session_close",
        start=date(2024, 6, 3),
        end=date(2024, 6, 3),
    )
    engine.sweep(template, config)
    yield engine, config, tmp_path
    get_settings.cache_clear()
    get_store.cache_clear()


# ----- stamping ------------------------------------------------------------


class TestSchemaVersionStamp:
    def test_runs_schema_includes_schema_version_column(self) -> None:
        assert "schema_version" in runs_schema()

    def test_runs_rows_carry_current_version(self, engine_with_run) -> None:
        engine, config, _ = engine_with_run
        runs = load_runs(engine.store, config)
        assert not runs.is_empty()
        assert set(runs["schema_version"].unique().to_list()) == {SCHEMA_VERSION}

    def test_meta_parquet_row_carries_version(self, engine_with_run) -> None:
        engine, config, _ = engine_with_run
        meta_key = f"{sweep_key_prefix(config)}/meta"
        frame = engine.store.read(meta_key)
        assert frame.row(0, named=True)["schema_version"] == SCHEMA_VERSION

    def test_meta_json_carries_version(self, engine_with_run) -> None:
        engine, config, _ = engine_with_run
        meta_payload = json.loads(meta_json_path(engine.store, config).read_text(encoding="utf-8"))
        assert meta_payload["schema_version"] == SCHEMA_VERSION


# ----- gate ---------------------------------------------------------------


class TestForwardCompatGate:
    def test_unknown_future_version_in_runs_raises(self, engine_with_run) -> None:
        engine, config, _ = engine_with_run
        runs_key = f"{sweep_key_prefix(config)}/runs"
        frame = engine.store.read(runs_key)
        future = frame.with_columns(pl.lit(SCHEMA_VERSION + 1).alias("schema_version"))
        engine.store.write(runs_key, future, mode="overwrite")

        with pytest.raises(ScanSchemaError) as excinfo:
            load_runs(engine.store, config)
        msg = str(excinfo.value)
        assert str(SCHEMA_VERSION + 1) in msg
        assert str(SCHEMA_VERSION) in msg

    def test_unknown_future_version_in_meta_raises(self, engine_with_run) -> None:
        engine, config, _ = engine_with_run
        meta_key = f"{sweep_key_prefix(config)}/meta"
        frame = engine.store.read(meta_key)
        future = frame.with_columns(pl.lit(SCHEMA_VERSION + 1).alias("schema_version"))
        engine.store.write(meta_key, future, mode="overwrite")
        with pytest.raises(ScanSchemaError):
            load_meta(engine.store, config)

    def test_unknown_future_version_in_meta_json_raises(self, engine_with_run) -> None:
        engine, config, _ = engine_with_run
        path = meta_json_path(engine.store, config)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["schema_version"] = SCHEMA_VERSION + 1
        path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(ScanSchemaError):
            load_meta_json(engine.store, config)

    def test_same_version_reads_cleanly(self, engine_with_run) -> None:
        engine, config, _ = engine_with_run
        runs = load_runs(engine.store, config)
        assert runs.height >= 1
        meta = load_meta(engine.store, config)
        assert meta["query_name"] == "sp500_5pct"
        meta_json = load_meta_json(engine.store, config)
        assert meta_json["query_name"] == "sp500_5pct"

    def test_legacy_unstamped_artifact_reads_cleanly(self, engine_with_run) -> None:
        """An artifact written before the version column existed
        should still load — we tolerate the missing column rather than
        force a migration."""
        engine, config, _ = engine_with_run
        runs_key = f"{sweep_key_prefix(config)}/runs"
        legacy = engine.store.read(runs_key).drop("schema_version")
        engine.store.write(runs_key, legacy, mode="overwrite")

        runs = load_runs(engine.store, config)
        assert runs.height >= 1
        # Loader should inject the current SCHEMA_VERSION column.
        assert set(runs["schema_version"].unique().to_list()) == {SCHEMA_VERSION}
