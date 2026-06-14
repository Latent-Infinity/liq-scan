"""Real-data memory budget gate for historical sweeps.

This test intentionally refuses to create market bars. It requires a
real parquet data root and either an explicit symbol list or a data
root large enough to discover the required universe.
"""

from __future__ import annotations

import os
import shutil
import tracemalloc
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from liq.data.manifest import CoverageManifest, CoverageRange
from liq.data.service import DataService
from liq.scan.engine import ScanEngine
from liq.scan.predicates import MovePredicate
from liq.scan.query import ScanQueryTemplate
from liq.scan.sweep import SweepConfig
from liq.scan.window import TradingMinutesWindow
from liq.store.parquet import ParquetStore

_BYTES_PER_GB = 1024**3
_MEMORY_BUDGET_BYTES = 4 * _BYTES_PER_GB
_DEFAULT_UNIVERSE_SIZE = 500
_DEFAULT_RANGE_DAYS = 365
_DEFAULT_START = date(2024, 1, 2)


def _real_data_root() -> Path:
    raw = os.environ.get("PERF_DATA_ROOT")
    if raw:
        return Path(raw).expanduser()
    workspace = Path(__file__).resolve().parents[3]
    return workspace / "liq-data" / "data" / "financial_data"


def _date_from_env(name: str, default: date) -> date:
    raw = os.environ.get(name)
    return date.fromisoformat(raw) if raw else default


def _timeframe_delta(timeframe: str) -> timedelta:
    if timeframe.endswith("m"):
        return timedelta(minutes=int(timeframe[:-1]))
    if timeframe.endswith("h"):
        return timedelta(hours=int(timeframe[:-1]))
    if timeframe.endswith("d"):
        return timedelta(days=int(timeframe[:-1]))
    raise ValueError(f"unsupported timeframe for real-data memory gate: {timeframe!r}")


