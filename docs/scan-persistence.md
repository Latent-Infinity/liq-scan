# Sweep persistence layout

Every sweep persists three artifacts under one folder in the store:

```
scans/
  {query_name}/
    {sweep_id}/
      runs/         # long-format ScanResult rows
      meta/         # one-row parquet metadata
      meta.json     # stable JSON metadata
      universes/    # per-as-of universe snapshots
      completed_asofs/ # completed as-of markers, including no-hit sessions
```

`query_name` is human-readable; `sweep_id` is a deterministic 16-char
sha256 prefix of the `SweepConfig` so the same config always lands in
the same folder.

## `runs`

One row per (as_of, symbol) that passed the predicate. The store's
existing parquet path holds it under `runs/data.parquet`.

| Column | Type | Notes |
| --- | --- | --- |
| `timestamp` | `Datetime("us", "UTC")` | Equal to `as_of` — included so the store's append-time dedup uses `(timestamp, symbol)` rather than dropping older rows |
| `as_of` | `Datetime("us", "UTC")` | The sweep iteration timestamp |
| `symbol` | `Utf8` | |
| `move_pct` | `Float64` | |
| `direction` | `Utf8` | `up` / `down` / `flat` |
| `window_start` | `Datetime("us", "UTC")` | `query.window.resolve(as_of)` lower bound |
| `window_end` | `Datetime("us", "UTC")` | Upper bound (`= as_of` for `trading_minutes` window) |
| `bar_count` | `Int64` | |
| `dollar_volume` | `Utf8` | Stored as decimal string to preserve precision |
| `metric_version` | `Utf8` | `"midrange-endpoint-v1"` by default |
| `split_event` | `Utf8` | `""` when no split overlapped the window |
| `universe_hash` | `Utf8` | 16-char sha256 of the as-of's resolved universe; foreign-key into `universes` |

## `meta` and `meta.json`

`meta` is a one-row parquet frame with the deterministic sweep config
snapshot. `meta.json` carries the same fields plus the full
as-of-less query template and config payload for operator inspection.

| Column | Notes |
| --- | --- |
| `query_name` | From `SweepConfig.query_name` |
| `sweep_id` | sha256 prefix of the config |
| `cadence` | `session_close` or `minutes_n` |
| `start` / `end` | ISO dates |
| `interval_minutes` | 0 when cadence is `session_close` |
| `query_hash` | sha256 prefix of the `ScanQueryTemplate` model JSON |
| `data_version_hash` | sha256 prefix over persisted universe/completion snapshots |

## `universes`

One row per as-of resolved universe across the sweep:

| Column | Notes |
| --- | --- |
| `as_of` | Sweep timestamp that observed this universe |
| `universe_hash` | 16-char sha256 of the sorted symbol list |
| `symbols_csv` | Comma-separated uppercase symbols |

`runs.universe_hash` points to the membership snapshot used for that
result row.

## `completed_asofs`

One row per as-of that completed successfully, regardless of whether
the predicate produced hits:

| Column | Notes |
| --- | --- |
| `timestamp` | Equal to `as_of`, present for store partitioning/dedup |
| `as_of` | Completed sweep timestamp |

## Idempotency + resumability

The runs writer dedupes on `(as_of, symbol, keep="last")` before
flushing — so a session that's re-emitted (resume, retry) collapses
to one row. `ScanEngine.sweep` skips as-ofs already present in
`completed_asofs.as_of` before issuing the next read, so resume picks
up exactly where it stopped, including sessions with zero hits.

The fingerprint that makes resumes safe is the deterministic
`sweep_id`: two `SweepConfig` instances with identical fields always
hash to the same id, and therefore the same folder.
