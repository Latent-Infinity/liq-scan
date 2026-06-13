# Point-in-time policy

A sweep over a non-point-in-time universe is silently biased. By
the time a composite universe like "SP500" is resolved through the
current-only stub source, every name that's been *removed* from the
index since the as-of date is missing from the result — so the sweep
"only saw" names that survived. Bankruptcies, acquisitions, and
delistings are exactly the high-magnitude moves the scanner is built
to find, so this bias points the wrong way: it under-represents the
signal we care about.

`liq-scan` refuses to ship that data quietly.

## The rule

`ScanEngine.sweep(...)` calls `data_service.resolve_universe(...)` per
as-of and inspects `ResolvedUniverse.pit`. If `pit` is `False` at any
iteration, the sweep raises `NonPITUniverseError` **immediately** —
before any read. No partial sweep is persisted; no labelling
artifact carries non-PIT data.

`ScanEngine.execute(...)` does not enforce this — single-query
operations are an interactive/exploratory shape, and the operator
choosing to run one is opting in to the bias for one call. Sweeps
generate downstream label data, so the rule is tighter there.

## What makes a universe PIT

`UniverseDefinition` kinds:

| Kind | PIT? |
| --- | --- |
| `explicit` | Always — the operator picked the list. |
| `filter` | Inherits from the reference-data adapter (PIT today; non-PIT vendors must advertise `pit=False`). |
| `composite` | Inherits from the `ConstituentSource`. The bundled `InMemoryStubSource` is current-only (`pit=False`). |
| `set_op` | Logical AND of the inputs' PIT flags. |

The CLI's universe resolver wires the in-process `InMemoryStubSource`
by default, so any composite universe via the CLI is non-PIT — and
therefore unsweepable until a real PIT vendor is plugged in.

## Operator workflow

1. Build a composite universe via a CLI/registry call.
2. Try the sweep — it raises `NonPITUniverseError`.
3. Either:
   * Substitute an `explicit` universe (acceptable when survivorship
     is not a concern — e.g., short backtests on a hand-picked basket).
   * Wire a real PIT constituent source (Norgate, Bloomberg PIT,
     etc.) and re-run.

## Why the per-as-of check

The PIT flag is per-resolution, not per-template. A `set_op` whose
inputs are PIT today may become non-PIT later if one input swaps to a
non-PIT source. The sweep re-resolves on every iteration so the
guarantee is enforced at each step rather than only at the start.
