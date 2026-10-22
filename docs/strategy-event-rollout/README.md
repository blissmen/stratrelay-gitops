# Shared strategy event rollout — relay-only activation

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
and keep the older immutable-library cutover paused. The original staged bundle remains outside Flux, with its relay at replicas
zero and provisioning Job suspended. The separate active relay base is described
below.

## Relay database bootstrap after stream provisioning

After `STRATEGY_RUNTIME` is provisioned/verified, run the dedicated helper on the
operator computer. It is plan-only unless `--apply` is supplied:

```bash
python3 scripts/strategy_event_relay_bootstrap.py \
  --kubeconfig "$HOME/Downloads/local (1).yaml" --context rancher --apply \
  --output "$HOME/Downloads/stratrelay-event-relay-bootstrap.json"
```

Apply mode creates **only** the `strategy_event_relay` PostgreSQL login and
`strategy-event-relay` Kubernetes Secret. It verifies the recorded trading
namespace UID, actual primary/database and all three migration checksums before
writes. A random credential is retained in the Secret before transactional role
creation. A retry can resume an owned Secret without a DB role; it never resets
an existing role password or adopts an unpaired pre-existing role. Unexpected
bindings fail closed. No credential enters command arguments, stdout, reports
or git. All subprocess output is captured; exceptions are reported by type only.

The role receives CONNECT, platform schema USAGE, SELECT/DELETE on the scheduling
outbox and UPDATE on its `published_at` column. No INSERT, schema CREATE, role
membership, superuser, database/role creation, replication or RLS bypass is
allowed by the login probe. The existing signal relay container is used to test
that login and these privileges through the actual DB service; it performs only
a read-only privilege query. No broker or strategy data is written.

`RELAY_DB_READY` means this prerequisite passed; it does not mean the event route
is active. Following the operator's RELAY_DB_READY report, the bundle at
`apps/trading-platform/strategy-runtime-event-relay` is enrolled in Flux. It
contains only a single relay at replicas one, a fail-closed prerequisite init
container and both sides of DB/NATS network permissions. Existing producers,
consumers and strategy modes remain unchanged. The relay may legitimately be
idle until producers are enabled; pod Ready alone is not delivery evidence.


## Relay-only activation evidence and limits

Operator evidence confirmed STRATEGY_RUNTIME provisioned/verified, then
RELAY_DB_READY with new role/Secret, successful service-path login and privilege
verification, and matching migration 072/073/074 filenames and checksums. These
are reported operator observations, not a live cluster read from this repository.

The active relay uses the shared trading-nats-auth account and its dedicated
strategy-event-relay DB Secret. The init container checks the actual DB primary,
outbox, role, delivery privileges and NATS stream compatibility without creating
streams or publishing messages. Failures block startup and report exception type
only. After startup, the existing relay code delivers scheduling hints and prunes
only delivered hints older than an hour. It does not write strategy evidence or
canonical signals. No producer or consumer is opted into JETSTREAM by this change.

Post-deployment evidence must include the prerequisite result, actual image,
restart count, shared-credential/DB connectivity and sustained relay process. A
Ready pod is not proof of end-to-end delivery; an empty stream can be normal
until the collector and strategy producers are enabled in subsequent changes.
Rollback is removal of the active relay base from Flux resources; retain the DB
role, Secret, stream, pending hints and all historical evidence for diagnosis.

## Collector hint-writer bootstrap and activation

The operator's relay rollout report at 2026-10-22T18:00:02Z showed the expected
image, one ready replica, zero relay/init restarts, and the compatible stream
with zero retained messages. This establishes relay startup; it is not evidence
of end-to-end scheduling or a latency improvement.

The same bootstrap helper now supports a separate collector profile:

```bash
python3 scripts/strategy_event_relay_bootstrap.py \
  --kubeconfig "$HOME/Downloads/local (1).yaml" --context rancher \
  --component collector --apply \
  --output "$HOME/Downloads/stratrelay-event-collector-bootstrap.json"
```

