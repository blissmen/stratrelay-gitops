# Fresh BTC-only Swing research cutover

Run: `swing-position-20261031-r1`. T0: **2026-10-31 11:59:00 UTC**
(05:59 MDT). Source `3503cf827c7590ade4b3f56a2a0034e5c4b7a9d8`, successful
release [41198150787](https://github.com/strat-relay/platform-tools/actions/runs/41198150787).
The exact runtime digest and source-file hashes are in `release.json`.
Registry manifest/config hashes and OCI source labels were verified before staging.

Prerequisites verified before this cutover: namespace UID, primary trading_platform,
exact migrations 070/078/083, five frozen OFFLINE registrations with no prior run,
all four real event bindings and effective DB privilege allowlists, shared NATS
login/permissions, newly provisioned bounded MARKET_EVIDENCE/LIBRARY_RESEARCH,
and BTC native causal H1/H4/D1 warm-up plus a DATA_READY fresh bid/ask observation.
Source scheduler #277 is deployed. Bridge latency can still make quotes stale;
every entry retains the five-second provider/cache gate without exceptions.

The one-shot activation Job uses existing provisioning Secret key references
and the planner's guarded transaction. No admin DSN is stored in runtime Secrets.
Its only network path is PostgreSQL/DNS; it has no service-account token.
Observer/orchestrator startup verifies the resulting run, fingerprints and OFF/
ineligible registration. Both use dedicated restricted event DB credentials.
The collector emits immutable events only for BTCUSDm; all existing memberships,
polling ownership and scheduling hints remain unchanged. No second bridge poller.

All five instances must remain RESEARCH, online=true, signal_publication=true,
execution_mode=OFF and execution_eligible=false. Canonical publication uses the
dedicated research orchestrator; ordinary orchestrator discovery skips event-owned
instances. Existing unrelated BTC observers are not rebound or restarted.

## Verification and rollback

Verify the Job's committed marker, observer/orchestrator Ready, exact pod image IDs,
persisted post-T0 checkpoint/evaluation progress for every key, and outbox/relay
publication. Ready alone is not activation proof. Initial context reconstruction is
not a prospective evaluation. Daily strategies cannot produce a new daily-close
evaluation until 2026-11-01 00:00 UTC; report zero evaluations as pending, not success.
Only trace a naturally accepted research entry through immutable evidence, the
research stream, canonical entry signal and canonical outbox. Never inject a setup.

To quiesce, use `quiesce.sql` through the approved primary admin path and a focused
GitOps patch scaling only these two Swing Deployments to zero. Keep event history,
canonical records, frozen parameters and all run state. If collector event production
needs rollback, revert its event binding/mode to OFF while retaining shared collection.
Do not restart the expired old run, clear streams, reset cursors or drop credentials.
If this Job misses T0 or fails, investigate and generate an entirely fresh bundle;
never edit this run's timestamps or remove guards. The original paused Job and its
ConfigMap remain in `historical-resources.json`; original source files are unchanged.
