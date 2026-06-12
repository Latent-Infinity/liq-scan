# liq-scan documentation

Per-feature docs land here as the library grows. None of the docs
listed below are written yet — they appear as the corresponding code
lands.

| Doc | Subject |
| --- | --- |
| `scan-query.md` | `ScanQuery` contract |
| `predicates.md` | Predicate combinators (move / dollar-volume / price) |
| `window-spec.md` | `WindowSpec` discriminated union + calendar semantics |
| `coverage-gap.md` | `CoverageGapError` payload + loud-fail policy |
| `sweep.md` | Historical sweep mode + per-as-of universe resolution |
| `scan-persistence.md` | Persisted scan-run layout in `liq-store` |
| `pit-policy.md` | Point-in-time correctness rules |
| `exceptions.md` | Exception hierarchy reference (appended on each hardening pass) |
| `logging.md` | Structured log event catalog (appended on each hardening pass) |

This directory is intentionally empty save for this placeholder while
the library is still scaffolding.
