# Donchian V2 research observer rollout

Prepared 2026-10-20. This bundle is not evidence of live activation.

- Source: `strat-relay/platform-tools@97ea3190bf563bb2bd0f67b6f9d2a8428b7e52e7` (PR 228, successful Release run 39461675503).
- Run: `donchian-v2-20261020-1830-v1`.
- T0: **2026-10-20 18:30:00 UTC / 12:30:00 MDT** (1792521000).
- Instance: `47493bab-d8f7-5a0a-942e-11867612307a`; definition `BREAKOUT_DONCHIAN_BREAKOUT`, version `V2`.
- Scope: BTCUSD/BTCUSDm, XAUUSD/XAUUSDm, USDJPY/USDJPYm; native completed H1/M15 cache data, 320-bar warm-up.
- Runtime: `ghcr.io/strat-relay/trading-platform-runtime@sha256:19b2f7661922176edab958ad0db3115b490822019adb71daedb73e6d7ccf89e0`.
- API: `ghcr.io/strat-relay/trading-platform-control-api@sha256:cf57571bf20b84134d49597088e342809db7974b0e00491b6fe3ab28abdbd407`.

## Scope and effect

The active kustomization adds only the V2 ConfigMap, Deployment and NetworkPolicy. Only the `orchestrator-shadow` container and `platform-signals-api` image are repinned. Updating the orchestrator recreates its shared pod, restarting its Context containers with their original images, configuration and PVC. Existing V1 library workloads and identities remain unchanged. The global release manifest and unrelated image pins remain unchanged because this is a scoped rollout.

Research-only, execution OFF/ineligible; no broker access. Research entries can reach the canonical orchestrator publication path after activation. Prospective entries and completed-candle reconstructions retain distinct evidence classes. This deployment does not validate profitability or authorize execution.

## Database prerequisite and order

Use the existing approved PostgreSQL admin/migration connection to the verified `trading` database. Do not print credentials, copy Secrets, grant new privileges, or substitute an observer credential. These files are operator-run SQL, not automatically applied numbered migrations.

1. Confirm current UTC time is before T0, the database target is correct, and the existing observer DB, Redis auth and image-pull Secret references are present. Inspect names only, never values.
2. Apply `registration.sql` with stop-on-error enabled. This creates the separate frozen V2 version, parameter set, offline/ineligible instance and three memberships. It does not repoint the shared V1 definition.
3. Apply `activation.sql` with stop-on-error enabled **before T0**. It atomically binds only the V2 instance and initial checkpoint to this run, image and activation boundary. Stop on any conflict; never overwrite a checkpoint. Do not rerun registration after activation because its offline guard intentionally rejects active instances.
4. Merge the scoped GitOps PR and allow normal Flux reconciliation. No manual restart or forced reconciliation is required. The observer init container fails closed until it can verify the activation attestation and source binding.

For an already configured approved local `psql` connection (connection arguments deliberately omitted):

```bash
psql -X --set=ON_ERROR_STOP=1 --file=docs/research/donchian-v2-20261020-1830-v1/registration.sql
psql -X --set=ON_ERROR_STOP=1 --file=docs/research/donchian-v2-20261020-1830-v1/activation.sql
```

If T0 expires before activation SQL commits, generate and review a fresh run bundle with a future T0. Do not edit only the ConfigMap timestamp, backdate activation, or reuse this run ID with different bindings. A rollout delayed after successful activation retains the original T0; record the actual first healthy cycle and label any catch-up evidence accurately.

## Read-only verification

Run from the checked-out GitOps PR after normal reconciliation:

```bash
mkdir -p donchian-v2-report
kubectl --kubeconfig "$HOME/Downloads/local.yaml" --context local -n trading get deployment donchian-breakout-v2-observer mt5-native-bridge-main-runtime platform-signals-api -o wide > donchian-v2-report/deployments.txt 2>&1
kubectl --kubeconfig "$HOME/Downloads/local.yaml" --context local -n trading get pods -l app.kubernetes.io/name=donchian-breakout-v2-observer -o wide > donchian-v2-report/pods.txt 2>&1
kubectl --kubeconfig "$HOME/Downloads/local.yaml" --context local -n trading logs deployment/donchian-breakout-v2-observer -c verify-research-registration --tail=100 > donchian-v2-report/registration-check.txt 2>&1
kubectl --kubeconfig "$HOME/Downloads/local.yaml" --context local -n trading logs deployment/donchian-breakout-v2-observer -c observer --since=30m --timestamps > donchian-v2-report/observer.txt 2>&1
kubectl --kubeconfig "$HOME/Downloads/local.yaml" --context local -n trading exec deployment/donchian-breakout-v2-observer -c observer -- python -m research.strategy_library_forward health --manifest /config/run.json > donchian-v2-report/health.json 2>&1
```

Verify actual image IDs, Ready state/restarts, successful causal warm-up, persisted state under the exact run ID, three healthy instruments, OFF/ineligible managed policy and V2 catalog ownership. Once an accepted entry occurs, trace its version/parameter/run provenance into canonical publication and theoretical outcomes; no setup is an acceptable result. Confirm zero execution intents/broker writes attributable to V2. Readiness or a published signal alone does not prove all these gates.

## Quiesce

Apply `quiesce.sql` through the same approved admin path to disable only this V2 instance's publication. Separately change only the V2 Deployment replicas to zero through GitOps. Either step alone does not stop both publication and observation. Preserve all manifests, events, checkpoints and outcomes. Never disable the shared Donchian parent or alter V1 to stop V2. Keep the new orchestrator image while pending V2 publications/outcomes may still require V2 support.
