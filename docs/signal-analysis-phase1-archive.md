# Collector-only continuous archival activation

2026-10-31. Revives the intent of CLOSED/UNMERGED PR #366, whose live primary
credential gate was never completed. No image change, no strategy patch, no
activation Job and no historical bulk backfill in this rollout.

Read-only preflight in the ACTUAL collector process environment:

- Database trading_platform; pg_is_in_recovery=false; transaction_read_only=off.
- Existing role has SELECT and INSERT on market_data.candle_archive.
- Actual image already includes the opt-in durable archive implementation:
  ghcr.io/strat-relay/trading-platform-runtime@sha256:292ae470702ab9720eb19aa8d05e89922a004d930d44ded1d81cec6783682807.
- Seven current cache windows: 11,032 valid completed candles, 144 archive
  overlaps, ZERO OHLC conflicts. New preservation upper estimate 8.7 MB at
  800 B/bar. No bridge calls or database writes were used for that inspection.
- Database ~5.16 GB, underlying filesystem ~23 GB available, PVC request 5 GiB.
  Review storage/backup growth before prolonged retention or broad backfill.

Original storage is append-only, no TTL. Existing bounded batches, idempotent
inserts, restart recovery and transactional OHLC conflict checks are reused.
Archive commits before Redis bar publication. Failures deliberately retain old
bar windows, degrade archive health and retry; independent quote tasks continue.

ONLY desired-state behavior change: MARKET_DATA_ARCHIVE_ENABLED=true on
market-data-collector. Later patches preserve its existing image and event/D1
scope; all membership, frozen observer, execution and trade-management settings
are unchanged. Render the complete Kustomization and run frozen-image checks.

After merge and Flux reconciliation:

1. Verify actual collector env/image, Ready state and md:health.archive_enabled.
2. Inspect candle_archive_committed, per-symbol archive_status/check timestamps.
3. Query archive counts/bounds by exact provider symbol/timeframe. Observe two
   advancing BTC close boundaries, not just healthy process heartbeats.
4. Inspect ARCHIVE_CONFLICT/UNAVAILABLE, healthy symbols and quote freshness.
5. New cache preservation does not fill already evicted Oct 16–27 BTC history.
   That needs a separately costed, bounded recovery; do not declare it complete.

Rollback by scoped GitOps flag false/remove and reconcile. Preserve archived
rows and original source_run_id. Disabling resumes Redis eviction risk. Do not
roll back other images or change strategy configurations to undo this rollout.
