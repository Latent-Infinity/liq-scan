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
```

## Validation rules

* `universe_ref` must be non-empty (`""` or `[]` rejected).
* `as_of` must be timezone-aware (naive datetimes rejected).
* `limit` must be `None` or `>= 0`.

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
