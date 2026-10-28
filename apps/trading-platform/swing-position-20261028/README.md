# Swing research activation

DRAFT: cluster credentials, privileges, stream ACLs and data readiness are not verified from the authoring environment. Do not merge until these bindings are reviewed. No live activation is claimed.

This bundle starts five frozen BTCUSD-only V1 evaluators, a research orchestrator, and a guarded activation Job. Execution remains OFF/ineligible. The Job checks fresh native H1/H4/D1 data, bounded existing NATS streams and the primary database; applies only migration 078 if absent; then runs the generated activation transaction. It never applies unrelated pending migrations.

Existing Secret references: trading-runtime-endpoints (approved migration path), library-event-observer, library-event-orchestrator, library-event-collector, trading-signal-cutoff, trading-redis-auth, ghcr-pull. No Secret values or new credentials are included. Missing credentials fail closed. Required event permissions and migration/activation SQL privileges must be verified before merge. The existing library relay must be healthy.

The collector patch enables durable library event production shared with existing library consumers. Review that shared effect before merge. It preserves the published collector image and adds the dedicated collector event binding.

T0 is immutable in manifest.json and activation.sql. If elapsed before activation, regenerate the complete bundle from the same attested source/image with a fresh run ID and future T0. Never edit only the timestamp or overwrite an existing run.

The Job has zero automatic retries. If its result is uncertain, inspect persisted state: retry verifies an existing exact run rather than resetting it. Migration and activation are separate transactions; a failed activation can leave registration safely offline. The activation SQL itself is atomic.

Success requires completed activation Job, Ready observer and orchestrator, and post-T0 durable evaluations for each strategy. A naturally accepted entry must reconcile through research notification, canonical signal and canonical outbox. No synthetic production signals.

Rollback: apply quiesce_sql.sql through the approved database path, scale these two workloads to zero through GitOps, and revert only this collector patch after reviewing other event consumers. Preserve all run state/evidence. Removing the manifest alone does not quiesce database registration.
