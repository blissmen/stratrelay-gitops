# Both PA corrected observers: concrete rollout

**Reviewable desired state; not yet verified or activated in the cluster.** This
PR adds the missing replacement workloads. The old deployments remain at zero.
No database SQL in this directory is part of Flux or the migration Job.

- Released source: `afb2da484022afce35bcbae6ce06639d9b015477` (platform-tools #222).
- Runtime digest: `sha256:87328a82d1024e882ef2bf4ee7bd928695625422be9d2a3b0a0dc3970db9ae18`.
- Run: `pa-ticks-20261019-1700-v1`.
- Planned publication boundary: **2026-10-19 17:00 UTC / 11:00 Mountain**.
- S/R: `9b6b913d-347d-5f90-b670-e6221a92b25c` / `pa-tick-sr-6106a8d9b1`.
- Engulfing: `d1fc279e-b8f0-5d48-a173-14a22fa3ded2` / `pa-tick-engulf-6106a8d9b1`.
- Credential prerequisite: Secret `price-action-corrected-observer-db` in `trading`.
- Scope: XAUUSD/BTCUSD/USDJPY, H1/M15, provider tick metadata,
  contemporaneous opening observations, 10-second cache-only cycles.
- All execution modes OFF, execution eligibility false, broker writes disabled.

The active resource is `apps/trading-platform/price-action-corrected-20261019.json`.
Its JSON formatting intentionally excludes this frozen artifact from the release
script's unquoted-YAML image repinner. Do not convert its image fields to that
format, modify its ConfigMap, or repoint its run to another image. `workload.json`
here is the identical review copy. The ConfigMap includes the source-bound
manifests and a read-only init check that refuses missing/mismatched registration,
legacy aliases, privileged credentials, wrong parameters or execution settings.
The init check also rejects first bootstrap after the planned boundary. If no
bootstrap occurred before it expires, generate a new run/T0/UUID bundle and
replace this PR's resources; do not backdate T0 or reuse the expired identity.
Actual first durable evidence is a separate timestamp, not the planned boundary.

## Before merging this PR

1. From the local cluster-connected environment verify context `local`, namespace
   `trading`, DB identity, current Flux revision and actual running source ancestry.
   Inventory existing corrected instances/runs to avoid duplicating another cutover.
   Verify Postgres/Redis Pod labels match both new network policies. Verify the
   original instances' frozen parameter/version joins against `register.sql`.
2. Through the established admin path run `credential-grants.sql`, or verify an
   equivalent dedicated role. It grants only research-table writes and read-only
   metadata access; an existing privileged/broad-write role aborts. Securely set
   its password using the existing credential provisioning mechanism. Create
   `price-action-corrected-observer-db` with an explicit `TRADING_POSTGRES_DSN`, or
   all PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD fields. Never commit/log the values,
   use trading_app, or reuse the execution credential. Verify role membership and
   SECURITY DEFINER function permissions through the established credential audit;
   table checks do not prove every possible function is harmless.
3. Apply `register.sql` transactionally through the verified admin path. Verify
   both new rows are OFFLINE, execution OFF/ineligible, with exact frozen parameter
   references, new UUIDs and three-symbol memberships. No original evidence is
   rewritten. Save sanitized before/after registration evidence.
4. Merge through normal GitOps review. Allow Flux reconciliation; do not manually
   create pods or restart unrelated workloads. Missing prerequisites leave the
   init guard blocked; do not bypass it to make pods Ready.

## After reconciliation

5. Verify each desired image against actual imageID, init success, no overlapping
   replicas, fresh durable bootstrap/cycles, exact market-input and entry-timing
   bindings, correct provider ticks and all three successful instruments. Initial
   bootstrap must emit zero entries. No direct bridge requests are permitted.
6. Apply `activate.sql` transactionally through the same admin path. It requires
   fresh bound checkpoints, exact scope and already-active parents, then enables
   only the new research publication instances and makes only the old instances
   offline. Record actual activation and first post-T0 durable evidence times.
7. Verify orchestrator refresh discovers both new UUIDs/explicit parameter refs,
   and follow an accepted post-T0 entry through canonical publication and shared
   theoretical outcome tracking when it occurs. Absence of a setup is valid.
   Verify zero execution intents/order attempts for both new UUIDs. Keep resolving
   old outcomes and keep all old checkpoints/events/signals/outcomes intact.

Record a sanitized timestamped report in Downloads and attach it to the rollout.
Report registration, deployment, forward evidence and publication separately.
For rollback apply `quiesce.sql` and scale only these two new Deployments to zero
through GitOps. Never restore old checkpoints into these runs or resume the old
mis-scaled evaluators automatically.
