# Remaining live injection and orchestrator activation

Implementation #256 and preparation #463 are merged. Offline catalog injection
is supplied by platform-tools #258, migration 078. No strategy is claimed live
until the cluster/database checks below succeed.

## Collector prerequisite PR

The focused collector change uses the published immutable runtime from source
`3d401c65daf60e23008ec37b04cf2871f6eaeba7`:

`ghcr.io/strat-relay/trading-platform-runtime@sha256:a38fe6a6a9ae7af9383353216f9f66d65a9e09ebab0d27f0f2a28ccc8a43ce4f`

Release evidence:
https://github.com/strat-relay/platform-tools/actions/runs/40642359092

Only the existing collector's image and BTC optional-D1 setting change. No new
Secret, strategy workload, T0 or publication-mode setting is introduced. Native
D1 being unavailable must not replace the four existing timeframe series.

## Verify actual data without broker requests

After Flux deploys the collector change, run on the authorized local machine:

```sh
kubectl --kubeconfig "$HOME/Downloads/local.yaml" --context local -n trading \
  exec deployment/market-data-collector -c collector -- \
  python -m research.swing_position_forward data-readiness
```

Require DATA_READY with 320 completed H1/H4/D1 candles, UTC/24-7 continuity,
causal watermarks, tick size and fresh bid/ask. If blocked, collect the redacted
exception type and D1 health; do not modify its validation or fabricate bars.

## Register, bind and publish

1. Merge/release platform #258, then apply migration 078 through the authorized
   migration path with an image containing that migration. Verify its recorded
   checksum. Confirm five distinct V1 instances are OFFLINE, RESEARCH, execution
   OFF and ineligible. The earlier collector image predates migration 078.
2. Verify existing collector/observer/orchestrator event credentials and scoped
   subject ACLs, primary `trading_platform`, migration 070, bounded
   MARKET_EVIDENCE/LIBRARY_RESEARCH streams and relay. Do not assume these are
   available merely because a Secret name appears in a template.
3. Prepare a fresh run from the exact reviewed runtime image using prepare.py.
   Review the generated collector event binding/patch separately: daily data
   readiness alone does not enable durable library event production.
4. Apply the guarded future-T0 activation transaction only after those checks.
   It binds all five managed instances to the new run, turns observation online,
   enables research publication and keeps execution OFF/ineligible.
5. Add the generated observer/orchestrator workload.json and its source/destination
   network policies through a reviewed activation GitOps PR. Do not deploy these
   workloads against absent credentials or an unapplied activation transaction.
6. Confirm all five streams progress. For each real accepted entry, reconcile
   exact IDs across research entry, research JetStream notification, canonical
   orchestrator signal and entry-created outbox publication. Retain that evidence
   in the deployment report. Do not inject fabricated test signals into production.

The event path is shared market evidence -> Swing research observer -> durable
research-entry notification -> canonical orchestrator publisher -> canonical
outbox. Account execution eligibility is not granted at any step.

If access is unavailable, report the precise blocker. Never report an offline
registration, merge or image publication as completed live activation.
