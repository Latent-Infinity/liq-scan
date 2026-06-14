# `liq-scan` exception catalog

All custom exceptions live in `liq.scan.exceptions` and derive from
`LiqScanError` so a consumer can catch the entire class hierarchy
with one `except` clause.

## Hierarchy

```
LiqScanError                       — root for every liq-scan error
├── ScanValidationError            — malformed ScanQuery / predicate / template
├── CoverageGapError               — required (symbol × window) not covered
├── NonPITUniverseError            — sweep refuses non-PIT universe
├── ScanSchemaError                — persisted artifact carries unknown schema_version
└── ScanPersistenceError           — write/finalize failure on a persisted scan run
```

## When each is raised

| Exception | Where | Retry-eligible? | Recovery |
| --- | --- | --- | --- |
| `LiqScanError` | Base; concrete subclasses below | n/a | — |
| `ScanValidationError` | A `ScanQuery` or yaml template fails pydantic validation, or a config violates an invariant ("limit must be > 0", "as_of must be tz-aware"). | **No** — caller bug. | Fix the input shape. |
| `CoverageGapError` | `ScanEngine.execute(...)` finds an as-of where the coverage manifest does not span the resolved window. | **Yes** — re-run `liq-data sync` for the missing range and retry. | Inspect `exception.missing` (list of `(symbol, [(gap_start, gap_end), ...])`), backfill, re-run. |
| `NonPITUniverseError` | `ScanEngine.sweep(...)` resolves a universe whose `ResolvedUniverse.pit` is `False`. | **No** — silent survivorship bias is the very thing the rule prevents. | Either use an explicit universe or wire a PIT constituent source. See `docs/pit-policy.md`. |
| `ScanSchemaError` | `load_runs` or `load_meta` reads a persisted artifact whose `schema_version` exceeds the reader's `SCHEMA_VERSION`. | **No** — the reader is older than the writer; downgrading is not a recovery path. | Upgrade the consumer or run a forward-compatible migration. See `docs/scan-persistence.md`. |
| `ScanPersistenceError` | Reserved for hard failures while writing or finalizing a persisted scan run — used by future store-backend adapters where atomic finalization can fail. | Depends on store backend. | Inspect cause + retry; the parquet store auto-rolls back partial writes. |

## Forward-compatibility policy

`ScanSchemaError` is the gate that enforces **read-only forward
compatibility**: a newer writer may stamp a higher `schema_version`,
and older readers refuse rather than silently truncate the fields
they do not understand. Bumping `SCHEMA_VERSION` is a **breaking
change** — never bump for additive columns, only when a column's
meaning changes or an older column disappears.

## How callers should handle them

```python
from liq.scan.engine import ScanEngine
from liq.scan.exceptions import (
    CoverageGapError,
    NonPITUniverseError,
    ScanSchemaError,
)

try:
    artifacts = engine.sweep(template, config)
except NonPITUniverseError:
    log.error("non-PIT universe; refusing to bias the labels")
    raise
except CoverageGapError as exc:
    log.warning("coverage gap", extra={"missing": exc.missing})
    raise
except ScanSchemaError as exc:
    log.error("schema drift", extra={"error": str(exc)})
    raise
```
