import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('swing_stage',ROOT/'staged/swing-position/stage_live.py')
stage=importlib.util.module_from_spec(spec);spec.loader.exec_module(stage)


class StageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        old=self.root/'apps/trading-platform/swing-position-20261028';old.mkdir(parents=True)
        source=ROOT/'apps/trading-platform/swing-position-20261028/workload.json'
        (old/'workload.json').write_bytes(source.read_bytes())
        self.original=json.loads(source.read_text())
        bundle=ROOT/'apps/trading-platform/swing-position-20261031-r1'
        with contextlib.redirect_stdout(io.StringIO()):stage.stage(bundle,self.root)
        self.output=self.root/'apps/trading-platform/swing-position-20261031-r1'

    def test_relay_init_and_readiness_are_run_independent_and_image_bound(self):
        patch=json.loads((self.output/'relay.patch.json').read_text())
        pod=patch['spec']['template']['spec'];init=pod['initContainers'][0];relay=pod['containers'][0]
        self.assertEqual(init['image'],relay['image'])
        self.assertIn('check_streams',init['command'][2])
        self.assertNotIn('run.json',init['command'][2])
        self.assertNotIn('strategy_library_forward_state',init['command'][2])
        compile(init['command'][2],'<relay-init>','exec')
        self.assertEqual(relay['readinessProbe']['exec']['command'][-1],'health')

    def test_original_paused_job_and_config_are_preserved(self):
        archived=json.loads((self.output/'historical-resources.json').read_text())['items']
        self.assertEqual(archived,[x for x in self.original['items'] if x['kind'] in ('ConfigMap','Job')])
        self.assertTrue(next(x for x in archived if x['kind']=='Job')['spec']['suspend'])

    def test_activation_is_bounded_and_uses_provisioning_refs_not_admin_dsn(self):
        job=json.loads((self.output/'activation-job.json').read_text())
        self.assertEqual(job['spec']['backoffLimit'],0)
        self.assertEqual(job['spec']['activeDeadlineSeconds'],120)
        pod=job['spec']['template']['spec']
        self.assertFalse(pod['automountServiceAccountToken'])
        c=pod['containers'][0];compile(c['command'][2],'<activation>','exec')
        self.assertEqual({e['name'] for e in c['env']},{'PGUSER','PGPASSWORD'})
        self.assertTrue(all(e['valueFrom']['secretKeyRef']['name']=='trading-postgres-auth' for e in c['env']))
        self.assertNotIn('postgresql://',json.dumps(job))


if __name__=='__main__':unittest.main()
