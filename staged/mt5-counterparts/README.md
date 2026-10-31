# Eight MT5 research counterparts

Companion to the platform-tools MT5 counterpart registration/runtime change.
This directory and the preparation workflow are tooling, not active resources.
Merging this tooling alone does not register or activate observers.

After the platform source PR merges and Release builds its main-origin runtime,
open **Actions → Prepare MT5 counterpart forward activation → Run workflow**.
Supply the verified immutable runtime image digest from that release. No local
terminal is required. The workflow generates both plans inside that exact image,
chooses fresh run identities and a T0 at least one hour ahead, then opens a new
activation PR. Review package access, existing credentials and T0 before merge.

The activation PR contains two isolated observer/orchestrator pairs, immutable
manifests, additive registration SQL and guarded activation Jobs. Jobs reuse the
established trading-postgres-auth Secret through secretKeyRef. No values are
copied or printed. library-event-observer, library-event-orchestrator,
trading-signal-cutoff, trading-redis-auth and ghcr-pull must already exist and be
usable. MARKET_EVIDENCE and LIBRARY_RESEARCH must authorize the new run consumers.

Five M15/H4 counterparts cover BTCUSD, XAUUSD and USDJPY. Three native UTC D1
counterparts cover BTCUSD only. The collector event-scope patch runs after the
older BTC-only patch and includes all three counterpart providers, without
changing the collector image, credentials or native D1 configuration.

Registration and activation execute atomically. Existing run IDs are rejected;
parameters remain frozen and execution OFF/ineligible. No existing observer's
image, checkpoint or binding is changed. The activation Job does not update the
migration-checksum ledger; the normal migration runner records migration 084
later if the Job registered first. Registration replay proves immutable identities
and OFF/ineligible execution while preserving already activated run attributes.
Activation still requires an offline instance and a new, unused run identity.

Before declaring success, require Job completion, init verification, current
evaluations and acknowledgment of actual research publications. No synthetic
signal is injected. Closed-market FX/metal streams may be dormant.

If T0 expires, rerun preparation with new identities; do not rewrite the old
binding or clear state. If activation fails, inspect the Job error type, database
privileges, Secret references and network policy. Repeated pod deletion cannot
repair missing credentials or a state mismatch.

Rollback uses each generated quiesce.sql followed by scaling down only the new
counterpart deployments. Keep all evidence and leave shared parent gates intact.
