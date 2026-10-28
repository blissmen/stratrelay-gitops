# Recovery after missing credential binding

The deployed orchestrator is blocked with CreateContainerConfigError: Secret library-event-orchestrator not found. The earlier activation PR was merged before its credential prerequisites were verified. This change pauses both Swing Deployments and the activation Job and removes the collector event patch from active Kustomize. The base collector retains event mode OFF without the new library-event-collector dependency. It preserves all manifests, SQL, source attestations, run data and broker execution settings.

This is containment, not completed activation or credential provisioning. No Secret values are stored in Git. Do not resume or reuse an elapsed activation deadline. First inspect the old Job and database state through the approved cluster path. If the old run exists, verify and preserve it rather than overwriting or automatically rebinding its instances. If no run exists, generate a complete fresh run/T0 bundle only after credentials, effective grants, NATS ACLs, streams, relay and data readiness are verified. Missing binding names must not be satisfied with placeholder credentials or copied admin DSNs.

Required live action: provision library-event-observer/orchestrator/collector and verify the relay using the approved credential mechanism. Authoring environment has no cluster credential/API access, so provisioning and current activation state remain unconfirmed.

# Swing research activation

DRAFT: cluster credentials, privileges, stream ACLs and data readiness are not verified from the authoring environment. Do not merge until these bindings are reviewed. No live activation is claimed.

This bundle starts five frozen BTCUSD-only V1 evaluators, a research orchestrator, and a guarded activation Job. Execution remains OFF/ineligible. The Job checks fresh native H1/H4/D1 data, bounded existing NATS streams and the primary database; applies only migration 078 if absent; then runs the generated activation transaction. It never applies unrelated pending migrations.

Existing Secret references: trading-runtime-endpoints (approved migration path), library-event-observer, library-event-orchestrator, library-event-collector, trading-signal-cutoff, trading-redis-auth, ghcr-pull. No Secret values or new credentials are included. Missing credentials fail closed. Required event permissions and migration/activation SQL privileges must be verified before merge. The existing library relay must be healthy.

The collector patch enables durable library event production shared with existing library consumers. Review that shared effect before merge. It preserves the published collector image and adds the dedicated collector event binding.

T0 is immutable in manifest.json and activation.sql. If elapsed before activation, regenerate the complete bundle from the same attested source/image with a fresh run ID and future T0. Never edit only the timestamp or overwrite an existing run.

The Job has zero automatic retries. If its result is uncertain, inspect persisted state: retry verifies an existing exact run rather than resetting it. Migration and activation are separate transactions; a failed activation can leave registration safely offline. The activation SQL itself is atomic.

Success requires completed activation Job, Ready observer and orchestrator, and post-T0 durable evaluations for each strategy. A naturally accepted entry must reconcile through research notification, canonical signal and canonical outbox. No synthetic production signals.

Rollback: apply quiesce_sql.sql through the approved database path, scale these two workloads to zero through GitOps, and revert only this collector patch after reviewing other event consumers. Preserve all run state/evidence. Removing the manifest alone does not quiesce database registration.
