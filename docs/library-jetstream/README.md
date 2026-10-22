# Library JetStream research cutover

**PREPARED, NOT ACTIVATED. Keep the PR draft until the prerequisites below are
verified on the cluster.** This is the original three-strategy library canary
(Donchian V1, EMA Pullback V1, FVG Retracement V1) on BTCUSDm, XAUUSDm and USDJPYm.
It does not migrate Context V1/V2, corrected PA, ICT/SMC or Donchian V2 observers.
No strategy rule, frozen parameter or historical evidence is rewritten.

Local validation: 17 manifest, redaction and cutover-gate tests passed. YAML,
embedded Python compilation and diff checks passed. No cluster, real credential,
NetworkPolicy or live NATS verification has been performed from this environment.

Source: platform-tools `2c333a98fa59c711dc167ab2834966ab879a7cfb` (merged PR 230).
Runtime digest: `sha256:35fc80be3dcb9a5062c43514395cce6772b3f004a95583d5a19fc9073f9da5fa`,
verified against the successful main Release workflow and GitOps release.json.

Candidate run: `library-js-20261022-0600-v1`. T0: **2026-10-22 06:00 UTC**
(midnight October 22 in America/Denver). This is a candidate, not a scheduled
or observed activation. If merge cannot finish with margin, generate a fresh
run/T0; the activation refuses elapsed T0 and never reuses an existing run.

## Before merge

1. Verify migration 070 via the approved migration/admin path. The existing
   migration Job uses the released image but its application role may lack DDL
   ownership; a successful image rollout is not migration evidence.
2. Provision and audit the six dedicated Secret contracts below through the
   existing approved credential mechanism. No Secret values belong in Git.
   Do not alias the current observer/runtime/admin DSN into these names.
3. Confirm NATS server supports multi-filter durable consumers (2.10+) and
   its account allows the scoped subjects/JetStream operations below. Establish
   available file-store capacity for two bounded 256 MiB streams.
4. Run the read-only report and verify the Kubernetes API service destination.
   The selected-file report at 2026-10-22T03:53:11Z confirms API service
   `10.43.0.1:443` and backend `10.10.10.10:6443`; both exact destinations are
   in the cutover reader policy. Verify effective policy before merge. Do not open broad
   internet egress to make the API gate pass.
5. Verify the three existing instances are OFF/ineligible, retain their original
   run binding and exact BTC/XAU/USDJPY memberships, and have enabled library
   publication parents. Confirm no independent deployment runs the same old run.
6. Review shared collector disk/DB write latency and evidence retention capacity.
   The source now commits evidence before updating Redis. A DB outage can delay
   the shared cache; this is a deliberate durability dependency, not a free
   performance change. No automatic DB evidence pruning is configured.

The collector uses Recreate and is shared. **Merging without the new collector
credential will interrupt shared market-data collection.** Do not merge this
candidate merely because manifests parse or containers build.

Run from this GitOps checkout and share the generated report:

```bash
python3 scripts/library_jetstream_preflight.py \
  --kubeconfig "$HOME/Downloads/local.yaml" \
  > "$HOME/Downloads/library-jetstream-preflight.json"
```

This report reads cluster metadata/Secret key names only and prints no Secret
values. It does not prove DB grants, NATS ACLs or live consumer health.
Failed reads report a sanitized error category; an inaccessible Secret has
unknown keys rather than being declared missing. Context names and API reachability
are included. Pass `--context <name>` if the kubeconfig uses another context.

The selected-file report restored access using `rancher`, confirmed the exact
released collector/scanner images and existing signal-cutoff Secret, and returned
RESOURCE_NOT_FOUND for all six new library-event Secrets. Old polling canary still
runs its frozen image. No transport Jobs or Deployments exist in that report.

Before provisioning credentials, gather the read-only local-socket PostgreSQL
prerequisites and NATS version/config-source inventory:

```bash
python3 scripts/library_jetstream_infrastructure.py \
  --kubeconfig '<the-working-selected-file>' --context rancher \
  > "$HOME/Downloads/library-jetstream-infrastructure.json"
```

This invokes only read operations: pod inventory, NATS version, and PostgreSQL
SELECTs with default_transaction_read_only=on. It never dumps NATS configuration,
environment values or passwords. A failed local-socket/admin read remains a blocker;
the script does not borrow or alter credentials to work around it.
The DB query uses the matching existing POSTGRES_USER/POSTGRES_PASSWORD pair
inside the same PostgreSQL pod when available, or the pod's explicit PGUSER/
PGPASSWORD settings. It never substitutes another role's password, copies a
credential out, or prompts interactively. Failures are classified without raw
credential-helper/server error output. The 04:00 UTC inventory verified NATS
2.10.29 and the trading-nats-auth reference, but the original DB probe failed;
migration, role and grant evidence remains unconfirmed.
The 04:09 UTC report classified a database-name failure. The original probe
incorrectly hardcoded `trading`. It now connects to the pod's configured
POSTGRES_DB, independently parses collector/orchestrator connection targets
without connecting or printing credentials, and compares database names.
The draft manifests still contain the unverified `LIBRARY_EVENT_DATABASE=trading`
placeholder. Replace it with the verified application database before merge;
do not create an empty database named trading to satisfy the old assumption.

## Credential contracts

All DSNs must explicitly target the application primary verified by the
infrastructure report (database name not yet verified), include a bounded
connect timeout, and use distinct non-superuser, non-owner roles. Audit effective
privileges, including inherited role membership; no broker tables/writes or DDL
rights for transport processes. The collector keeps its existing read path and
uses its dedicated event DSN only for immutable evidence writes.

