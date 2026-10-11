# Unified Outcome Resolver — Phase A

Phase A publishes the compatible runtime image and prepares the resolver without
changing production authority or applying migrations.

## Compatibility boundary

The Phase A image must run against the production schema through migration 050.
The legacy Context and Liquidity workloads therefore set
`OUTCOME_RESOLVER_ENFORCE_WRITER_GATE=false`. The gate queries
`platform.outcome_resolver_control`, which is created by migrations 051–053;
enabling it before those migrations would break legacy outcome writes with an
undefined-table error.

This is sequencing, not a permanent safety bypass. A follow-up, migration-gated
change must set the legacy writer gate to `true` only after migrations 051–053
have been applied and verified.

## Phase A invariants

- The schema migration Job and its NetworkPolicies are excluded from the active
  Flux Kustomization. The operator-controlled migration manifest is suspended
  and is not a desired-state resource.
- `unified-outcome-resolver` is deployed with both
  `OUTCOME_RESOLVER_ENABLED=false` and `OUTCOME_RESOLVER_PRIMARY=false`. It
  cannot acquire a lease, advance a cursor, or write outcomes while inactive.
- Standalone Liquidity publication remains enabled and
  `LIQUIDITY_LIVE_ORCHESTRATOR_MODE=false`; no production publication-authority
  handoff occurs in Phase A.
- Existing terminal outcomes remain immutable. Reconciliation is limited to
  currently OPEN or missing-outcome signals in the later cutover phase.

## Required follow-up sequence

1. Validate the Phase A image and rendered manifests in staging against schema
   050.
2. Apply migrations 051–053 manually under the approved migration runbook;
   verify the control, lease, cursor, and contract tables.
3. Verify the migration Job completed successfully and remains outside the
   active Flux Kustomization.
4. Apply a separate configuration change setting the legacy writer gate true,
   while retaining `LEGACY_COMPAT` and keeping the resolver inactive.
5. Stop legacy outcome writers, verify zero compatibility writes, then perform
   the fenced `STOPPED` → `RESOLVER_PRIMARY` transition and start the resolver.

Rollback before step 4 is an image/configuration rollback with the legacy
writers still operating against schema 050. After step 4, rollback requires
the control row to be returned to `LEGACY_COMPAT` and the resolver to be fenced
before any legacy writer is restarted.