Expected result: COLLECTOR_DB_READY. This creates only strategy_event_collector
and strategy-event-collector-db after the same identity/primary/migration gates.
The collector role receives INSERT on event_id/runtime_id/payload and SELECT on
event_id for ON CONFLICT, with CONNECT/schema USAGE. It cannot read whole hint
payloads, update delivery status, delete hints, create schema objects, or gain
admin/membership privileges. The login/privilege probe runs from the existing
collector container through the DB service, without writing hints or changing
collector environment. The relay profile remains the default and keeps its
existing role, Secret and retry behavior.

After the operator reported COLLECTOR_DB_READY with successful service-path
login/privilege verification, the patch at
apps/trading-platform/strategy-event-collector.patch.yaml is enrolled through
Kustomize. It enables only the bounded background cache-hint writer. It preserves the collector image, single-reader
Recreate strategy, Redis/bridge configuration and MARKET_DATA_EVENT_MODE=OFF.
That older immutable-library event mode is separate and stays OFF. The collector
writes cache-ready scheduling hints to PostgreSQL; it does not connect directly
to NATS. Existing collector DB egress/destination ingress already permit that
service path; no new bridge or NATS network permission is needed.

This activation restarts the one collector briefly through Recreate. Check continued Redis/quote freshness,
strategy_event_health, committed cache hints and relay NATS acceptance before
activating consumers. This phase alone does not change strategy decision or
entry semantics, execute orders, or opt strategies into event-driven scheduling.


After Flux applies the collector patch, run the read-only delivery audit:

```bash
python3 scripts/strategy_event_collector_audit.py \
  --kubeconfig "$HOME/Downloads/local (1).yaml" --context rancher \
  --output "$HOME/Downloads/stratrelay-event-collector-rollout.json"
```

COLLECTOR_HINT_DELIVERY_OBSERVED requires the expected ready image on collector
and relay, actual collector JETSTREAM/runtime/database bindings, fresh cache
heartbeat and writer success after this collector container started, compatible
NATS stream configuration, and post-start hint rows marked published by the
relay. It reads at most 50 recent hint rows with transaction/statement timeouts;
it does not insert test hints, publish NATS messages or query strategy outcomes.
Row created-to-published durations use the database row timestamps; created_at
is not a transaction commit timestamp or an end-to-end strategy latency.
Zero retained stream messages does not negate already-published durable rows.

The audit always reports strategy_consumption_verified=false. Cache hint
delivery proves neither strategy consumption nor canonical signal latency or
broker execution. Continue with consumer opt-in only after this evidence is
reviewed. Roll back the collector hint writer by removing its Kustomize patch,
preserving credentials, stream, hint rows and all historical evidence.

## Existing consumer activation after cache-hint delivery

Operator evidence at 2026-10-22T21:14:34Z showed healthy collector event mode,
zero pending writer symbols/errors, expected ready collector/relay images and
zero restarts, and 50/50 sampled hint rows published. Maximum sampled row-created
to published interval was 95.926 ms. This is producer-to-relay evidence only;
it is not a strategy decision-to-publication latency measurement.

Three transport-only patches opt in existing services:

| Existing container | Event path | Preserved behavior |
| --- | --- | --- |
| orchestrator-shadow | Shared cache/evidence NATS hints; stable runtime signal-orchestrator | One existing primary authority, current lifecycle/instance policy/cutoff and publication ownership |
| liquidity-live-runtime/runtime | Cache NATS hints; stable runtime liquidity-live-runtime | MONITOR_ONLY; standalone canonical publication stays off |
| signal-outbox-relay/relay | PostgreSQL LISTEN canonical_outbox_events with recovery scans | Existing DB leases, event contracts, publication deduplication and execution policies |

All three use the single trading-nats-auth credential. Existing DB/NATS/DNS
NetworkPolicies already permit these pod labels on both connection sides, so
no network policy or DB role grant is added. Event flags are container-scoped;
Context V1/V2 remain in their current polling modes. The shared main pod uses
Recreate, so its Context and post-exit sidecars restart with the orchestrator,
retaining their settings and persisted state. No second orchestrator workload,
new strategy identity, source rule, broker policy or execution mode is created.

