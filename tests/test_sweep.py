"""TDD tests for ``ScanEngine.sweep`` + sweep persistence.

The sweep iterates as-of timestamps across a date range, re-resolving
the universe per timestamp, and persists every result row into a
single sweep folder under the store. Resumability and reproducibility
are the load-bearing invariants — a sweep that's interrupted re-runs
exactly the missing as-ofs, and two consecutive sweeps over identical
inputs produce a byte-identical ``runs.parquet``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from liq.data.manifest import CoverageManifest, CoverageRange
from liq.data.service import DataService
from liq.data.universes import UniverseDefinition, UniverseKind
from liq.scan.engine import ScanEngine
from liq.scan.exceptions import NonPITUniverseError
from liq.scan.persistence import (
    SweepArtifacts,
    completed_asofs_key,
    list_persisted_as_ofs,
    load_runs,
    meta_json_path,
    sweep_key_prefix,
)
from liq.scan.predicates import MovePredicate
from liq.scan.query import ScanQueryTemplate
from liq.scan.sweep import SweepConfig
from liq.scan.window import TradingMinutesWindow
from liq.store.parquet import ParquetStore

# ----- fixtures -------------------------------------------------------------


def _bars(open_px: float, close_px: float, *, start: datetime, n: int = 30) -> pl.DataFrame:
    rows = []
    for i in range(n):
        if i < 5:
            o = h = lo = c = open_px
        elif i >= n - 5:
            o = h = lo = c = close_px
        else:
            o = h = lo = c = open_px + (i / n) * (close_px - open_px)
        rows.append(
            {
                "timestamp": start + timedelta(minutes=i),
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "volume": 1000,
            }
        )
    return pl.DataFrame(rows)


# Sessions seeded with the last 30 minutes of bars so a
# ``trading_minutes:30`` window ending at session_close (20:00 UTC)
# lands on data we actually wrote.
_SESSION_OPENS = {
    1: datetime(2024, 6, 3, 19, 30, tzinfo=UTC),  # Mon — UPMOVE hit
    2: datetime(2024, 6, 4, 19, 30, tzinfo=UTC),  # Tue — flat
    3: datetime(2024, 6, 5, 19, 30, tzinfo=UTC),  # Wed — DOWNMOVE hit
    4: datetime(2024, 6, 6, 19, 30, tzinfo=UTC),  # Thu — flat
    5: datetime(2024, 6, 7, 19, 30, tzinfo=UTC),  # Fri — UPMOVE hit
}


def _profile_for_session(idx: int) -> dict[str, tuple[float, float]]:
    """Up/down profiles per session."""
    if idx == 1:
        return {"UPMOVE": (100.0, 110.0), "DOWNMOVE": (100.0, 99.0)}
    if idx == 2:
        return {"UPMOVE": (100.0, 100.5), "DOWNMOVE": (100.0, 99.5)}
    if idx == 3:
        return {"UPMOVE": (100.0, 100.5), "DOWNMOVE": (100.0, 88.0)}
    if idx == 4:
        return {"UPMOVE": (100.0, 100.5), "DOWNMOVE": (100.0, 100.5)}
    return {"UPMOVE": (100.0, 109.0), "DOWNMOVE": (100.0, 99.0)}


def _seed(data_root: Path, *, only_sessions: tuple[int, ...] = (1, 2, 3, 4, 5)) -> None:
    store = ParquetStore(str(data_root))
    for idx in only_sessions:
        session_open = _SESSION_OPENS[idx]
        for sym, (open_px, close_px) in _profile_for_session(idx).items():
            store.write(
                f"databento/{sym}/bars/1m",
                _bars(open_px, close_px, start=session_open),
            )
            manifest = CoverageManifest.load(
                root=data_root,
                provider="databento",
                dataset="EQUS.MINI",
                timeframe="1m",
                symbol=sym,
            )
            with manifest.transaction() as txn:
                txn.record(
                    CoverageRange(
                        start=session_open,
                        end=session_open + timedelta(minutes=30),
                        fetched_at=datetime(2024, 6, 8, tzinfo=UTC),
                    )
                )


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from liq.data.settings import get_settings, get_store

    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    get_settings.cache_clear()
    get_store.cache_clear()

    store = ParquetStore(str(tmp_path))
    eng = ScanEngine(data_service=DataService(), store=store)
    yield eng, tmp_path
    get_settings.cache_clear()
    get_store.cache_clear()


def _template() -> ScanQueryTemplate:
    return ScanQueryTemplate(
        universe_ref=["UPMOVE", "DOWNMOVE"],
        window=TradingMinutesWindow(kind="trading_minutes", n=30),
        predicate=MovePredicate(threshold_pct=5.0, direction="either"),
        ranking="abs_move",
        limit=None,
    )


def _config(name: str = "sp500_5pct") -> SweepConfig:
    return SweepConfig(
        query_name=name,
        cadence="session_close",
        start=date(2024, 6, 3),
        end=date(2024, 6, 7),
    )


# ----- 5-session correctness + persistence ----------------------------------


class TestSweepCorrectness:
    def test_five_session_sweep_produces_three_hits(self, engine) -> None:
        eng, root = engine
        _seed(root)

        artifacts = eng.sweep(_template(), _config())
        runs = load_runs(eng.store, _config())
        # Sessions 1, 3, 5 each yield at least one row (UPMOVE / DOWNMOVE / UPMOVE).
        as_ofs = sorted({row["as_of"] for row in runs.iter_rows(named=True)})
        assert len(as_ofs) == 3
        assert {row["symbol"] for row in runs.iter_rows(named=True)} == {"UPMOVE", "DOWNMOVE"}
        assert artifacts.runs_count == runs.height
        assert artifacts.sessions_scanned == 5
        assert artifacts.sessions_with_hits == 3

    def test_persisted_artifacts_carry_query_metadata(self, engine) -> None:
        eng, root = engine
        _seed(root)
        eng.sweep(_template(), _config())

        prefix = sweep_key_prefix(_config())
        meta_key = f"{prefix}/meta"
        assert eng.store.exists(meta_key)
        meta = eng.store.read(meta_key)
        # The meta frame should encode query_name, sweep_id, cadence,
        # start, end, query_hash; one row per sweep.
        row = meta.row(0, named=True)
        assert row["query_name"] == "sp500_5pct"
        assert row["cadence"] == "session_close"
        assert row["data_version_hash"]
        meta_json = json.loads(meta_json_path(eng.store, _config()).read_text(encoding="utf-8"))
        assert meta_json["query_name"] == "sp500_5pct"
        assert meta_json["query_template"]["universe_ref"] == ["UPMOVE", "DOWNMOVE"]
        assert meta_json["data_version_hash"] == row["data_version_hash"]


# ----- PIT enforcement ------------------------------------------------------


class TestPITEnforcement:
    def test_non_pit_universe_raises_before_any_read(self, engine) -> None:
        eng, root = engine
        _seed(root)
        composite_template = ScanQueryTemplate(
            universe_ref=UniverseDefinition(
                name="sp500",
                version=1,
                kind=UniverseKind.COMPOSITE,
                spec={"source": "stub", "id": "SP500"},
            ),
            window=TradingMinutesWindow(kind="trading_minutes", n=30),
            predicate=MovePredicate(threshold_pct=5.0, direction="either"),
            ranking="abs_move",
            limit=None,
        )
        with pytest.raises(NonPITUniverseError):
            eng.sweep(composite_template, _config())


# ----- resumability ---------------------------------------------------------


class TestResumability:
    def test_resume_after_mid_sweep_failure_completes_remaining(
        self, engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        eng, root = engine
        _seed(root)

        # Inject a failure after the second persisted session.
        call_count = {"n": 0}
        real_execute = eng.execute

        def failing_execute(query):
            call_count["n"] += 1
            if call_count["n"] == 3:
                raise RuntimeError("boom mid-sweep")
            return real_execute(query)

        monkeypatch.setattr(eng, "execute", failing_execute)
        with pytest.raises(RuntimeError):
            eng.sweep(_template(), _config())

        # The first sweep should have persisted sessions 1 & 2.
        first_as_ofs = list_persisted_as_ofs(eng.store, _config())

        # Resume — restore real execute.
        monkeypatch.setattr(eng, "execute", real_execute)
        artifacts = eng.sweep(_template(), _config())

        final_as_ofs = list_persisted_as_ofs(eng.store, _config())

        # No duplicates: every as_of appears exactly once across the two runs.
        assert final_as_ofs == sorted(set(final_as_ofs))
        # The resume must extend the persisted set, not regenerate it.
        for ts in first_as_ofs:
            assert ts in final_as_ofs
        assert artifacts.resumed_from is not None
        assert len(final_as_ofs) == 5

    def test_second_sweep_skips_zero_hit_sessions(
        self, engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        eng, root = engine
        _seed(root)
        eng.sweep(_template(), _config())

        def fail_if_called(query):
            raise AssertionError("execute should not run for a completed sweep")

        monkeypatch.setattr(eng, "execute", fail_if_called)
        artifacts = eng.sweep(_template(), _config())

        assert artifacts.resumed_from is not None
        assert artifacts.runs_count == load_runs(eng.store, _config()).height


# ----- reproducibility ------------------------------------------------------


class TestReproducibility:
    def test_second_sweep_is_byte_identical(self, engine, tmp_path: Path) -> None:
        eng, root = engine
        _seed(root)
        eng.sweep(_template(), _config())
        first_bytes = _runs_bytes(eng, _config())

        # Wipe & re-seed in a fresh tmp_path-equivalent location.
        # Resumability would otherwise short-circuit the second sweep.
        prefix = sweep_key_prefix(_config())
        eng.store.delete(f"{prefix}/runs")
        eng.store.delete(f"{prefix}/meta")
        eng.store.delete(f"{prefix}/universes")
        eng.store.delete(completed_asofs_key(_config()))
        path = meta_json_path(eng.store, _config())
        if path.exists():
            path.unlink()
        eng.sweep(_template(), _config())
        second_bytes = _runs_bytes(eng, _config())

        assert hashlib.sha256(first_bytes).digest() == hashlib.sha256(second_bytes).digest()


def _runs_bytes(engine_obj: ScanEngine, config: SweepConfig) -> bytes:
    """Serialize the on-disk runs frame to canonical CSV bytes.

    Direct parquet byte-equality is brittle (compression dict ordering,
    block alignment); compare canonical row content instead.
    """
    runs = load_runs(engine_obj.store, config)
    return runs.write_csv().encode("utf-8")


# ----- PIT audit ------------------------------------------------------------


class TestPITAudit:
    def test_no_bar_after_as_of_enters_any_result(self, engine) -> None:
        eng, root = engine
        # Seed bars only through session 3.
        _seed(root, only_sessions=(1, 2, 3))
        # Manifest claims coverage through session 5 — the bars are
        # absent on disk so subsequent sessions yield no rows. Since
        # CoverageGapError fires on manifest gaps, we tighten the sweep
        # to sessions 1-3 only.
        config = SweepConfig(
            query_name="sp500_5pct",
            cadence="session_close",
            start=date(2024, 6, 3),
            end=date(2024, 6, 5),
        )
        eng.sweep(_template(), config)
        runs = load_runs(eng.store, config)

        # No window_end on any row may exceed the corresponding as_of.
        for row in runs.iter_rows(named=True):
            assert row["window_end"] <= row["as_of"]


# ----- persistence artifact return ------------------------------------------


class TestSweepArtifacts:
    def test_artifacts_carry_sweep_id_and_counts(self, engine) -> None:
        eng, root = engine
        _seed(root)
        artifacts = eng.sweep(_template(), _config())
        assert isinstance(artifacts, SweepArtifacts)
        assert artifacts.sweep_id  # non-empty
        assert artifacts.query_name == "sp500_5pct"
