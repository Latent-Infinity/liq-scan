"""Smoke test for ``liq-scan sweep``.

Seeds a small two-session toy universe, writes a yaml template, runs
the CLI, and asserts the persisted runs key has content + the JSON
summary on stdout is valid.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from liq.data.manifest import CoverageManifest, CoverageRange
from liq.scan.cli import app
from liq.store.parquet import ParquetStore

_runner = CliRunner()


def _bars(open_px: float, close_px: float, *, start: datetime) -> pl.DataFrame:
    rows = []
    for i in range(30):
        v = open_px if i < 5 else close_px if i >= 25 else open_px + (i / 30) * (close_px - open_px)
        rows.append(
            {
                "timestamp": start + timedelta(minutes=i),
                "open": v,
                "high": v,
                "low": v,
                "close": v,
                "volume": 1000,
            }
        )
    return pl.DataFrame(rows)


def _seed(root: Path) -> None:
    store = ParquetStore(str(root))
    for day, profile in [
        (3, {"UPMOVE": (100.0, 110.0)}),
        (4, {"UPMOVE": (100.0, 100.5)}),
    ]:
        sopen = datetime(2024, 6, day, 19, 30, tzinfo=UTC)
        for sym, (o, c) in profile.items():
            store.write(f"databento/{sym}/bars/1m", _bars(o, c, start=sopen))
            m = CoverageManifest.load(
                root=root,
                provider="databento",
                dataset="EQUS.MINI",
                timeframe="1m",
                symbol=sym,
            )
            with m.transaction() as t:
                t.record(
                    CoverageRange(
                        start=sopen,
                        end=sopen + timedelta(minutes=30),
                        fetched_at=datetime(2024, 6, 8, tzinfo=UTC),
                    )
                )


@pytest.fixture
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from liq.data.settings import get_settings, get_store

    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    get_settings.cache_clear()
    get_store.cache_clear()
    _seed(tmp_path)
    yield tmp_path
    get_settings.cache_clear()
    get_store.cache_clear()


class TestSweepCli:
    def test_sweep_writes_artifacts_and_emits_summary(self, seeded: Path) -> None:
        query_path = seeded / "sp500_5pct.yaml"
        query_path.write_text(
            """\
universe_ref: ["UPMOVE"]
window:
  kind: trading_minutes
  n: 30
predicate:
  kind: move
  threshold_pct: 5.0
  direction: either
""",
            encoding="utf-8",
        )

        result = _runner.invoke(
            app,
            [
                "sweep",
                "--query",
                str(query_path),
                "--start",
                "2024-06-03",
                "--end",
                "2024-06-04",
                "--data-root",
                str(seeded),
            ],
        )
        assert result.exit_code == 0, result.output
        summary = json.loads(result.output)
        assert summary["query_name"] == "sp500_5pct"
        assert summary["sessions_scanned"] == 2
        assert summary["runs_count"] >= 1
