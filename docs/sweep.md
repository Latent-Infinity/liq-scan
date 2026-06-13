# `ScanEngine.sweep` — historical sweeps

`sweep` runs one `ScanQueryTemplate` across a range of as-of
timestamps and persists every result. The template is the
as-of-less form of `ScanQuery`; the sweep iteration picks the
as-ofs per cadence and builds one concrete `ScanQuery` per
iteration.

## Contract

```python
def sweep(
    template: ScanQueryTemplate,
    config: SweepConfig,
) -> SweepArtifacts
```

* `template` — `ScanQueryTemplate` (universe_ref + window + predicate
  + ranking + limit + metric_version + split_handling). No
  `as_of` field.
* `config` — `SweepConfig(query_name, cadence, start, end,
  interval_minutes=None)`.
* Returns `SweepArtifacts(query_name, sweep_id, runs_count,
  sessions_scanned, sessions_with_hits, resumed_from)`.

## Cadence

| Value | Iteration rule |
| --- | --- |
| `session_close` | One as-of per NYSE regular session in `[start, end]` — uses each session's close timestamp (UTC). |
| `minutes_n` | One as-of every `interval_minutes` from `start` 00:00 UTC through `end` 00:00 UTC. Requires `--interval-minutes`. |

Sessions outside the NYSE calendar (weekends, holidays) are skipped
automatically for `session_close`; the cadence does **not** force a
synthetic close on non-trading days.

## CLI

```bash
liq-scan sweep \
  --query queries/sp500_5pct.yaml \
  --start 2024-01-02 \
  --end 2024-12-31 \
  --data-root ./data \
  --output json
```

Stdout is the JSON summary (`runs_count`, `sessions_scanned`, etc.);
stderr carries structured logs. The yaml template shape mirrors
`ScanQueryTemplate`; see `docs/scan-query.md` for the field semantics
and `docs/predicates.md` for the predicate combinators.

## Persistence

Three artifacts live under `scans/{query_name}/{sweep_id}/`:

* `runs` — long-format `ScanResult` rows + `as_of`.
* `meta.json` plus `meta` — query/config metadata, query hash, and
  data-version hash.
* `universes` — one row per as-of resolved universe, keyed by
  sha256(symbols).
* `completed_asofs` — one row per successfully completed as-of, even
  when the predicate produced no hits.

See `docs/scan-persistence.md` for the on-disk shape and idempotency
rules.

## Resumability

A sweep that's interrupted mid-range and re-invoked picks up from the
next uncompleted as-of. Completion is tracked independently from
result rows, so no-hit sessions are not reprocessed on resume.
Idempotency keys are `(as_of, symbol)`, so a partial-then-resume run
produces exactly the same rows as a single uninterrupted run.
`SweepArtifacts.resumed_from` is the latest completed as-of when the
resume happened — `None` on first runs.

## Reproducibility

Two consecutive sweeps over identical inputs (template, cadence,
range, store contents) produce a byte-identical `runs` frame — the
foundation of the regression-test harness that asserts no silent drift
in the move metric or predicate evaluation.

## PIT enforcement

A sweep over a non-point-in-time universe (e.g., a composite resolved
through the stub source) raises `NonPITUniverseError` immediately,
before any read. See `docs/pit-policy.md` for the rationale.