| Secret | Required keys | DB privileges / capability |
| --- | --- | --- |
| `library-event-collector` | `LIBRARY_EVENT_DSN` | research schema usage; SELECT/INSERT snapshots and outbox; SELECT/INSERT/UPDATE source heads |
| `library-event-relay` | DSN + NATS keys | SELECT outbox/state; UPDATE only outbox.published_at; LISTEN |
| `library-event-observer` | DSN + NATS keys | SELECT snapshots/outbox/heads/state/events/inbox/cursors; INSERT/UPDATE state and cursors; INSERT events/outbox/inbox |
| `library-event-orchestrator` | DSN + NATS keys | SELECT immutable evidence and managed catalog/membership/state; INSERT inbox; existing canonical signal/candidate/outbox insertion privileges; row-lock privileges where required by FOR SHARE/FOR UPDATE |
| `library-event-provision-streams` | NATS keys | No DB access needed; create/read only the two research streams |
| `library-event-activate` | `LIBRARY_EVENT_DSN` | Controlled short-lived cutover role: SELECT catalog/membership/state; UPDATE online/attributes/updated_at on the three instances; INSERT fresh research state. No NATS or broker credential |

DSN = `LIBRARY_EVENT_DSN`. NATS keys = `NATS_URL`, `P2_NATS_USER`,
`P2_NATS_PASSWORD`. Provisioning needs no DSN.
The event orchestrator also reads existing `trading-signal-cutoff` (ID/UTC).

PostgreSQL SELECT FOR SHARE/UPDATE requires appropriate UPDATE privilege on at
least one column in addition to SELECT. Audit the actual canonical insertion
path and restrict these column grants rather than granting arbitrary table UPDATE.
Do not guess a universal GRANT ALL or reuse admin credentials.

NATS ACLs must include scoped JetStream request/reply management subjects and
private inbox replies required by nats-py, in addition to these data namespaces:

- Relay: publish `market.evidence.committed.v1.*` and
  `research.library.entry.observed.v1.*` only, plus read stream configuration.
- Observer: create/bind/read its own `observer-<run>` durable on MARKET_EVIDENCE;
  pull and acknowledge it, with the manifest's three provider filters.
- Orchestrator: create/bind/read its own `orchestrator-<run>` durable on
  LIBRARY_RESEARCH, filtered to this run; pull/ack only that durable.
- Stream operator: create/info MARKET_EVIDENCE and LIBRARY_RESEARCH.
- No grants to execution subjects, other durable consumers, stream deletion,
  stream purge or arbitrary wildcard JetStream administration for runtime roles.

ACLs are broker-specific; verify them with the approved NATS management path.
No NATS server/account configuration is owned by this GitOps repository today.

## Merge and reconcile

After prerequisite proof, merge the reviewed GitOps PR through the normal path.
Flux then stops the old polling observer, enables collector evidence commits,
creates the bounded streams in a one-off Job and launches the cutover Job.
The cutover checks the old pod is gone, the legacy scanner and collector have
finished the exact released rollout, stream provisioning succeeded, and original
run bindings/memberships are correct. Its transaction quiesces and rebinds only
the three OFF research instances and inserts a fresh attested checkpoint.

All three transport Deployments wait for matching activation evidence. Init
container backoff while the bounded Jobs finish is expected. A Job exhausting its
bounded retry/deadline is a blocker; inspect it instead of repeatedly deleting it.
An uncertain/lost activation response must be checked against DB state; never
reset the checkpoint. Full rebinding is transactional and preserves the old run.

The event observer uses next-open mode, five-second quote freshness, causal
H1/M15 ordering and separate tentative bid/ask references. Canonical publication
is DB_PRIMARY; existing canonical outbox relay remains the network publisher.
Execution stays DISABLED/OFF/ineligible. No sizing or broker code runs here.

## Verify

```bash
kubectl --kubeconfig "$HOME/Downloads/local.yaml" --context local -n trading \
  get pods,jobs -l stratrelay.app/library-events=true -o wide
kubectl --kubeconfig "$HOME/Downloads/local.yaml" --context local -n trading \
  logs deployment/library-event-relay --tail=100
kubectl --kubeconfig "$HOME/Downloads/local.yaml" --context local -n trading \
  logs deployment/library-event-observer --tail=100
kubectl --kubeconfig "$HOME/Downloads/local.yaml" --context local -n trading \
  logs deployment/library-event-orchestrator --tail=100
```

Verify both completed Jobs, no old polling
pods, correct image IDs, bounded streams, provider cursors/inbox progress, absence
of redelivery storms, and an accepted entry with bid/ask and stage timestamps.
BTC supplies the always-open observer readiness heartbeat; a quiet FX/metal feed
does not alone fail readiness. Probe success does not prove signals are profitable
or that end-to-end publication has occurred. Confirm zero execution intents and
broker writes from the canary through the existing execution audit.

## Regeneration and rollback

With the exact released platform-tools checkout on PYTHONPATH and PyYAML installed:

```bash
PYTHONPATH=/path/to/platform-tools python3 scripts/prepare_library_jetstream.py \
  --run-id '<fresh-library-js-run>' --activation-time '<future-UTC-epoch>'
```

The planner requires at least ten minutes of margin. Regeneration updates only
the candidate ConfigMap/Jobs and preserves existing frozen run evidence.

Rollback in GitOps first disables the new collector event mode/DSN dependency,
then stops the three event workloads. Use the generated run-scoped quiesce SQL
through the approved DB path. Preserve streams, evidence, inboxes and checkpoints.
Do not simply scale the old observer back up: managed bindings now identify the
new event run. Restoring the old binding/image/run requires a separate reviewed
transaction. Other strategy deployments and execution authority remain unchanged.
