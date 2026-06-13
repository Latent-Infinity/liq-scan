# `CoverageGapError` — loud fail on incomplete coverage

`ScanEngine.execute(...)` consults the per-symbol coverage manifest
before reading any bars. If any resolved symbol does not have full
coverage of the window the engine raises `CoverageGapError` with a
fully-enumerated gap list, so the operator can act without re-running
the scan.

## Shape

```python
class CoverageGapError(LiqScanError):
    missing: list[tuple[str, list[tuple[datetime, datetime]]]]
```

Each tuple is `(symbol, [(gap_start, gap_end), ...])`. Gaps are
half-open `[start, end)` ranges identical to those stored in the
liq-data `CoverageManifest`.

## CLI behavior

`liq-scan execute ...` exits with status code `2` and prints a JSON
object to **stderr**:

```json
{
  "error": "coverage_gap",
  "message": "coverage gaps for 1 symbol(s) in window 2024-06-03T13:30:00+00:00 → 2024-06-03T20:00:00+00:00",
  "missing": [
    {"symbol": "GHOST", "gaps": [["2024-06-03T13:30:00+00:00", "2024-06-03T20:00:00+00:00"]]}
  ]
}
```

Stdout stays empty so a downstream `jq` pipeline can disambiguate
"empty result" from "scan failed."

## Why a hard error, not a warning

A scan that silently drops uncovered symbols would systematically
under-represent the same names every run. The next pipeline stage
(label generation, backtest target, etc.) would then learn from a
biased subset of the universe — a "missing-data survivorship" bug
that would not surface until results disagree across runs. Failing
loud forces the operator to either backfill, narrow the universe, or
explicitly accept the partial coverage.

## How to fix in practice

1. Inspect `missing` to see exactly which symbols are short.
2. Run `liq-data sync <universe> --start <gap_start> --end <gap_end>`
   to fetch the missing range.
3. Re-run the scan.
