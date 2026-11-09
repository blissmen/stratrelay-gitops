from copy import deepcopy
import os
from pathlib import Path
import unittest
import yaml
from check_context_v1_runtime_rollout import validate,MAIN


@unittest.skipUnless(os.getenv('CONTEXT_V1_RENDERED_MANIFEST'),'rendered manifest not configured')
class ContextRuntimeRolloutTests(unittest.TestCase):
    def setUp(self):
        self.documents=list(yaml.safe_load_all(Path(os.environ['CONTEXT_V1_RENDERED_MANIFEST']).read_text()))
        self.main=next(d for d in self.documents if d and d['kind']=='Deployment' and d['metadata']['name']==MAIN)
        self.spec=self.main['spec']['template']['spec']
        self.cron=next(d for d in self.documents if d and d['kind']=='CronJob' and d['metadata']['name']=='signal-confidence-reconcile')

    def rejected(self):
        with self.assertRaises((AssertionError,KeyError)):
            validate(self.documents)

    def test_coordinated_render_is_valid(self):
        self.assertTrue(validate(self.documents)['configuration_valid'])

    def test_concurrent_old_source_is_rejected(self):
        self.main['spec']['strategy']['type']='RollingUpdate';self.rejected()

    def test_writable_import_source_is_rejected(self):
        self.spec['initContainers'][1]['volumeMounts'][0]['readOnly']=False;self.rejected()

    def test_import_before_schema_is_rejected(self):
        self.spec['initContainers'].reverse();self.rejected()

    def test_mismatched_component_storage_mode_is_rejected(self):
        for c in self.spec['containers']:
            if c['name']=='context-post-exit-observer':
                next(e for e in c['env'] if e['name']=='CONTEXT_V1_STORAGE_MODE')['value']='FILE'
        self.rejected()

    def test_unpublished_tag_instead_of_digest_is_rejected(self):
        next(c for c in self.spec['containers'] if c['name']=='context-paper')['image']='ghcr.io/strat-relay/trading-platform-runtime:latest'
        self.rejected()

    def test_mismatched_reconcile_image_is_rejected(self):
        self.cron['spec']['jobTemplate']['spec']['template']['spec']['containers'][0]['image']='other-image'
        self.rejected()

    def test_unbounded_reconcile_batch_is_rejected(self):
        self.cron['spec']['jobTemplate']['spec']['template']['spec']['containers'][0]['args']=['--limit','1000']
        self.rejected()

    def test_overlapping_reconciliation_is_rejected(self):
        self.cron['spec']['concurrencyPolicy']='Allow';self.rejected()

    def test_missing_database_network_path_is_rejected(self):
        self.documents=[d for d in self.documents if not (d and d['metadata']['name']=='signal-confidence-reconcile-postgres-ingress')]
        self.rejected()

    def test_mismatched_release_provenance_is_rejected(self):
        with self.assertRaises(AssertionError):
            validate(self.documents,{'images':[{'name':'trading-platform-runtime','reference':'another-digest'}]})


if __name__=='__main__':unittest.main()
