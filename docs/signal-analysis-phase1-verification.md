# Phase 1 live verification — 2026-10-31 UTC

## Delivered and gated

- Application #286 implements coverage, bounded source recovery, UTC validation,
  explicit reconstruction provenance, snapshot recovery, lookup migration 085 and
  native UTC D1 archival. #288 makes API coverage logs emit without changing the
  root logging policy. Both passed CI and merged.
- Console #71 adds coverage disclosure and window-token fences; no chart library
  or overlay changes. All 358 Console tests passed and its production build passed.
- GitOps #511 enabled the existing archive-capable collector without an image
  change. #513 deployed the Console. #514 scoped the main remediation images to
  collector + Signals API only. The telemetry-only follow-up uses the same scope.
- Main-only release images and exact source commits are recorded in
  signal-analysis-phase1-runtime.json. Migration 085's checksum and applied ledger
  row were verified, without replaying any strategy registration migration.
- Application CI: 1,914 unittest tests, 115 skipped; PostgreSQL snapshot recovery,
  restart idempotency and original provenance retention test passed. Existing
  Signal Analysis pytest suite: 13 passed. All four Docker validation builds pass.

## Actual data and API checks

The original BTC M15 durable archive stopped at Oct 15 23:30 UTC. Continuous
collector commits at 17:05 UTC preserved 1,522 BTC and 1,591 ETHBTC completed bars
across M5/M15/H1/H4. Subsequent 17:10/17:15 commits advanced real completed bars;
duplicate windows did not duplicate stored candles. Native BTC D1 now has 323
durable completed rows. No synthetic/resampled candles or bid/ask reconstruction.

Actual HTTP request to the deployed Signals API:

`GET /api/v1/signals/SIG_9b55b90276dbc035ed25b2bb/analysis/series?timeframe=M15`

Before: 0 candles and unavailable-history/incomplete-warmup warnings.
After initial archival: 128 visible candles, canonical archive source, precise
seven-slot warmup gap and zero selected timestamps absent from the archive.

Bounded recovery then used only seven actual bars from immutable snapshot
`b6c6007d15d73e6bce8b1b0b242265efa98d56a10a318fd1fa0a89a1e352bc68`:

- Exact MT5/BTCUSDm/M15 range [1793099700,1793106000), UTC-aligned opens.
- Dry run: 7 available, 0 missing, estimated 2,800–5,600 bytes. Existing role's
  SELECT/INSERT/UPDATE checkpoint privileges passed preflight.
- Applied in chunks of four: 7 inserted; cursor 1793106000; COMPLETE.
- Same immutable job rerun: 0 inserted; COMPLETE. Original source_run_id preserved.
- PG insertion clocks are 17:18:24 UTC, NOT the old candle close clocks. Source run
  references the immutable snapshot and recovery job. No strategy evidence filled.

After recovery: 128 visible candles; COMPLETE requested/calculation coverage;
warmup 400/400; missing 0; selected-not-archived 0; no coverage warnings.
Replay remains honestly unavailable because the original 15:25 decision is not
an M15 open. No timestamp rounding or outcome-contract/outcome mutation.

An actual `as_of=1793460300` HTTP request returned last close 1793459700 and ZERO
later candles. Coverage explicitly says original_decision_observations=false.

The bounded 400-slot BTC audit at approximately 17:22 UTC reported:

- M5: COMPLETE, 0 missing, 0 selected-not-archived.
- M15: COMPLETE, 0 missing, 0 selected-not-archived.
- H1: PARTIAL, 385/400 retained slots (15 older missing), 0 not-archived.
- H4: PARTIAL, 336/400 retained slots (64 older missing), 0 not-archived.
- D1: PARTIAL, 323/400 retained slots (77 older missing), 0 not-archived.

These are generic chart calculation-window counts, NOT a claim of strategy
readiness failure: strategy warmup requirements and frozen algorithms are separate.
Archival lag is measured against native timeframe closes and must be interpreted
using the session/timeframe, not a universal number-of-seconds threshold.

## Scope and concurrent-change caveat

Full rendered-manifest comparison for #514 found exactly TWO changed resources:
Deployment market-data-collector and Deployment platform-signals-api. Frozen
research image checks passed. Console deployment was independently scoped.
Our application/migration/recovery code does not write strategy configuration,
memberships, execution authority, risk, trade management or canonical outcomes.

The whole-cluster before/after strategy/membership digest is NOT unchanged:
a separate existing `trading-platform-schema-migrations` job ran 17:03:51–17:04:02
and applied counterpart registration migration 084. At 17:04 it changed four
legacy definition enabled flags and added/updated counterpart memberships. This
matches the independently merged main/GitOps counterpart rollout, not Phase 1's
index-only 085 or recovery utility. We did not revert or conceal that concurrent
work. Do not use this report to certify that no other actor changed the cluster.

## Remaining boundaries

Old BTC history already evicted between the Oct 15 archive and Oct 27 recovered
windows remains missing. No broad broker backfill was launched. Recover it only
through a separately bounded, costed collector-owned plan. Recent chart success
must not be represented as complete lifetime history.

Legacy data still lacks broker-server/feed-revision identity; metadata says so.
Snapshot/Redis windows are not stitched across uncertain identities, and recovered
market context is never original decision-time strategy evidence.

Underlying storage: database ~5.16 GB, ~23 GB filesystem free, PVC request 5 GiB.
Expand/review PVC and backup growth before broad or long-retention recovery.
No TTL/deletion was introduced. The seven-bar repair was <6 KB estimated.

Runbook, operator recovery commands, monitoring fields and rollback:
strat-relay/platform-tools `docs/signal-analysis/phase1-candle-remediation.md`.
