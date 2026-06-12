# liq-scan

Cross-sectional universe screening for the LIQ Stack.

> **Status:** scaffolding only — package importable, CLI wired, no
> business logic yet. See
> [`../liq-docs/plans/liq-scan-plan.md`](../liq-docs/plans/liq-scan-plan.md)
> for the delivery plan and
> [`../liq-docs/requirements/liq-scan-requirements.md`](../liq-docs/requirements/liq-scan-requirements.md)
> for the requirements spec.

## What this library does

`liq-scan` answers one question: **"Which symbols satisfy predicate P
over window W as of time T?"** — across a curated, named universe,
backed by bars in `liq-store`.

Examples (target shape, not built yet):

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

> _The `execute` (single query) and `sweep` (historical) subcommands
> are planned, not yet implemented. The CLI is wired but currently
> exposes only `--help`._

```bash
# Future shape (not yet implemented):
liq-scan execute \
  --universe sp500 \
  --as-of 2024-06-03T20:00:00Z \
  --window trading_minutes:390 \
  --threshold 5 \
  --direction either \
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
