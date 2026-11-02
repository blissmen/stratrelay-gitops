# Protected commercial catalog write enablement (production)

## Release order

This change enables `STRATEGY_CATALOG_ADMIN_WRITES_ENABLED=true` in the
`trading/platform-signals-api` Deployment. It must only be merged once an
operator has verified the **running** service has the expected database
migration/permissions and authenticated Console RBAC. The change does not
alter execution, strategy lifecycle, or subscriptions.

The backend already independently requires:
- Valid signed StratRelay JWT matching IAM's configured signer and issuer
- `OPS_ADMIN` role in signed IAM claims
- Verified subject set on request context before any catalog mutation
- Immutable before/after actor audit in the same DB transaction
- Pricing revision matching for edits
- Explicit publish action and valid Stripe Price IDs for nonzero published prices

## Required checks before merging this GitOps PR

1. Confirm the actual running Platform API image includes the unified Console
   IAM verifier and verified actor guard (platform-tools #305, #306, #307).
2. Confirm `/api/v1/console/auth/me` returns 200 with an approved, newly
   issued IAM operator JWT and 403 with an ordinary customer's signed JWT.
   Never paste JWTs, passwords or Secret contents into logs/issues.
3. Connect to the **trading Postgres database as the application database role**
   (not a superuser) and run this read-only preflight:

```sql
SELECT
  current_user AS db_role,
  to_regclass('strategy_mgmt.strategy_catalog') IS NOT NULL AS catalog_exists,
  to_regclass('strategy_mgmt.strategy_catalog_symbols') IS NOT NULL AS symbols_exist,
  to_regclass('strategy_mgmt.strategy_catalog_signal_tiers') IS NOT NULL AS tiers_exist,
  to_regclass('strategy_mgmt.strategy_catalog_admin_audit') IS NOT NULL AS audit_089_exists;
```

Also check required role grants for:
`strategy_catalog`: SELECT/INSERT/UPDATE;
`strategy_catalog_symbols`: SELECT/INSERT/DELETE;
`strategy_catalog_signal_tiers`: SELECT/INSERT/DELETE;
`strategy_catalog_admin_audit`: INSERT.

Apply migration `postgres/migrations/089_strategy_catalog_admin_audit.sql`
through the project's approved migration process if needed; review grants.
Do not switch on writes if the audit relation or privileges are missing.

4. Review Stripe monthly/annual Price IDs for accuracy in test mode prior to
   publishing any paid offering. Do not auto-publish any strategy.

## Post-deploy checks

- Confirm the `platform-signals-api` rollout is healthy and the actual pod
  has `STRATEGY_CATALOG_ADMIN_WRITES_ENABLED=true`.
- With a verified operator session, `GET /api/v1/strategy-catalog/admin` must
  return `data.writes_enabled=true`. The Console should enable **New offering**.
- With no bearer (401) and an ordinary signed customer bearer (403), catalog
  admin GET/POST/PATCH must remain denied. No catalog records should change.
- Create a single **hidden/inactive** test offering, check read-back, verify
  the audit row stores `iam:<authenticated-user-uuid>`, and confirm
  pricing-revision conflicts return 409. Do not publish it until Stripe
  contract/entitlements are checked.

## Rollback

Revert the feature flag to `false` and reconcile Flux. Previously saved
catalog rows remain; this only blocks further catalog writes.
