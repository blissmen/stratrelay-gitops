# StratRelay authentication services

This component deploys the StratRelay IAM and notification services into the
existing `trading` namespace.

Both services use the shared in-namespace endpoints:

- PostgreSQL: `trading-postgres:5432`
- NATS: `trading-nats:4222`

The `stratrelay-auth-secrets` Secret is provisioned separately by the cluster
secret-management system. It must contain the database URLs and credentials,
JWT values, Google OAuth values, and Resend sender credentials referenced by the
workloads. No secret values belong in this repository.

The image references are tied to the StratRelay backend commits that introduced
the auth and email flows. They must be published to GHCR, or replaced with
their immutable release digests, before this component is reconciled by Flux.
