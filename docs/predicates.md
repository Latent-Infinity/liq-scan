# Predicates

Predicates evaluate a per-symbol summary (`PredicateInput`) and return
`True` if the symbol should appear in the ranked output. They compose
through `AndPredicate`, so a query can mix multiple thresholds without
growing query keywords.

## `PredicateInput`

```python
class PredicateInput(BaseModel):
    symbol: str
    move_pct: float           # signed; "up" is positive
    dollar_volume: Decimal
    price: Decimal
    bar_count: int
```

The engine assembles one `PredicateInput` per symbol after computing
the midrange-endpoint move metric (`metric_version`).

## Concrete predicates

### `MovePredicate(threshold_pct, direction, k=5)`

Pass when the signed move clears `threshold_pct`:

| `direction` | Pass condition |
| --- | --- |
| `up` | `move_pct >= threshold_pct` |
| `down` | `move_pct <= -threshold_pct` |
| `either` | `abs(move_pct) >= threshold_pct` |

`k` is the endpoint-aggregate window: open = mean of first `k` bars,
close = mean of last `k` bars. Default `k=5`.

### `DollarVolumePredicate(min_usd)`

Pass when `dollar_volume >= min_usd`. Useful for filtering out thin
names that satisfy a price move purely on noise.

### `PricePredicate(min_usd)`

Pass when `price >= min_usd`. Typical penny-stock exclusion filter.

## `AndPredicate(predicates=[...])`

Logical AND across child predicates. An empty child list is the
universal predicate (passes every row). Evaluation short-circuits on
the first `False`.

## Why no `OrPredicate`?

Cross-sectional sweeps almost always tighten predicates as the
universe grows; OR would loosen them. The pattern hasn't been needed
yet — if a real use case appears, the combinator slots in alongside
`AndPredicate` without changing other predicates.
