"""CLI entry point for ``liq-scan``.

``liq-scan execute`` runs a single :class:`ScanQuery` and prints
the ranked ``ScanResult`` list to stdout as JSON. Structured logs
go to stderr. ``--help`` / ``--version`` return 0.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast

import typer

from liq.scan import __version__
from liq.scan.exceptions import CoverageGapError

app = typer.Typer(
    name="liq-scan",
    help="Cross-sectional universe screening for the LIQ Stack.",
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"liq-scan {__version__}")
        raise typer.Exit()


@app.callback()
def root(
    _version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the liq-scan version and exit.",
    ),
) -> None:
    """liq-scan CLI root."""


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _build_engine(data_root: Path) -> Any:
    """Construct DataService + ParquetStore + ScanEngine wired together."""
    from liq.data.service import DataService
    from liq.data.settings import get_settings, get_store
    from liq.data.universes import UniverseRegistry
    from liq.scan.engine import ScanEngine
    from liq.store.parquet import ParquetStore

    os.environ["DATA_ROOT"] = str(data_root)
    get_settings.cache_clear()
    get_store.cache_clear()

    data_service = DataService()
    store = ParquetStore(str(data_root))
    registry = UniverseRegistry(data_root)
    return ScanEngine(data_service=data_service, store=store, registry=registry)


@app.command("execute")
def execute(
    universe: str = typer.Option(
        ..., "--universe", help="Universe name or comma-separated symbols."
    ),
    as_of: str = typer.Option(..., "--as-of", help="ISO timestamp (tz-aware)."),
    window: str = typer.Option(..., "--window", help="Window spec (e.g. 'trading_minutes:390')."),
    threshold: float = typer.Option(5.0, "--threshold", help="Move-percent threshold."),
    direction: str = typer.Option("either", "--direction", help="up|down|either"),
    limit: int | None = typer.Option(None, "--limit"),
    tradable: str | None = typer.Option(
        None,
        "--tradable",
        help=(
            "Optional tradable-universe name or comma-separated symbols. "
            "Filters results down to symbols you'd actually trade — applied "
            "after rank + limit."
        ),
    ),
    data_root: Path | None = typer.Option(
        None,
        "--data-root",
        help="Root directory for parquet store + manifest (default: ./data).",
    ),
    output: str = typer.Option("json", "--output", help="json|table (table is a fallback)."),
) -> None:
    """Run one ScanQuery and emit the ranked result list."""
    from liq.scan.predicates import MovePredicate
    from liq.scan.query import ScanQuery
    from liq.scan.window import parse_window_spec

    if direction == "up":
        predicate = MovePredicate(threshold_pct=threshold, direction="up")
    elif direction == "down":
        predicate = MovePredicate(threshold_pct=threshold, direction="down")
    elif direction == "either":
        predicate = MovePredicate(threshold_pct=threshold, direction="either")
    else:
        typer.echo(f"direction must be up|down|either; got {direction!r}", err=True)
        raise typer.Exit(code=1)

    def _parse_ref(text: str) -> str | list[str]:
        return [s.strip().upper() for s in text.split(",") if s.strip()] if "," in text else text

    universe_ref: str | list[str] = _parse_ref(universe)
    tradable_ref: str | list[str] | None = _parse_ref(tradable) if tradable else None
    as_of_dt = datetime.fromisoformat(as_of)
    window_spec = parse_window_spec(window)
    ranking = "abs_move" if direction == "either" else "up" if direction == "up" else "down"
    query = ScanQuery(
        universe_ref=universe_ref,
        as_of=as_of_dt,
        window=window_spec,
        predicate=predicate,
        ranking=ranking,
        limit=limit,
        tradable_ref=tradable_ref,
    )

    engine = _build_engine(data_root if data_root is not None else Path.cwd() / "data")

    try:
        results = engine.execute(query)
    except CoverageGapError as exc:
        payload = {
            "error": "coverage_gap",
            "message": str(exc),
            "missing": [
                {"symbol": sym, "gaps": [[s.isoformat(), e.isoformat()] for s, e in gaps]}
                for sym, gaps in exc.missing
            ],
        }
        typer.echo(json.dumps(payload), err=True)
        raise typer.Exit(code=2) from exc

    if output == "json":
        rows = [r.model_dump(mode="json") for r in results]
        typer.echo(json.dumps(rows, default=_json_default))
    else:
        for r in results:
            print(f"{r.symbol}\t{r.move_pct:+.2f}%\t{r.direction}", file=sys.stdout)


@app.command("sweep")
def sweep_cmd(
    query: Path = typer.Option(..., "--query", help="Path to yaml ScanQueryTemplate."),
    start: str = typer.Option(..., "--start", help="ISO date (inclusive)."),
    end: str = typer.Option(..., "--end", help="ISO date (inclusive)."),
    query_name: str | None = typer.Option(
        None, "--name", help="Sweep folder name; default = yaml file stem."
    ),
    cadence: str = typer.Option("session_close", "--cadence", help="session_close|minutes_n"),
    interval_minutes: int | None = typer.Option(
        None, "--interval-minutes", help="Required for cadence=minutes_n."
    ),
    data_root: Path | None = typer.Option(None, "--data-root"),
    output: str = typer.Option("json", "--output", help="json|table"),
) -> None:
    """Run a historical sweep of a ScanQueryTemplate."""
    from datetime import date as _date

    from liq.scan.exceptions import NonPITUniverseError
    from liq.scan.sweep import SweepConfig
    from liq.scan.template_loader import load_query_template

    if cadence not in ("session_close", "minutes_n"):
        typer.echo(f"cadence must be session_close|minutes_n; got {cadence!r}", err=True)
        raise typer.Exit(code=1)

    template = load_query_template(query)
    config = SweepConfig(
        query_name=query_name or query.stem,
        cadence=cast(Literal["session_close", "minutes_n"], cadence),
        start=_date.fromisoformat(start),
        end=_date.fromisoformat(end),
        interval_minutes=interval_minutes,
    )

    engine = _build_engine(data_root if data_root is not None else Path.cwd() / "data")

    try:
        artifacts = engine.sweep(template, config)
    except NonPITUniverseError as exc:
        typer.echo(json.dumps({"error": "non_pit_universe", "message": str(exc)}), err=True)
        raise typer.Exit(code=3) from exc

    summary = {
        "query_name": artifacts.query_name,
        "sweep_id": artifacts.sweep_id,
        "runs_count": artifacts.runs_count,
        "sessions_scanned": artifacts.sessions_scanned,
        "sessions_with_hits": artifacts.sessions_with_hits,
        "resumed_from": (artifacts.resumed_from.isoformat() if artifacts.resumed_from else None),
    }
    if output == "json":
        typer.echo(json.dumps(summary, default=_json_default))
    else:
        for k, v in summary.items():
            print(f"{k}: {v}", file=sys.stdout)


def main() -> None:
    """Console-script entry point declared in ``pyproject.toml``."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
