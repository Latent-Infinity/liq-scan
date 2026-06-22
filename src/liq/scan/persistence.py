"""Sweep persistence — runs, meta, and universe snapshots.

Three artifacts live under the key prefix
``scans/{query_name}/{sweep_id}/``:

* ``runs`` — long-format ``ScanResult`` rows + ``as_of`` (the iteration
  key). Append-with-idempotency-key on ``(as_of, symbol)``.
* ``meta`` — one-row frame: query_name, sweep_id, cadence, start, end,
  query_hash. Survives sweep restarts; used to look up "what query did
  this sweep run."
* ``universes`` — one row per unique resolved universe across the
  sweep, keyed by sha256 of the sorted symbol list. ``runs`` references
  by hash so a 250-session sweep stores each unchanged universe once.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl

from liq.scan.anchors.mean_reversion import AnchorVolSource, MeanReversionAnchor, RegimeLabel
from liq.scan.exceptions import ScanSchemaError
from liq.scan.query import ScanResult
from liq.scan.sweep import SweepConfig

if TYPE_CHECKING:
    from liq.store.parquet import ParquetStore


SCHEMA_VERSION = 1
"""Wire-format version stamped on every sweep artifact.

Bumped only on a breaking change to ``runs`` or ``meta`` shape.
Readers tolerate the same version or lower (missing column → treated
as legacy / unstamped); higher versions raise :class:`ScanSchemaError`
so a forward-compat reader never silently truncates fields it does
not understand.
"""

MEAN_REVERSION_ANCHOR_SCHEMA_VERSION = 2
"""Wire-format version for mean-reversion anchor tables."""


def sweep_key_prefix(config: SweepConfig) -> str:
    sweep_id = compute_sweep_id(config)
    return f"scans/{config.query_name}/{sweep_id}"


def completed_asofs_key(config: SweepConfig) -> str:
    return f"{sweep_key_prefix(config)}/completed_asofs"


def meta_json_path(store: ParquetStore, config: SweepConfig) -> Path:
    return store.data_root / sweep_key_prefix(config) / "meta.json"


def compute_sweep_id(config: SweepConfig) -> str:
    """Return a deterministic id derived from the config shape."""
    payload = {
        "query_name": config.query_name,
        "cadence": config.cadence,
        "start": config.start.isoformat(),
        "end": config.end.isoformat(),
        "interval_minutes": config.interval_minutes,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8"))
    return digest.hexdigest()[:16]


def universe_hash(symbols: Sequence[str]) -> str:
    """Hash a resolved-universe symbol list to a 16-char id."""
    sorted_csv = ",".join(sorted({s.upper() for s in symbols}))
    return hashlib.sha256(sorted_csv.encode("utf-8")).hexdigest()[:16]


# ----- artifact summary -----------------------------------------------------


@dataclass(frozen=True)
class SweepArtifacts:
    """Return value of :meth:`ScanEngine.sweep`."""

    query_name: str
    sweep_id: str
    runs_count: int
    sessions_scanned: int
    sessions_with_hits: int
    resumed_from: datetime | None = None


# ----- persistence helpers --------------------------------------------------


def runs_schema() -> dict[str, Any]:
    """Schema for the on-disk runs frame.

    ``timestamp`` is a deterministic copy of ``as_of`` — present so
    the underlying ``ParquetStore.write`` path picks it up as part of
    its dedup key. Without it the store dedupes on ``symbol`` alone
    and silently drops the older rows of a multi-session sweep.
    """
    return {
        "timestamp": pl.Datetime("us", "UTC"),
        "as_of": pl.Datetime("us", "UTC"),
        "symbol": pl.Utf8,
        "move_pct": pl.Float64,
        "direction": pl.Utf8,
        "window_start": pl.Datetime("us", "UTC"),
        "window_end": pl.Datetime("us", "UTC"),
        "bar_count": pl.Int64,
        "dollar_volume": pl.Utf8,
        "metric_version": pl.Utf8,
        "split_event": pl.Utf8,
        "universe_hash": pl.Utf8,
        "schema_version": pl.Int64,
    }


def completed_schema() -> dict[str, Any]:
    return {
        "timestamp": pl.Datetime("us", "UTC"),
        "as_of": pl.Datetime("us", "UTC"),
    }


def results_to_frame(results: Iterable[ScanResult], *, universe_hash_value: str) -> pl.DataFrame:
    rows = [
        {
            "timestamp": r.as_of,
            "as_of": r.as_of,
            "symbol": r.symbol,
            "move_pct": float(r.move_pct),
            "direction": r.direction,
            "window_start": r.window_actual[0],
            "window_end": r.window_actual[1],
            "bar_count": int(r.bar_count),
            "dollar_volume": str(r.dollar_volume),
            "metric_version": r.metric_version,
            "split_event": r.split_event or "",
            "universe_hash": universe_hash_value,
            "schema_version": SCHEMA_VERSION,
        }
        for r in results
    ]
    if not rows:
        return pl.DataFrame(schema=runs_schema())
    return pl.DataFrame(rows, schema=runs_schema())


def mean_reversion_anchor_schema() -> dict[str, Any]:
    return {
        "timestamp": pl.Datetime("us", "UTC"),
        "symbol": pl.Utf8,
        "anchor_ts": pl.Datetime("us", "UTC"),
        "direction": pl.Utf8,
        "anchor_event_id": pl.Utf8,
        "scan_run_id": pl.Utf8,
        "scan_query_version": pl.Utf8,
        "metric_version": pl.Utf8,
        "resolved_universe_version": pl.Utf8,
        "quality_flags": pl.Utf8,
        "excursion_units": pl.Utf8,
        "midrange_now": pl.Utf8,
        "midrange_base": pl.Utf8,
        "reversion_target": pl.Utf8,
        "vol_t": pl.Utf8,
        "anchor_vol_source": pl.Utf8,
        "regime_at_anchor": pl.Utf8,
        "schema_version": pl.Int64,
    }


def mean_reversion_anchors_to_frame(anchors: Iterable[MeanReversionAnchor]) -> pl.DataFrame:
    rows = []
    for anchor in anchors:
        rows.append(
            {
                "timestamp": anchor.anchor_ts,
                "symbol": anchor.symbol,
                "anchor_ts": anchor.anchor_ts,
                "direction": anchor.direction,
                "anchor_event_id": anchor.anchor_event_id,
                "scan_run_id": anchor.scan_run_id,
                "scan_query_version": anchor.scan_query_version,
                "metric_version": anchor.metric_version,
                "resolved_universe_version": anchor.resolved_universe_version,
                "quality_flags": json.dumps(list(anchor.quality_flags), sort_keys=True),
                "excursion_units": str(anchor.excursion_units),
                "midrange_now": str(anchor.midrange_now),
                "midrange_base": str(anchor.midrange_base),
                "reversion_target": str(anchor.reversion_target),
                "vol_t": str(anchor.vol_t),
                "anchor_vol_source": anchor.anchor_vol_source.model_dump_json(),
                "regime_at_anchor": anchor.regime_at_anchor or "",
                "schema_version": MEAN_REVERSION_ANCHOR_SCHEMA_VERSION,
            }
        )
    if not rows:
        return pl.DataFrame(schema=mean_reversion_anchor_schema())
    return pl.DataFrame(rows, schema=mean_reversion_anchor_schema())


def _regime_label(value: object) -> RegimeLabel | None:
    if value == "trend":
        return "trend"
    if value == "chop":
        return "chop"
    if value == "indeterminate":
        return "indeterminate"
    return None


def frame_to_mean_reversion_anchors(frame: pl.DataFrame) -> list[MeanReversionAnchor]:
    stamped = _check_and_stamp(
        frame,
        artifact="mean_reversion_anchors",
        reader_version=MEAN_REVERSION_ANCHOR_SCHEMA_VERSION,
    )
    anchors: list[MeanReversionAnchor] = []
    for row in stamped.iter_rows(named=True):
        flags_raw = row.get("quality_flags") or "[]"
        source_raw = row.get("anchor_vol_source") or "{}"
        anchors.append(
            MeanReversionAnchor(
                symbol=str(row["symbol"]),
                anchor_ts=row["anchor_ts"],
                direction=row["direction"],
                anchor_event_id=str(row["anchor_event_id"]),
                scan_run_id=str(row["scan_run_id"]),
                scan_query_version=str(row["scan_query_version"]),
                metric_version=str(row["metric_version"]),
                resolved_universe_version=str(row["resolved_universe_version"]),
                quality_flags=tuple(str(flag) for flag in json.loads(str(flags_raw))),
                excursion_units=Decimal(str(row["excursion_units"])),
                midrange_now=Decimal(str(row["midrange_now"])),
                midrange_base=Decimal(str(row["midrange_base"])),
                reversion_target=Decimal(str(row["reversion_target"])),
                vol_t=Decimal(str(row["vol_t"])),
                anchor_vol_source=AnchorVolSource.model_validate_json(str(source_raw)),
                regime_at_anchor=_regime_label(row["regime_at_anchor"]),
            )
        )
    return anchors


def write_schema_manifest(
    path: Path,
    *,
    schema_version: int,
    table_name: str,
    fields: Sequence[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": schema_version,
        "table_name": table_name,
        "fields": list(fields),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_mean_reversion_anchors(
    anchors: Iterable[MeanReversionAnchor],
    *,
    store: ParquetStore,
    table_name: str = "scans/mean_reversion/anchors",
) -> str:
    frame = mean_reversion_anchors_to_frame(anchors)
    store.write(table_name, frame, mode="overwrite")
    write_schema_manifest(
        store.data_root / table_name / "meta.json",
        schema_version=MEAN_REVERSION_ANCHOR_SCHEMA_VERSION,
        table_name="mean_reversion_anchors",
        fields=list(mean_reversion_anchor_schema()),
    )
    return table_name


def read_mean_reversion_anchors(
    *,
    store: ParquetStore,
    table_name: str = "scans/mean_reversion/anchors",
) -> list[MeanReversionAnchor]:
    if not store.exists(table_name):
        return []
    return frame_to_mean_reversion_anchors(store.read(table_name))


def append_runs(store: ParquetStore, config: SweepConfig, frame: pl.DataFrame) -> None:
    """Idempotent append: dedupe on ``(as_of, symbol)`` so a resume
    that re-emits a session collapses to one row."""
    if frame.is_empty():
        return
    key = f"{sweep_key_prefix(config)}/runs"
    existing = store.read(key) if store.exists(key) else pl.DataFrame()
    merged = (
        pl.concat([existing, frame], how="diagonal_relaxed") if not existing.is_empty() else frame
    )
    merged = merged.unique(subset=["as_of", "symbol"], keep="last").sort(["as_of", "symbol"])
    store.write(key, merged, mode="overwrite")


def load_runs(store: ParquetStore, config: SweepConfig) -> pl.DataFrame:
    key = f"{sweep_key_prefix(config)}/runs"
    if not store.exists(key):
        return pl.DataFrame(schema=runs_schema())
    frame = store.read(key)
    return _check_and_stamp(frame, artifact="runs")


def load_meta(store: ParquetStore, config: SweepConfig) -> dict[str, Any]:
    """Return the meta parquet row after validating its schema version."""
    key = f"{sweep_key_prefix(config)}/meta"
    if not store.exists(key):
        raise ScanSchemaError(
            f"meta key missing for {config.query_name!r}/{compute_sweep_id(config)}"
        )
    frame = store.read(key)
    stamped = _check_and_stamp(frame, artifact="meta")
    return stamped.row(0, named=True)


def load_meta_json(store: ParquetStore, config: SweepConfig) -> dict[str, Any]:
    """Return the operator JSON sidecar after validating its schema version."""
    path = meta_json_path(store, config)
    if not path.exists():
        raise ScanSchemaError(
            f"meta.json missing for {config.query_name!r}/{compute_sweep_id(config)}"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    version = payload.get("schema_version", SCHEMA_VERSION)
    if int(version) > SCHEMA_VERSION:
        raise ScanSchemaError(
            f"meta.json schema_version={version} exceeds reader version {SCHEMA_VERSION}"
        )
    return payload


def _check_and_stamp(
    frame: pl.DataFrame,
    *,
    artifact: str,
    reader_version: int = SCHEMA_VERSION,
) -> pl.DataFrame:
    """Validate ``schema_version`` and inject it back when missing.

    Forward-compat policy: a stamped artifact with ``schema_version >
    SCHEMA_VERSION`` raises :class:`ScanSchemaError`. A legacy
    unstamped artifact is accepted and tagged with the current
    version on read so consumers see a consistent column.
    """
    if "schema_version" not in frame.columns:
        return frame.with_columns(pl.lit(reader_version).cast(pl.Int64).alias("schema_version"))
    versions = frame["schema_version"].drop_nulls().unique().to_list()
    for v in versions:
        if int(v) > reader_version:
            raise ScanSchemaError(
                f"{artifact} schema_version={v} exceeds reader version {reader_version}"
            )
    return frame


def write_meta(
    store: ParquetStore,
    config: SweepConfig,
    *,
    query_hash: str,
    template_json: str | None = None,
    data_version_hash: str | None = None,
) -> None:
    sweep_id = compute_sweep_id(config)
    row = {
        "query_name": config.query_name,
        "sweep_id": sweep_id,
        "cadence": config.cadence,
        "start": config.start.isoformat(),
        "end": config.end.isoformat(),
        "interval_minutes": config.interval_minutes or 0,
        "query_hash": query_hash,
        "data_version_hash": data_version_hash or "",
        "schema_version": SCHEMA_VERSION,
    }
    store.write(
        f"{sweep_key_prefix(config)}/meta",
        pl.DataFrame([row]),
        mode="overwrite",
    )
    payload = {
        **row,
        "query_template": json.loads(template_json) if template_json else None,
        "config": {
            "query_name": config.query_name,
            "cadence": config.cadence,
            "start": config.start.isoformat(),
            "end": config.end.isoformat(),
            "interval_minutes": config.interval_minutes,
        },
    }
    path = meta_json_path(store, config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def upsert_universe_snapshot(
    store: ParquetStore,
    config: SweepConfig,
    *,
    as_of: datetime,
    symbols: Sequence[str],
) -> str:
    """Persist a universe snapshot for ``as_of``; return its hash.

    Hash-keyed snapshots collapse unchanged universes to one row even
    across long sweeps.
    """
    h = universe_hash(symbols)
    key = f"{sweep_key_prefix(config)}/universes"
    existing = store.read(key) if store.exists(key) else pl.DataFrame()
    row = pl.DataFrame(
        [
            {
                "as_of": as_of,
                "universe_hash": h,
                "symbols_csv": ",".join(sorted({s.upper() for s in symbols})),
            }
        ],
        schema={
            "as_of": pl.Datetime("us", "UTC"),
            "universe_hash": pl.Utf8,
            "symbols_csv": pl.Utf8,
        },
    )
    merged = pl.concat([existing, row], how="diagonal_relaxed") if not existing.is_empty() else row
    merged = merged.unique(subset=["as_of"], keep="last").sort("as_of")
    store.write(key, merged, mode="overwrite")
    return h


def mark_as_of_complete(store: ParquetStore, config: SweepConfig, as_of: datetime) -> None:
    key = completed_asofs_key(config)
    existing = store.read(key) if store.exists(key) else pl.DataFrame(schema=completed_schema())
    row = pl.DataFrame(
        [{"timestamp": as_of, "as_of": as_of}],
        schema=completed_schema(),
    )
    merged = pl.concat([existing, row], how="diagonal_relaxed") if not existing.is_empty() else row
    merged = merged.unique(subset=["as_of"], keep="last").sort("as_of")
    store.write(key, merged, mode="overwrite")


def list_persisted_as_ofs(store: ParquetStore, config: SweepConfig) -> list[datetime]:
    completed_key = completed_asofs_key(config)
    if store.exists(completed_key):
        completed = store.read(completed_key)
        if not completed.is_empty():
            return sorted({row["as_of"] for row in completed.select("as_of").iter_rows(named=True)})
    runs = load_runs(store, config)
    if runs.is_empty():
        return []
    return sorted({row["as_of"] for row in runs.select("as_of").iter_rows(named=True)})


def sweep_data_version_hash(store: ParquetStore, config: SweepConfig) -> str:
    """Hash persisted universe/completion snapshots for meta reproducibility."""
    pieces: list[str] = []
    for suffix in ("universes", "completed_asofs"):
        key = f"{sweep_key_prefix(config)}/{suffix}"
        if not store.exists(key):
            continue
        frame = store.read(key)
        if frame.is_empty():
            continue
        pieces.append(frame.sort(frame.columns).write_csv())
    return hashlib.sha256("\n".join(pieces).encode("utf-8")).hexdigest()[:16]


__all__ = [
    "MEAN_REVERSION_ANCHOR_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "SweepArtifacts",
    "append_runs",
    "completed_asofs_key",
    "completed_schema",
    "compute_sweep_id",
    "list_persisted_as_ofs",
    "load_meta",
    "frame_to_mean_reversion_anchors",
    "load_meta_json",
    "load_runs",
    "mean_reversion_anchor_schema",
    "mean_reversion_anchors_to_frame",
    "mark_as_of_complete",
    "meta_json_path",
    "read_mean_reversion_anchors",
    "results_to_frame",
    "runs_schema",
    "sweep_data_version_hash",
    "sweep_key_prefix",
    "universe_hash",
    "upsert_universe_snapshot",
    "write_mean_reversion_anchors",
    "write_meta",
    "write_schema_manifest",
]
