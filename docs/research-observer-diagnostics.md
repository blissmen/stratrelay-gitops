# Research observer diagnostics (2026-10-31)

After merging, Flux launches one bounded read-only Job: `research-observer-diagnostics-20261031` in `trading`. In Rancher open Workloads → Jobs → this Job → Pod → container `diagnostics` → Logs. Copy the JSON report; no local terminal commands are needed.

It uses the existing observer database binding and Redis authentication. There are no new Secret names, Kubernetes API tokens, broker endpoints or NATS clients. PostgreSQL is explicitly read-only with a ten-second statement timeout. NetworkPolicy permits only PostgreSQL, Redis and DNS. It does not restart or modify other workloads, publish messages, migrate data, activate strategies or reset checkpoints.

The report distinguishes scheduled closure from a source snapshot already stale before close, gives source/quote timestamps and gap counts, and reads managed Swing registration and known research checkpoint health. It omits raw cache, OHLC/quotes, DSNs, environment values and exception text. Missing privileges remain a redacted report error; no credential substitution is attempted.

Use this evidence to decide the repair: source hole recovery, process/checkpoint failure, or missing activation binding. Current Swing manifests remain paused after the missing event credential incident. Do not resume their elapsed T0. Provision the actual approved bindings and inspect old run state before generating any fresh activation. A green parent catalog badge is not observer progress.

The Job has no automatic retries, no TTL/reconcile recreation loop, and a three-minute deadline. Completed diagnostic resources can be removed through a later GitOps cleanup. No database evidence needs rollback.

Validation: Python compile; four tests for preclose stale detection, secret/price redaction, exception redaction and literal read-only SQL. No live cluster validation performed. Related API health improvement: https://github.com/strat-relay/platform-tools/pull/275
