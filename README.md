# liq-scan

Cross-sectional universe screening for the LIQ Stack.

> **Status:** implementation-ready for generic cross-sectional scanning.
> `execute` and `sweep` are implemented with predicate evaluation,
> coverage checks, schema-versioned persistence, and a Typer CLI. See
> [`../liq-docs/plans/liq-scan-plan.md`](../liq-docs/plans/liq-scan-plan.md)
> for the delivery plan and
> [`../liq-docs/requirements/liq-scan-requirements.md`](../liq-docs/requirements/liq-scan-requirements.md)
> for the requirements spec. Product-complete operation still requires
> an operator-approved production sweep and provider spend gate.

## What this library does

`liq-scan` answers one question: **"Which symbols satisfy predicate P
over window W as of time T?"** — across a curated, named universe,
backed by bars in `liq-store`.

Examples:

- *"Which S&P 500 names moved ±5% over the trailing session as-of
  2024-06-03 close?"*
- *"Run that same scan as-of every session close from 2023-03-28 to
  present; persist all triggers as label anchors."*

It is **not** an indicator library (that's `liq-features`), an
acquisition library (that's `liq-data`), or a signal generator
(that's `liq-signals`). It is pure cross-sectional discovery.

## Position in the stack

```
liq-data      ← provides universe definitions + bars + calendar
liq-store     ← provides cross-sectional read_multi
liq-features  ← optional, for feature-based predicates (v2)
   │
   └──→ liq-scan ──→ persisted ScanResults (in liq-store)
```

`liq-scan` is **public** (MIT) and depends only on other public LIQ
libraries; see [the dependency graph](../liq-docs/architecture/liq-stack-spec.md#dependency-matrix).

## Quickstart

```bash
# Run a single scan and emit ranked ScanResult rows as JSON.
liq-scan execute \
  --universe sp500 \
  --as-of 2024-06-03T20:00:00+00:00 \
  --window trading_minutes:390 \
  --threshold 5 \
  --direction either \
  --output json

# Run a persisted historical sweep from a ScanQueryTemplate YAML file.
liq-scan sweep \
  --query queries/mean-reversion.yaml \
  --start 2024-01-01 \
  --end 2024-12-31 \
  --cadence session_close \
  --output json
```

## Development

```bash
uv sync
uv run pytest -q
uv run ruff format --check src tests
uv run ruff check src tests
uv run ty check src
```

See `AGENT_STATE.md` for the current repo state under the liq-scan
plan.

## License

MIT — see `LICENSE`.
