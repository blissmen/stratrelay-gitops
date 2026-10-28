# Staged Swing/Position research enrollment

Companion: https://github.com/strat-relay/platform-tools/pull/256

This directory is deliberately **not referenced by Flux or the active trading
Kustomization**. It contains no mutable image tag, placeholder digest, expired
activation time, automatic registration Job or Secret value.

The five existing V1 definitions are H4 Structure Pullback, Daily Channel
Breakout, Daily EMA Pullback, Weekly Range Break + Retest, and H4 Momentum
Continuation. Initial scope is BTCUSD/BTCUSDm. Broker execution is DISABLED;
all five instances remain RESEARCH, execution OFF and ineligible.

## Why this is staged

Four definitions require D1 data absent from the old default collector contract.
The implementation adds isolated optional native UTC/24-7 daily evidence. Native
broker-server daily bars with offset boundaries must fail readiness rather than
being relabeled UTC. There is no verified new image or live data attestation yet.
FX/metals require separately reviewed closure handling and are excluded.

Existing FVG, Order Block, canary, Context, Kojo, PA and Donchian workloads and
their images/checkpoints are not repinned or changed by this staged PR.

## Prepare the concrete change after the runtime release

Use a clean isolated checkout of the reviewed platform commit. Obtain the exact
published image digest and save its OCI labels (for example `docker image inspect`
for that digest). The preparer verifies the source revision label; preserve the
release/build evidence that binds those labels to that digest.

```sh
python3 staged/swing-position/prepare.py \
  --platform-root "$SWING_PLATFORM_CHECKOUT" \
  --source-commit "$SWING_SOURCE_COMMIT" \
  --image "$SWING_RUNTIME_DIGEST" --image-labels "$SWING_IMAGE_INSPECT_JSON" \
  --run-id "$SWING_RUN_ID" --activation-time "$SWING_T0_EPOCH" \
  --output "staged/swing-position/runs/$SWING_RUN_ID"
```

All variables are operator-selected verified values. T0 must be at least ten
minutes in the future. The preparer only writes files and never applies them.
Review generated registration.sql, activation.sql, quiesce.sql, run.json,
release.json, workload.json, collector.patch.json and readiness.json.

## Two ordered GitOps changes and explicit database steps

1. Review the collector patch first, including the exact image, existing endpoint
   and primary-DB bindings, optional D1 scope, shared read-bridge command budget,
   and `library-event-collector` event connection binding. The base collector's
   default four-timeframe source contract remains unchanged.
2. Ensure migration 070, MARKET_EVIDENCE/LIBRARY_RESEARCH bounded streams, relay,
   NATS ACLs and referenced `library-event-observer`,
   `library-event-orchestrator`, `library-event-collector`, `trading-signal-cutoff`,
   Redis-auth and GHCR bindings actually exist. Provision missing credentials via
   the approved path; do not copy broker/admin credentials.
3. Require the runtime's `research.swing_position_forward data-readiness` check
   against real cache data: 320 completed H1/H4/D1 bars, UTC/24-7 continuity,
   current watermarks, valid tick size and fresh bid/ask. Check existing workloads
   remain healthy after the collector rollout.
4. Review/apply offline registration.sql through the authorized DB path. It
   registers five distinct managed instances and BTC memberships without enabling
   a strategy or overwriting conflicting identities.
5. Freeze a fresh T0 bundle. Review/apply activation.sql before T0. It checks the
   trading_platform primary, research-only identity and scope, and refuses an
   existing run/checkpoint or elapsed T0.
6. Add the **generated workload.json** as a resource in the active trading
   Kustomization in a subsequent reviewed activation PR. The generated network
   policies include both source egress and destination PostgreSQL/Redis/NATS
   ingress. Do not merely add the staged directory as a resource.
7. Verify both new processes are Ready, all five managed instances appear, and
   observation progresses at their H1/H4/D1 boundaries. When a setup qualifies,
   trace its accepted entry through research JetStream and the canonical
   publisher/outbox. A zero signal count alone does not establish a fault.

Do not bulk-repin existing frozen observer images. Verify source hashes recorded
in the activation attestation match the running runtime; in-pod source edits are
not permitted. No broker execution authority is enabled at any step.

## Rollback

Quiesce/scale down only the two new Swing workloads through GitOps and apply the
generated run-scoped quiesce.sql. Preserve all run manifests, source attestations,
events, checkpoints and theoretical outcomes. Never reset a checkpoint to make
it match a different image. Remove optional D1 collection only when no other
reviewed consumer needs it. No historical performance identity is rewritten.
