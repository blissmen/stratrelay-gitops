# Complete both corrected PA observers

Concrete fresh run: `pa-ticks-20261019-1745-v3`.
Planned publication boundary: **2026-10-19 17:45 UTC / 11:45 AM Mountain**.
Source: `afb2da484022afce35bcbae6ce06639d9b015477`.
Pinned runtime: `sha256:87328a82d1024e882ef2bf4ee7bd928695625422be9d2a3b0a0dc3970db9ae18`.

| Strategy | Instance | Workload |
|---|---|---|
| S/R Retest V1 | a18be906-f63b-5a3c-981b-36e8f3950c99 | pa-tick-sr-29ad478ca8 |
| Engulfing Continuation V1 | c5d62982-e482-52c1-94b3-93cc0f06ad4d | pa-tick-engulf-29ad478ca8 |

The cluster-connected operator reported successful dedicated-role provisioning,
privilege audit, Secret creation and registration of the unused 17:40 bundle.
Its local-only commit `584c45d` is unavailable in this environment. This PR
recreates a valid M15-boundary bundle from the released generator instead of
pretending to push that unseen commit or editing its registered frozen binding.

The expired 17:00 ConfigMap, SQL and identity history remain intact. Its two
Deployments are scaled to zero. The unstarted 17:40 registrations are not deleted
or silently rebound. Both original legacy deployments remain at zero. The
registration/activation runner refuses to proceed if either superseded corrected
run has durable checkpoint/evaluation evidence; inspect and preserve that run if
this gate fires. No historical checkpoint, signal or outcome is rewritten.

## GitOps reconciliation sequence

All resources are in `apps/trading-platform/price-action-corrected-20261019-1745.json`.
`workload.json` here is an identical review copy. JSON formatting keeps the frozen
image outside the bulk release YAML repinner. Never repoint this run's image.

1. `pa-tick-register-29ad478ca8` uses exactly the existing schema migration
   Job's `envFrom` credential reference, only for the included scoped registration
   SQL. It mounts the dedicated observer Secret separately, verifies that both
   connections reach the same PostgreSQL primary, audits observer role attributes
   and unrelated table writes, rejects older corrected evidence, and registers
   only these two fresh OFFLINE research identities. It does not run migrations,
   change credentials/grants, enable execution, or invoke broker code.
2. Each observer's read-only init guard uses only the dedicated observer Secret
   and requires exact registered version/parameter/run/image/manifest/T0 bindings,
   expected research privileges, no elevated role attributes or unrelated writes,
   and execution OFF/ineligible. Missing registration retries naturally. First
   bootstrap must occur before 17:45 UTC; if it does not, preserve the failed run
   and generate another immutable bundle rather than backdating or bypassing the
   guard. Initial cache bootstrap emits no entries.
3. `pa-tick-activate-29ad478ca8` uses the same existing migration credential
   reference for activation SQL only. It verifies the same primary and dedicated
   credential, waits at most 25 minutes for both fresh RUNNING three-instrument
   checkpoints with finite positive ticks, reruns read-only registration guards,
   then applies `activate.sql`. The SQL verifies exact source/manifest/timing/T0
   bindings, membership and fresh timestamps before enabling only these two
   research publication instances. Publication ignores entries before the planned
   boundary; actual activation, bootstrap and evidence times are separate facts.
4. New observers read only the canonical Redis cache every 10 seconds with H1/M15
   warmup. Their credentials cannot write canonical signals/outbox/execution.
   The orchestrator publishes accepted research entries, with theoretical outcomes
   kept separate from broker results. No setup yet is not a rollout failure.

Both Jobs have hard deadlines, `backoffLimit: 0`, no service-account token, nonroot
read-only filesystems, and only PostgreSQL/DNS network access. Postgres ingress
allows the exact two observer and two Job names; Redis ingress only the observers.
Neither Job creates a credential or exposes a Secret value. Failure logs print
sanitized static reasons or exception class names. If the existing migration role
cannot perform this scoped SQL, stop on that concrete privilege failure; never
fall back to or copy execution/admin credentials into observer workloads.

## Evidence still required after merge

From context `local`, namespace `trading`, verify Flux revision, both Job completion
statuses/logs, observer init success and actual imageIDs. Inspect fresh durable
checkpoints, provider ticks, source bindings and per-instrument status. Confirm
orchestrator discovery of both UUIDs/explicit parameter references and zero
execution intents/order attempts. Follow canonical publication and theoretical
resolution when a post-T0 accepted entry occurs. Preserve legacy outcomes.

Save sanitized actual activation/bootstrap/first-evidence timestamps and status
to Downloads. Merging this PR proves desired state only, not successful live
activation. Cluster API access from this workspace is still blocked by HTTP 403.

For rollback, apply quiesce.sql through the same controlled admin path and scale
only these new observers to zero through GitOps. Retain ConfigMaps and ledgers.
Do not change the old frozen runs or disable unrelated workloads.

Offline verification:

```bash
python3 ops/price-action/pa-ticks-20261019-1745-v3/verify_bundle.py
python3 ops/price-action/pa-ticks-20261019-1745-v3/test_run_cutover.py
```

## Mounted-script import repair (r2)

The first Jobs ran `python /cutover/run_cutover.py`; the observer init ran
`python /config/preflight.py`. Python then put the mounted directory on sys.path,
not `/app`, so the platform's `postgres` package could not be imported. This was
reproduced without making a database call. Set `PYTHONPATH=/app` on both observer
containers/init containers and the replacement `-r2` Jobs. Keep the same run,
T0, instance IDs, manifest contents, frozen parameters, image and safety gates.

Immutable ConfigMaps receive `-r2` names with the same bound manifest data and
only safer missing-module diagnostics. Failed original Jobs and their ConfigMaps
remain as history. New Job names permit Kubernetes to execute the repaired entry
commands; no manual restart is needed. Database and observer admission guards are
unchanged. Logs now include a missing module's name, while other connection errors
remain sanitized to exception class names.