def _symbols_from_file(path: Path) -> list[str]:
    return [
        line.strip().upper()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _discover_symbols(root: Path, *, provider: str, timeframe: str) -> list[str]:
    provider_root = root / provider
    if not provider_root.exists():
        return []
    symbols = []
    for child in provider_root.iterdir():
        if (child / "bars" / timeframe).exists():
            symbols.append(child.name.upper())
    return sorted(symbols)


def _selected_symbols(root: Path, *, provider: str, timeframe: str) -> list[str]:
    if raw := os.environ.get("PERF_SYMBOLS"):
        return [s.strip().upper() for s in raw.split(",") if s.strip()]
    if raw := os.environ.get("PERF_SYMBOL_FILE"):
        return _symbols_from_file(Path(raw).expanduser())
    return _discover_symbols(root, provider=provider, timeframe=timeframe)


def _parquet_files(root: Path, *, provider: str, symbol: str, timeframe: str) -> list[Path]:
    key_root = root / provider / symbol / "bars" / timeframe
    return sorted(key_root.rglob("*.parquet")) if key_root.exists() else []


def _real_coverage(
    root: Path,
    *,
    dataset: str,
    provider: str,
    symbol: str,
    timeframe: str,
) -> tuple[datetime, datetime] | None:
    manifest_path = root / "metadata" / provider / dataset / timeframe / symbol / "manifest.parquet"
    if manifest_path.exists():
        frame = pl.scan_parquet(str(manifest_path)).select(
            pl.col("start_ts").min().alias("start"),
            pl.col("end_ts").max().alias("end"),
        )
        row = frame.collect().row(0, named=True)
        start = row["start"]
        end = row["end"]
        if isinstance(start, datetime) and isinstance(end, datetime):
            if start.tzinfo is None:
                start = start.replace(tzinfo=UTC)
            if end.tzinfo is None:
                end = end.replace(tzinfo=UTC)
            return start.astimezone(UTC), end.astimezone(UTC)

    files = _parquet_files(root, provider=provider, symbol=symbol, timeframe=timeframe)
    if not files:
        return None
    frame = pl.scan_parquet([str(p) for p in files]).select(
        pl.col("timestamp").min().alias("start"),
        pl.col("timestamp").max().alias("end"),
    )
    row = frame.collect().row(0, named=True)
    start = row["start"]
    end = row["end"]
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return start.astimezone(UTC), end.astimezone(UTC) + _timeframe_delta(timeframe)


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


def _link_real_symbol(
    source_root: Path,
    work_root: Path,
    *,
    provider: str,
    dataset: str,
    symbol: str,
    timeframe: str,
    required_start: datetime,
    required_end: datetime,
) -> bool:
    source = source_root / provider / symbol / "bars" / timeframe
    if not source.exists():
        return False
    target = work_root / provider / symbol / "bars" / timeframe
    for source_file in source.rglob("*.parquet"):
        target_file = target / source_file.relative_to(source)
        _materialize_store_file(source_file, target_file)

    coverage = _real_coverage(
        source_root,
        provider=provider,
        dataset=dataset,
        symbol=symbol,
        timeframe=timeframe,
    )
    if coverage is None:
        return False
    start, end = coverage
    if start > required_start or end < required_end:
        return False
    manifest = CoverageManifest.load(
        root=work_root,
        provider=provider,
        dataset=dataset,
        timeframe=timeframe,
        symbol=symbol,
    )
    with manifest.transaction() as txn:
        txn.record(
            CoverageRange(
                start=start,
                end=end,
                fetched_at=datetime.now(tz=UTC),
                batch_job_id="real-data-memory-gate",
            )
        )
    return True


@pytest.fixture
def real_year_universe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from liq.data.settings import get_settings, get_store

    provider = os.environ.get("PERF_PROVIDER", "databento")
    dataset = os.environ.get("PERF_DATASET", "EQUS.MINI")
    timeframe = os.environ.get("PERF_TIMEFRAME", "1m")
    min_symbols = int(os.environ.get("PERF_MIN_SYMBOLS", _DEFAULT_UNIVERSE_SIZE))
    range_days = int(os.environ.get("PERF_RANGE_DAYS", _DEFAULT_RANGE_DAYS))
    start = _date_from_env("PERF_START", _DEFAULT_START)
    end = _date_from_env("PERF_END", start + timedelta(days=range_days - 1))
    required_start = datetime(start.year, start.month, start.day, tzinfo=UTC)
    required_end = datetime(end.year, end.month, end.day, tzinfo=UTC) + timedelta(days=1)

    source_root = _real_data_root()
    if not source_root.exists():
        pytest.skip(f"real data root not found: {source_root}")

    symbols = _selected_symbols(source_root, provider=provider, timeframe=timeframe)
    if len(symbols) < min_symbols:
        pytest.skip(
            f"real data universe has {len(symbols)} symbols; {min_symbols} required "
            "for the memory gate"
        )

    linked = [
        symbol
        for symbol in symbols
        if _link_real_symbol(
            source_root,
            tmp_path,
            provider=provider,
            dataset=dataset,
            symbol=symbol,
            timeframe=timeframe,
            required_start=required_start,
            required_end=required_end,
        )
    ]
    if len(linked) < min_symbols:
        pytest.skip(
            f"only {len(linked)} real symbols have readable parquet; {min_symbols} required"
        )

    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    get_settings.cache_clear()
    get_store.cache_clear()

    store = ParquetStore(str(tmp_path))
    engine = ScanEngine(
        data_service=DataService(),
        store=store,
        provider=provider,
        dataset=dataset,
        timeframe=timeframe,
    )
    template = ScanQueryTemplate(
        universe_ref=linked,
        window=TradingMinutesWindow(kind="trading_minutes", n=30),
        predicate=MovePredicate(threshold_pct=0.1, direction="either"),
        ranking="abs_move",
        limit=None,
    )
    config = SweepConfig(
        query_name="memory_perf_real",
        cadence="session_close",
        start=start,
        end=end,
    )
    yield engine, template, config
    get_settings.cache_clear()
    get_store.cache_clear()


@pytest.mark.perf
@pytest.mark.skipif(
    os.environ.get("RUN_LARGE_PERF") != "1",
    reason="set RUN_LARGE_PERF=1 and provide real market data to run the memory gate",
)
def test_one_year_real_sweep_under_memory_budget(real_year_universe) -> None:
    engine, template, config = real_year_universe

    tracemalloc.start()
    try:
        engine.sweep(template, config)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    peak_gb = peak / _BYTES_PER_GB
    print(f"real-data sweep peak allocation: {peak_gb:.2f} GB")
    assert peak < _MEMORY_BUDGET_BYTES, (
        f"real-data sweep peak allocation {peak_gb:.2f} GB exceeds 4 GB budget"
    )
