"""Smoke test for ``liq-scan execute``.

Builds a tiny in-process store + manifest under tmp_path, invokes the
CLI via Typer's CliRunner, and validates the JSON output against the
``schemas/scan_result.json`` schema.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from liq.data.manifest import CoverageManifest, CoverageRange
from liq.data.universes import UniverseDefinition, UniverseKind, UniverseRegistry
from liq.scan.cli import app
from liq.store.parquet import ParquetStore

_runner = CliRunner()


def _bars(open_px: float, close_px: float, *, n: int = 30) -> pl.DataFrame:
    base = datetime(2024, 6, 3, 13, 30, tzinfo=UTC)
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
                "timestamp": base + timedelta(minutes=i),
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "volume": 1000,
            }
        )
    return pl.DataFrame(rows)


def _seed(data_root: Path) -> None:
    store = ParquetStore(str(data_root))
    session_open = datetime(2024, 6, 3, 13, 30, tzinfo=UTC)
    session_end = session_open + timedelta(minutes=30)
    for sym, open_px, close_px in [
        ("UPMOVE", 100.0, 110.0),
        ("DOWNMOVE", 100.0, 90.0),
        ("INBAND", 100.0, 102.0),
    ]:
        store.write(f"databento/{sym}/bars/1m", _bars(open_px, close_px))
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
                    end=session_end,
                    fetched_at=datetime(2024, 6, 4, tzinfo=UTC),
                )
            )


@pytest.fixture
def seeded_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from liq.data.settings import get_settings, get_store

    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    get_settings.cache_clear()
    get_store.cache_clear()
    _seed(tmp_path)
    yield tmp_path
    get_settings.cache_clear()
    get_store.cache_clear()


class TestExecuteCli:
    def test_execute_returns_json_results(self, seeded_root: Path) -> None:
        result = _runner.invoke(
            app,
            [
                "execute",
                "--universe",
                "UPMOVE,DOWNMOVE,INBAND",
                "--as-of",
                "2024-06-03T14:00:00+00:00",
                "--window",
                "trading_minutes:30",
                "--threshold",
                "5",
                "--direction",
                "either",
                "--data-root",
                str(seeded_root),
                "--output",
                "json",
            ],
        )
        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)
        assert isinstance(rows, list)
        names = {r["symbol"] for r in rows}
        assert "UPMOVE" in names
        assert "DOWNMOVE" in names
        assert "INBAND" not in names

    def test_execute_validates_against_scan_result_schema(self, seeded_root: Path) -> None:
        schema_path = Path(__file__).resolve().parents[1] / "schemas" / "scan_result.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        result = _runner.invoke(
            app,
            [
                "execute",
                "--universe",
                "UPMOVE,DOWNMOVE",
                "--as-of",
                "2024-06-03T14:00:00+00:00",
                "--window",
                "trading_minutes:30",
                "--threshold",
                "5",
                "--direction",
                "either",
                "--data-root",
                str(seeded_root),
                "--output",
                "json",
            ],
        )
        rows = json.loads(result.output)
        from jsonschema import validate

        for row in rows:
            validate(instance=row, schema=schema)

    def test_execute_tradable_flag_filters_results(self, seeded_root: Path) -> None:
        """``--tradable`` accepts a comma-separated list and drops
        non-tradable symbols from the JSON output."""
        result = _runner.invoke(
            app,
            [
                "execute",
                "--universe",
                "UPMOVE,DOWNMOVE",
                "--as-of",
                "2024-06-03T14:00:00+00:00",
                "--window",
                "trading_minutes:30",
                "--threshold",
                "5",
                "--direction",
                "either",
                # Comma-separated → inline list (matches --universe semantics).
                "--tradable",
                "UPMOVE,DOES-NOT-EXIST",
                "--data-root",
                str(seeded_root),
                "--output",
                "json",
            ],
        )
        assert result.exit_code == 0, result.output
        names = {r["symbol"] for r in json.loads(result.output)}
        assert "UPMOVE" in names
        assert "DOWNMOVE" not in names

    def test_execute_named_universe_and_named_tradable_use_registry(
        self, seeded_root: Path
    ) -> None:
        registry = UniverseRegistry(seeded_root)
        registry.save(
            UniverseDefinition(
                name="research-moves",
                version=1,
                kind=UniverseKind.EXPLICIT,
                spec={"symbols": ["UPMOVE", "DOWNMOVE"]},
            )
        )
        registry.save(
            UniverseDefinition(
                name="tradable-moves",
                version=1,
                kind=UniverseKind.EXPLICIT,
                spec={"symbols": ["UPMOVE"]},
            )
        )

        result = _runner.invoke(
            app,
            [
                "execute",
                "--universe",
                "research-moves",
                "--as-of",
                "2024-06-03T14:00:00+00:00",
                "--window",
                "trading_minutes:30",
                "--threshold",
                "5",
                "--direction",
                "either",
                "--tradable",
                "tradable-moves",
                "--data-root",
                str(seeded_root),
                "--output",
                "json",
            ],
        )

        assert result.exit_code == 0, result.output
        assert [r["symbol"] for r in json.loads(result.output)] == ["UPMOVE"]

    def test_execute_coverage_gap_exit_code_two(self, seeded_root: Path) -> None:
        result = _runner.invoke(
            app,
            [
                "execute",
                "--universe",
                "UPMOVE,GHOST",
                "--as-of",
                "2024-06-03T14:00:00+00:00",
                "--window",
                "trading_minutes:30",
                "--threshold",
                "5",
                "--direction",
                "either",
                "--data-root",
                str(seeded_root),
            ],
        )
        assert result.exit_code == 2
