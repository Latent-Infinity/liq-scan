# `liq-scan` structured log catalog

`liq-scan` emits structured `logging.LogRecord` events via the
standard `logging` module. Fields are attached via the `extra=` kwarg
so they land as queryable attributes on the record rather than being
baked into the message string.

Logger: `liq.scan.engine` (INFO by default; `pit_warning` and the
`empty_universe` advisory go to WARNING).

## Correlation key

| Operation | Correlation field |
| --- | --- |
| `ScanEngine.execute(...)` | `scan_run_id` — uuid5(NAMESPACE_URL, model_dump_json(query)) |
| `ScanEngine.sweep(...)` | `correlation_id == "sweep:<sweep_id>"` |

Every event carries both a `scan_run_id` and a `correlation_id`. For
single-query operations the two are equal. For a sweep, the
`correlation_id` is shared by every event of the same sweep
(including the nested `execute` events) so an operator can
`jq 'select(.correlation_id == "sweep:abc123…")'` and reconstruct the
full timeline.

## `execute` event sequence

A successful `execute` emits, in order:

| Event | Level | Required fields |
| --- | --- | --- |
| `scan_started` | INFO | `as_of`, `include_extended_hours` |
| `universe_resolved` | INFO | `symbols_count`, `pit` |
| `empty_universe` | WARNING | (emitted only when no symbols resolve) |
| `coverage_verified` | INFO | `missing_count`, `window_start`, `window_end` |
| `read_multi` | INFO | `keys_count`, `missing_count`, `window_start`, `window_end` |
| `predicate_evaluated` | INFO | `evaluated_count`, `passed_count` |
| `scan_completed` | INFO | `result_count` |

When `coverage_verified.missing_count > 0` the engine raises
`CoverageGapError` and the sequence terminates at that event.

## `sweep` event sequence

A successful `sweep(template, config)` over N as-ofs emits:

```
sweep_started
  (sweep_resumed)                — only when resuming an interrupted run
[ universe_resolved              — once per as-of, nested execute event
  coverage_verified              — once per as-of
  read_multi                     — once per as-of
  predicate_evaluated            — once per as-of
  scan_completed                 — once per as-of
  sweep_as_of ] * N
sweep_completed
```

### `sweep_started`

| Field | Type | Description |
| --- | --- | --- |
| `event` | `str` | `"sweep_started"` |
| `correlation_id` | `str` | `"sweep:<sweep_id>"` |
| `query_name` | `str` | From `SweepConfig.query_name` |
| `sweep_id` | `str` | sha256 prefix of the config |
| `cadence` | `str` | `session_close` / `minutes_n` |
| `start`, `end` | `str` | ISO dates |

### `sweep_resumed`

Emitted **only** when persisted state covers some but not all
as-ofs. Carries the latest completed as-of plus the remaining count.

| Field | Description |
| --- | --- |
| `resumed_from` | ISO timestamp of the last completed as-of |
| `remaining` | Count of as-ofs left to scan |

### `sweep_as_of`

Emitted once per as-of after a successful `execute`. Carries the
universe hash so an operator can join against the persisted
`universes` snapshot.

| Field | Description |
| --- | --- |
| `as_of` | ISO timestamp of the iteration |
| `universe_hash` | sha256 prefix of the resolved universe |

### `sweep_completed`

Emitted exactly once per `sweep(...)` invocation.

| Field | Description |
| --- | --- |
| `sessions_scanned` | Count of as-ofs in the cadence grid |
| `sessions_with_hits` | Count of as-ofs whose `execute` returned at least one result |
| `runs_count` | Total persisted rows in the runs frame |

## Reconstructing a sweep

A single `jq` filter reconstructs the whole timeline:

```bash
jq 'select(.correlation_id == "sweep:<sweep_id>") | "\(.event)\t\(.as_of // "")"' \
  liq-scan.log
```

Output should follow this shape:

```
sweep_started
sweep_resumed         # only if resuming
sweep_as_of    2024-01-02T20:00:00+00:00
sweep_as_of    2024-01-03T20:00:00+00:00
...
sweep_completed
```

The audit-reconstruction test in `tests/test_sweep_audit.py` enforces
exactly this shape — a regression there (missing event, swapped
order, dropped correlation key) fails the verify gate.
