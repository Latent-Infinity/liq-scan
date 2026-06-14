# `ScanQuery`

`ScanQuery` is the operator-facing input to `ScanEngine.execute(...)`.
It's a frozen Pydantic model, so it hashes, compares, and round-trips
through JSON with no ceremony.

## Shape

```python
class ScanQuery(BaseModel):
    universe_ref: str | list[str]      # name from the registry or explicit list
    as_of: datetime                    # tz-aware
    window: WindowSpec                 # see docs/window-spec.md
    predicate: AnyPredicate            # see docs/predicates.md
    ranking: Literal["abs_move", "up", "down"] = "abs_move"
    limit: int | None = None
    include_extended_hours: bool = False
    metric_version: str = "midrange-endpoint-v1"
    split_handling: Literal["adjust", "exclude"] = "adjust"
    tradable_ref: str | list[str] | None = None   # optional execution gate
```

## Validation rules

* `universe_ref` must be non-empty (`""` or `[]` rejected).
* `as_of` must be timezone-aware (naive datetimes rejected).
* `limit` must be `None` or `>= 0`.
* `tradable_ref` (when set) must be non-empty — the absence of a tradable
  set is communicated by leaving the field `None`, not by passing an
  empty list.

## Research vs. tradable scope

`universe_ref` is the **research scope** — the symbols we study and
pull data for. `tradable_ref` is the optional **execution policy** —
the symbols we'd actually act on (broker coverage, internal allow-list,
etc.).

The engine resolves both via `DataService.resolve_universe(...)`. After
rank + limit run against the research set, results outside the
tradable set are dropped and a `tradable_filtered` log event fires
with `kept` / `dropped` counts. Filtering happens **after** ranking
intentionally: a top-K query with a tradable filter returns at most K
rows minus the non-tradable names, *not* the top-K of the tradable
subset (otherwise `#K+1` would silently backfill and the ranking shape
would no longer be honest).

Per-broker tradable lists compose through the same registry
primitives — e.g.:

```bash
# Trade list (per broker).
liq-data universe create --name tradable-tradestation --kind explicit --symbols NVDA,AAPL,…
liq-data universe create --name tradable-ibkr        --kind explicit --symbols NVDA,AAPL,PLTR,…

# Research-scope intersection.
liq-data universe create \
  --name ai-chip-tradable-tradestation \
  --kind set_op --op intersect \
  --inputs ai-chip,tradable-tradestation
```

Then:

```bash
liq-scan execute \
  --universe ai-chip \
  --tradable ai-chip-tradable-tradestation \
  --as-of 2026-06-13T20:00:00Z \
  --window trading_minutes:390 \
  --threshold 5
```

See `liq-data/docs/universes.md` for the underlying set-op primitives.

## Ranking semantics

| Value | Sort key | Direction filter (when set in `predicate`) |
| --- | --- | --- |
| `abs_move` | `|move_pct|` descending | `direction=either` |
| `up` | `move_pct` descending | `direction=up` |
| `down` | `move_pct` ascending | `direction=down` |

The predicate's `direction` and the query's `ranking` are independent;
the CLI sets them in sync as a convenience.

## Output

`ScanEngine.execute(query)` returns `list[ScanResult]` ranked per
`query.ranking` and truncated to `query.limit`. See
`docs/coverage-gap.md` for the loud-fail path when coverage is
incomplete.
