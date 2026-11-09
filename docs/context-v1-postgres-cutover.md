# Context V1 PostgreSQL cutover and uniform confidence activation

## Release prerequisite — do not merge ahead of the backend release

This activation depends on
[platform-tools PR #344](https://github.com/strat-relay/platform-tools/pull/344).
PR #343's observation/confidence contract is already merged and its current
release is recorded in `apps/trading-platform/release.json`.

Before merging this activation:

1. Merge #344 after its CI passes.
2. Wait for its main-only Release workflow to publish immutable images.
3. Merge the normal GitOps image-promotion PR for that main release, including
   its `release.json`. Confirm the source commit contains #344 and the image
   promotion passed frozen-research guards.
4. Rebase this activation and rerun its render/configuration checks. Then merge.

This PR deliberately does not invent an unpublished digest or use a branch-built
image. Kustomize replacements inherit the existing main V1 runtime's promoted
digest for both init containers and the confidence reconciler. Those replacements
follow the normal immutable release promotion automatically. Configuration tests
do not prove that an image contains #344; the release prerequisite is separate.

Merging this activation before the required image promotion would stop the main
runtime at its capability gate until a suitable image is promoted. Keep the PR
draft until the release prerequisite is satisfied.

## What reconciliation does

The existing main runtime remains `Recreate`, so its old containers stop before
the replacement pod accesses the archived checkpoint. Its new init sequence is:

1. Import the new runtime capability, apply checksum-verified pending schema
   migrations, and verify migration 098 is recorded. The existing advisory lock
   serializes this with the ordinary schema migration Job.
2. Mount the existing `mt5-native-bridge-runtime` PVC read-only and bootstrap the
   Context V1 stream from `/work/context_structure_retrace_forward_state_compact.json`
   and `/work/context_structure_retrace_forward.jsonl`. The first import streams
   and verifies input checksums; it commits history and readiness together. On
   later pod starts the bootstrap checks PostgreSQL and skips the old files.
3. Start the V1 runner, canonical orchestrator and post-exit observer together with
   `CONTEXT_V1_STORAGE_MODE=POSTGRES`, stream `context-v1:phase6`, instance `phase6`.
   Their shared PostgreSQL credentials use the existing Secret by reference.

The source JSON files and frozen manifest remain on the PVC as rollback evidence.
The runner works only with pending/re-entry setups and open positions; archive
reads, discovery, event deduplication and post-exit pages come from PostgreSQL.
The frozen trading functions, freshness/recovery rules and signal identities are
preserved. Recreate causes a normal main-runtime restart/import window.

Uniform confidence capture is enabled on the canonical orchestrator. A bounded
CronJob processes at most 100 missing confidence records each minute, with
concurrency forbidden. It also reconciles missed captures and signals published
by restricted research publishers. Its network policy permits only PostgreSQL and
DNS; it has no Kubernetes API token. Confidence remains informational and
uncalibrated, without changes to sizing or execution eligibility.

This change does not change execution authority, strategy membership, subscriptions,
notification delivery, or frozen research image pins.

## Verify after reconciliation

- Both init containers complete; bootstrap reports IMPORTED on first activation
  or READY with `history_file_read=false` on later starts.
- The main runtime containers are ready. PostgreSQL checkpoint ownership is
  V1/phase6, revision advances, and symbol candle cursors progress.
- Compare total entity counts, all open position IDs, active setup/re-entry
  sequences and the imported source checksums with the archived checkpoint.
- Inspect the shared observation endpoint with operator auth. Historical rows
  remain visible while the runtime working set excludes archived terminal rows.
- Confirm newly published signals retain their IDs/geometry/frozen hashes and
  identify PostgreSQL provenance. Historical imports must not create fresh trades.
- Confirm confidence summaries appear and the reconcile Job reports completed
  or explicit failure IDs; insufficient history must remain unscored.
- Verify post-exit observations advance through bounded pages and pod memory
  reflects the active set rather than the complete historical checkpoint.

The Python synthetic import benchmark was bounded-memory; actual PVC, driver,
database import time and production RSS still require these deployment checks.

## Failure and rollback

An import, schema or ownership error blocks application startup and leaves the
archived source files unchanged. A failed import transaction does not expose a
partially ready stream. Fix the error and let Kubernetes retry the init container.
After database progress, do not revert to FILE using the old checkpoint. Stop
writers and export/reconcile the latest durable entities/cursors first. No
automatic stale-file fallback is configured.

Local validation:

```sh
kubectl kustomize apps/trading-platform > /tmp/context-runtime.yaml
python3 scripts/check_context_v1_runtime_rollout.py /tmp/context-runtime.yaml \
  --release apps/trading-platform/release.json
```
