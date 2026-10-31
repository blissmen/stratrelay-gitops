# Canary and ICT/SMC continuation recovery

The Rancher screenshot shows strategy-library-canary-v1-observer and
ict-smc-forward-v1-observer crashing on 0318108d, while Donchian has returned to
6feabc4e. PR #515 intentionally reverted only Donchian; these two overlays kept
the newer image. PR #512 changed all three without new run IDs.

Restore only Canary and ICT/SMC main images to the preceding desired digest
6feabc4e2afb42944850a16eb19c616a13ed1c07421bee4305a815f696f0a735. Each receives a
new verify-continuation-binding init container using that same image. It requires
the approved continuation config and a saved binding/config/artifact match before
startup. Missing state or an event-cutover attestation blocks startup. Canary's
existing verify-no-event-cutover init guard remains in place.

The guard uses existing read-only credential references and reports hashes and
match flags. It never resets, deletes or rewrites checkpoints, streams, binding
fields or attestations. It does not alter strategy parameters, T0, memberships,
publication settings or broker execution. The new guard ConfigMap contains no
replacement strategy code. Donchian and Swing resources are unchanged.

The previous digest is a recovery candidate until each actual init report proves
BINDING_MATCH. If not, preserve state and inspect that report; do not remove the
guard or try arbitrary images. After Flux reconciliation, require both init guards
to finish, current evaluations to advance, and observer readiness. Expected closed
market data and true data gaps must be diagnosed separately from identity failures.
The screenshot still shows Donchian unready; Running alone is not proof of health.

Live cluster access is unavailable in this workspace. This change therefore does
not claim that either observer has already recovered.
