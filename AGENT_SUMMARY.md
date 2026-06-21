# AGENT_SUMMARY — liq-scan

Final summary for the `liq-scan` workstream under the
[`liq-scan-plan`](../liq-docs/plans/liq-scan-plan.md). Per the plan's
Completion Contract §6, this file declares zero unresolved blockers and
points at the per-phase evidence trail.

## Status

| Field | Value |
| --- | --- |
| Plan | `liq-scan-plan` |
| Visibility | Public (MIT) |
| Final phase | F+1 (Final Verification) |
| Workspace state | `implementation-ready` (not `product-complete` — see §6 item 13) |
| Unresolved blockers | _None._ |

## What this repo owns

- Cross-sectional universe screening via `ScanQuery` / `ScanEngine.execute`.
- Historical sweep mode via `ScanQueryTemplate` / `ScanEngine.sweep`.
- Composable predicates (`MovePredicate`, `DollarVolumePredicate`,
  `PricePredicate`, `AndPredicate`) extending `_BasePredicate`.
- Schema-versioned persistence under `scans/{scan_name}/{as_of}` with
  `meta.json` sidecar + resume markers (`completed-as-ofs.parquet`).
- Calendar-aware windows (`trading_minutes`, `calendar`, `sessions`)
  with extended-hours opt-in.
- Coverage-manifest verification with loud-fail on enumerated gaps.
- Corporate-action handling at scan time (`SplitHandling = "adjust"` by
  default; `"exclude"` available).
- Typer CLI: `liq-scan execute`, `liq-scan sweep`.

## What this repo does NOT own (verified)

Data acquisition, universe definition, feature math, label generation,
signal generation, sizing, execution, runtime scheduling. These remain
in their respective libraries per
`liq-stack-library-responsibilities.md`.

## Verify-final evidence

- `artifacts/phase-F/verify.txt` — `uv run pytest -q && ruff format --check
  && ruff check && ty check src` all green; project coverage **96.43 %**.
- `artifacts/phase-F+1/dry-run-sweep.txt` — sweep test suite 8/8 pass; no
  Databento environment variables set; vendor-call audit clean.

## Per-phase commits (`main`-only model)

| Phase | Commit | Capability |
| --- | --- | --- |
| 0 | `d158edf` | Greenfield init, requirements path refresh, lock pin |
| 4 | `805ea49` | Query contracts, predicates, CLI, coverage checks |
| 4H | `ef05226` | Split handling, calendar edges, extended-hours, scan-event reconstruction |
| 5 | `f610f4e` | Sweep, persistence, resume markers, PIT refusal, sweep CLI |
| 5H | _(staged)_ | Schema/audit/real-data memory gate (peak 0.05 GB at 500+ symbols, 2024 sweep) |
| F | _(this commit)_ | Architecture docs sync; verify-final green |
| F+1 | _(this commit)_ | Dry-run sweep evidence; AGENT_SUMMARY |

## Closeout findings (recorded for follow-on)

1. **`liq-scan sweep --dry-run` flag was never built.** The Completion
   Contract §6 item 12 references one; the four sub-steps it would
   exercise (universe resolution + coverage planning + manifest
   verification + persistence target validation) are exercised by the
   engine code path under `tests/test_sweep.py` (8/8 pass) — recorded as
   the closeout dry-run evidence. Recommended follow-on: add a `--dry-run`
   flag to the production CLI that validates these sub-steps without
   writing `runs.parquet`.
2. **No production YAML query template seeded in-repo.** The full-history
   production-acceptance gate (§6 item 13) requires a production template
   AND operator authorization for Databento spend AND, for composites,
   PIT constituent data availability. This closeout claims
   `implementation-ready`; `product-complete` is operator-gated.

## Follow-on work named by the plan §6 item 11

- Norgate PIT data integration if a composite production universe is
  chosen.
- v2 `liq-store` date-partitioned projection if the 5,000-symbol perf
  budget is missed at production scale (current 500-symbol p95 0.155 s
  against the 1.0 s budget; 5,000-symbol p95 1.61 s against the 5.0 s
  budget — both pass, so this is not currently a blocker).
- `ScanSignalProvider` adapter wrapping `ScanQuery` → `Signal`s (lives
  in strategy code or `liq-signals`).
- Label generation in `liq-datasets` consuming persisted scan runs.

## Tag preparation

Tag string proposed: `liq-scan-plan/F+1`.
**Not pushed by agent.** Operator authorizes push + tag per plan §3.13
step 6.
