# Donchian V2 activation recovery

Supersedes unactivated run donchian-v2-20261020-1830-v1. The original deployment's init container rejected missing registration/activation state; the original T0 expired.

Generated using the pinned deployed runtime's research.donchian_v2_forward.plan. Run: donchian-v2-20261020-2111-v1. T0: 2026-10-20 21:11 UTC / 15:11 MDT (1792530660). Same frozen V2 identity, parameters, image and three-instrument scope as the original rollout. Execution remains OFF and ineligible; publication is research-only.

Apply registration.sql then activation.sql with ON_ERROR_STOP using the existing admin connection to the verified trading_platform database, before T0. Activation includes identity, scope, checkpoint and time guards. Do not backdate or overwrite existing checkpoints. The active workload references a fresh immutable ConfigMap. See the original rollout README for verification and quiesce procedures.