Canonical outbox event mode uses PostgreSQL commit notifications, rather than a
second NATS wakeup consumer. Its existing NATS publisher still publishes the
same canonical events. No stream provisioning or auth rotation is performed by
the GitOps patches. The separate older immutable-library event route stays off.

After Flux applies these patches, run:

```bash
python3 scripts/strategy_event_consumer_audit.py \
  --kubeconfig "$HOME/Downloads/local (1).yaml" --context rancher \
  --output "$HOME/Downloads/stratrelay-event-consumer-rollout.json"
```

CONSUMER_WAKEUPS_AND_CANONICAL_LISTENER_OBSERVED requires actual event-mode flags,
expected ready image, current-container EVENT wakeup logs with valid event IDs,
and the expected NATS consumer filters/NEW delivery/explicit ACK/acknowledged
messages for orchestrator and Liquidity. It also requires an idle PostgreSQL
LISTEN backend from the current canonical relay pod and confirmed event mode.
The audit is read-only: INFO requests only, no consumer creation, subscription,
test message, test signal or historical mutation. It returns allowlisted log
fields, never raw logs/credentials, and leaves all_strategy_consumption_verified
and canonical_signal_latency_verified false. A real post-rollout canonical
signal trace is still required to establish latency improvement.

Remaining work: bind Context V1/V2 hint-writer credentials before their event
opt-in; audit frozen corrected PA/library/ICT-SMC/Donchian artifacts and use fresh
run bindings where required. Do not bulk repin those images or reset checkpoints.
Rollback these three consumer patches to restore their prior scheduling modes;
retain stream/durables, credentials, evidence and historical identities.

## Pending consumer evidence and frozen observer readiness diagnostics

The operator's 2026-10-22T21:29:04Z consumer report showed event mode and valid
NATS consumer configuration with 140/140 orchestrator deliveries acknowledged,
144/144 Liquidity deliveries acknowledged, no pending/redelivered messages,
expected ready images, zero container restarts and one canonical LISTEN backend.
It contained no matching current-container EVENT wakeup log rows. This proves
receiver-thread delivery and canonical LISTEN presence; evaluator-cycle wakeup
and signal latency remain unconfirmed. Do not equate ACK with evaluation commit.

The consumer audit now reports capture counts, recognized JSON/logging formats,
allowlisted cycle-phase/timing events, the event-module digest/log contract, and
the orchestrator's filtered persisted heartbeat. It recognizes the known
stratrelay.strategy_audit logging prefix without returning raw log lines or
unknown event payloads. It retains the EVENT wakeup requirement and does not
turn ACKs alone into successful evaluator evidence. A parser/capture issue and a
blocked/slow business loop are hypotheses until these diagnostics are collected.

The ICT/SMC, original library canary and Donchian V2 screenshot separately showed
Running/NotReady on their unchanged frozen images. Run the read-only observer
audit without repinning, resetting checkpoints or relaxing readiness:

```bash
python3 scripts/strategy_observer_readiness_audit.py \
  --kubeconfig "$HOME/Downloads/local (1).yaml" --context rancher \
  --output "$HOME/Downloads/stratrelay-observer-readiness.json"
```

It reads the frozen run's persisted health projection and compares its binding
using the current frozen image. It records per-stream statuses, exception types
and static reason classifications, plus Redis state/quote-age/bar-count metadata.
Unknown exception messages, raw health objects, credentials, prices and market
bars are not exported. No evaluator is run; the probe never calls run_once,
process, ledger.save, bridge APIs, cache writes or broker APIs. Config/DB/cache
failures are exception-type-only and remain explicit missing evidence. The
READ_ONLY_DIAGNOSTICS status means collection, not a healthy runtime verdict.

A blocked instrument can affect the run-wide readiness predicate; determine the
specific persisted reasons and cache state before changing that predicate. No
market-closure, data-gap, artifact conflict or deployment cause is proven solely
by the screenshot. These diagnostic changes alter no workload or strategy code.
