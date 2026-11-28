# SR hypotheses rollout preparation

Requires the platform SR hypothesis forward-registration change and its #387 prerequisite to be merged and built. No production image or activation timestamp is supplied by this PR.

Generate a fresh bundle from the exact released runtime with `research.sr_hypotheses_forward plan`, group `sr-hypotheses`, unique run ID, immutable image digest and future T0. Migration 099 must be applied before activation. Then run:

```sh
python scripts/stage_sr_hypotheses.py --bundle /absolute/path/to/bundle --root .
python -m unittest discover -s scripts -p test_stage_sr_hypotheses.py -v
```

Review the generated files in a separate rollout PR. The stager adds one observer, a guarded activation Job, existing collector scope patches and one manifest append to `library-orchestrator-unified`. Existing manifest arguments stay in place. It records the observer image and binding in the frozen research lock. It rejects a duplicate SR attachment, unexpected instruments/keys, replacement of shared command arguments and an elapsed activation time.

No cluster calls, credentials or broker orders occur during staging. Runtime Secret references must already exist. Review Kubernetes rendering and release checks before rollout. After deployment, prove all four evaluations advance and trace a naturally accepted entry through the shared worker into canonical publication. Keep execution disabled.

Rollback through the bundle's quiesce operation and a reviewed GitOps removal of this observer and its manifest attachment. Retain historical research rows and binding evidence.
