# Fresh replacement Swing research activation

Run `swing-position-20261031-r2`, T0 **2026-10-31 12:10 UTC / 06:10 MDT**.
Exact source `ecf400f2877b2155d5eae370d7cb9adc98be2c59`, successful
[release 41198894404](https://github.com/strat-relay/platform-tools/actions/runs/41198894404).
Immutable runtime digest and source hashes: `release.json`. The OCI manifest,
platform manifest and config hashes and source labels were verified against the
published digest. Preparation used a clean checkout of that exact source.

This is a complete fresh planner bundle, not an edit or reset of r1. The r1
activation committed but its startup gate rejected Python tuple versus JSON array
parameter equality. It was quiesced with its run-scoped SQL before T0; both Swing
Deployments were scaled to zero. Its state has zero streams/evaluations/signals,
and all five strategy IDs have zero execution intents/results. The state and all
historical files are retained. `historical-resources.json` retains both earlier
ConfigMaps and Jobs, including the suspended October-28 Job and completed r1 Job.

Platform #279 compares complete actual JSON canonical value hashes against the
unchanged frozen parameter hashes; it does not trust stored fingerprints alone.
The shared relay now verifies primary/outbox permissions and bounded streams,
independently of the expired October-22 strategy run. It has published real BTC
market evidence successfully. Dedicated event-role logins and effective privilege
allowlists were verified, including a credential-reuse rerun with no new roles,
Secrets, password rotations or grants repair. Migrations 070/078/083 match exactly.

Scope remains BTCUSD -> BTCUSDm only, five frozen Swing definitions, observation
and canonical research publication ON, execution OFF/ineligible. Existing shared
collector only; event emission is explicitly BTC-only. No global execution policy
or account routing change. No admin DSN in runtime bindings. The one-shot guarded
activation Job references the existing provisioning Secret and has no API token,
no broker path, no retries and a 120-second deadline.

## Acceptance and rollback

Verify committed Job, exact images, both runtime pods Ready, and persisted post-T0
strategy progress. Startup/context reconstruction is NOT a prospective evaluation.
Expected first new execution-candle boundaries after T0: H4 Structure Pullback
13:00 UTC; Weekly Range Break + Retest and H4 Momentum Continuation 16:00 UTC;
Daily Channel Breakout and Daily EMA Pullback 2026-11-01 00:00 UTC. Until real
evaluations persist, report that acceptance criterion as pending, not success.
Only trace a naturally accepted entry to canonical signal/outbox; never inject one.
Intermittent bridge latency still rejects stale quotes under the unchanged
five-second provider/cache gate. Never manufacture bars or loosen quote policy.

Rollback: execute this run's `quiesce.sql` through the approved primary path and
scale only the two Swing Deployments to zero through GitOps. Preserve run state,
frozen parameters, canonical history and streams. If event production must be
disabled, return the collector event mode to OFF without disabling collection.
If activation fails or T0 expires, inspect the attempt and generate another full
future-T0 bundle. Never edit immutable source attestations, reset a run or restart
the expired October-28 candidate.
