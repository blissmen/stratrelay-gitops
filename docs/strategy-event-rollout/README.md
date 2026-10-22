# Shared strategy event rollout — staged, not activated

Application PRs #238 and #239 are merged. PRs #240 and #241 were duplicates of
their original branches and are closed. GitHub PR comparisons use the merge base;
after a squash merge an old branch may still show a patch despite identical files.
New work starts from current main.

Verified release: platform-tools `410e55f4453fb7bb06cb951e143aa6040fb3c478`, successful
Release workflow `39773665592`. GitOps release PR #383 carries the exact image:

`ghcr.io/strat-relay/trading-platform-runtime@sha256:f7b4ea91af4e5c6866bdceaa0e1f10ade9c340d7e070ea110c65fa11865ee872`

The bundle under `staged/strategy-runtime-events` is deliberately **not referenced
by the Flux application**. Its relay has replicas 0 and provisioning Job is
suspended. It contains no credentials, registrations, migrations, strategy
activation, mounted observer state, execution policy changes or broker access.
Merging this preparation cannot start it. It does not enable event mode on any
existing workload and does not resume the older paused immutable-library cutover.

## Read-only live evidence

From the repository checkout, run the command below on the computer with the
working kubeconfig. It writes a redacted report to Downloads; the script uses
only the Python standard library and the existing kubectl client.

```bash
python3 scripts/strategy_event_preflight.py \
  --kubeconfig "$HOME/Downloads/local (1).yaml" \
  --context rancher \
  --signal-id SIG_50b76b119c96260bed69441e \
  --output "$HOME/Downloads/stratrelay-strategy-event-preflight.json"
```

It reads Kubernetes metadata, Secret **key names only in its report**, image IDs,
restart counts, and allowlisted non-secret environment flags inside running
containers. Actual runtime environment is important: a flag can be inherited
through envFrom even when it is absent from the Deployment's explicit env list.
It never outputs Secret values, credentials, DSNs, arbitrary environment, raw pod
logs or exception details. When the selected signal is present in PostgreSQL,
it reads a bounded log window around its decision and retains only known timing
event types and allowlisted timing fields. Missing/rotated logs remain explicit
missing evidence; a current configuration snapshot does not prove the old path.

The optional probes run against existing containers, without creating workloads:

- PostgreSQL primary: the previously verified in-container POSTGRES_USER/password
  environment path, explicit database `trading_platform`, read-only PGOPTIONS and
  SELECT queries only. Missing credentials or permissions are reported, never
  replaced with another account. Checks migration filenames/checksums and the
  canonical notification trigger, plus selected timing fields for the signal.
- Existing signal relay: read-only NATS stream INFO request using its existing
  service connection. This sends a management request, not a market, research,
  signal or execution event; it neither provisions nor rewrites streams. Existing
  credentials are used only for this read-only inspection, not assigned to any
  new runtime role.

Secret presence and stream INFO do not prove global account permissions or new run bindings.
The report always leaves those as unverified review gates and never declares
activation approved. Commands failing access produce redacted error categories.
If the approved audit mechanism uses different Secret names, reconcile the map
before activation. Use the shared NATS account described below; do not copy credentials into per-runner Secrets.

## Activation sequence

1. Reconcile the released application image separately through release PR #383.
   Image deployment alone does not enable STRATEGY_EVENT_MODE=JETSTREAM.
2. Verify the actual primary ledger. Transport migration 072 is immutable; the
   later Kojo routing migration is 073 with identical SQL; canonical notification
   migration is 074. Compare filename AND checksum. Do not rewrite ledger history
   or replay an already applied migration to make the numbers match.
3. Use one shared NATS account with global publish/subscribe permissions, as
   explicitly authorized by the user. Reference `trading-nats-auth` keys
   `NATS_USER`/`NATS_PASSWORD` as `P2_NATS_USER`/`P2_NATS_PASSWORD` in each client.
   Use the in-cluster `trading-nats` service URL. Do not create separate NATS
   users or duplicate per-strategy NATS Secrets. Verify actual permissions before
   activation; Secret metadata alone does not prove them. Database credentials
   remain separate: collector, relay and file-backed Context connections need
   their STRATEGY_EVENT_DSN bindings; PA/library evidence roles need INSERT on
   the scheduling outbox within their existing transaction. No runtime receives
   PostgreSQL admin or broker credentials through this change.
4. Review both sides of NetworkPolicy for every existing runner, not only the new
   relay. The staged policy covers only the new labeled relay/provisioner; it does
   not grant NATS access to observer pods automatically.
5. Provision and inspect STRATEGY_RUNTIME through the operator credential. Subjects:
   runtime.market.cache.ready.v1.* and runtime.strategy.evidence.ready.v1.*;
   file storage, discard NEW, max 64 MiB, 1024-byte messages, 300-second retention.
6. Start the new relay and verify durable publish acceptance. Enable the collector
   hint writer; verify recent md:health.strategy_event_health success and continued
   collector progress. This mode is separate from MARKET_DATA_EVENT_MODE and does
   not require resuming the older immutable-library pipeline.
7. Enable the existing authority and canonical signal relay, preserving all cutoff,
   policy and execution-authority settings. Then switch Context V1/V2 scheduling.
   Kojo and in-process Liquidity inherit the authority's wakeup path. Do not run a
   second canonical publisher for the same managed binding.
8. Enroll every PA/library workload, including original canary, ICT/SMC and Donchian
   V2. Audit frozen artifacts first. Where identity binds runner source, prepare
   fresh research-only OFF/ineligible bindings and future T0; preserve old evidence,
   checkpoints and outcomes. No bulk image pin, binding overwrite or state reset.
9. Verify multiple post-T0 market events end to end and record timing correlations:
   source receipt, visible cache, hint commit, NATS acceptance/receipt, evaluation,
   evidence commit, canonical insertion and publication. Confirm actual runtime
   flags, not just merge status or pod Ready. Broker permissions remain unchanged.

The supplied AUDCAD signal took 29.567474 seconds from decision to publication,
of which 28.060236 seconds preceded emission and 1.507238 seconds followed it.
Its missing source timestamp/data age do not locate those first 28 seconds.
SYMBOL_NOT_ALLOWED is an execution-policy result, not a stale-signal diagnosis.
No claim that the event route was active can be made from that payload alone.

Source SOP:
https://github.com/strat-relay/platform-tools/blob/410e55f4453fb7bb06cb951e143aa6040fb3c478/docs/engineering/STRATEGY_EVENT_TRANSPORT_SOP.md

## Next operator step: bounded infrastructure audit

When the full audit times out in PostgreSQL, obtain infrastructure evidence
independently before provisioning credentials or enabling runners:

```bash
python3 scripts/strategy_event_preflight.py \
  --kubeconfig "$HOME/Downloads/local (1).yaml" --context rancher \
  --infrastructure-only \
  --output "$HOME/Downloads/stratrelay-strategy-event-infrastructure.json"
```

This mode does not trace a signal, scan entry/outbox rows, or probe runner
environments. Inside the discovered PostgreSQL pod it connects explicitly to
loopback port 5432 and `trading_platform`, using the existing approved admin audit
environment only for read-only SELECTs. Connection timeout is five seconds,
statement timeout five seconds and lock timeout two seconds. It does not fall
back to a different database or credential. Migration evidence remains unverified
if that command fails; an observed empty migration list means the expected rows
were not found. NATS metadata includes flag names, environment key names and
Secret/ConfigMap references only. No argument values or configuration contents
are returned, and stream INFO remains a read-only request.

## Shared NATS account policy

The user has authorized one global NATS user instead of separate scoped users.
The staged relay and stream provisioner therefore reference the existing shared
`trading-nats-auth` Secret directly. Existing server authentication and credentials
are not rotated or rewritten by this change. Once live permissions are verified,
all event consumers use the same binding pattern. Global means unrestricted NATS
publish/subscribe access within that account, including JetStream API subjects;
it does not mean PostgreSQL superuser, Kubernetes cluster-admin or broker access.

Remaining database bindings are `strategy-event-collector-db`,
`strategy-event-relay`, `strategy-event-context-v1` and
`strategy-event-context-v2`, each containing STRATEGY_EVENT_DSN. PA/library
observers keep their existing evidence DB identities and need the scheduling
outbox grant audited. The preflight no longer demands twelve per-client NATS
credential Secrets or an audit of per-client scoped ACLs.

All existing activation gates still apply: database privilege verification,
actual shared NATS permissions, stream provisioning, both NetworkPolicy sides,
artifact/checkpoint bindings, and post-T0 timing evidence. Preserve frozen runs
and keep the older immutable-library cutover paused. The relay remains replicas
zero, provisioning Job suspended, and the staged bundle outside Flux resources.
