import json
from pathlib import Path
import shutil
import tempfile
import unittest
from stage_mt5_counterparts import stage


class StageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'gitops';self.bundle=Path(self.temp.name)/'bundle'
        source=Path(__file__).resolve().parents[1]
        shutil.copytree(source/'apps/trading-platform/swing-position-20261031-r2',
                        self.root/'apps/trading-platform/swing-position-20261031-r2')
        (self.root/'apps/trading-platform/kustomization.yaml').write_text('resources:\n  - existing.yaml\npatches:\n  - path: existing.patch.json\n')
        self.bundle.mkdir()
        self.config={'run_id':'counterpart-test','activation_time':2000000000,'execution_eligible':False,
            'transport':'JETSTREAM','symbols':{'BTCUSD':'BTCUSDm','XAUUSD':'XAUUSDm','USDJPY':'USDJPYm'}}
        self.image='ghcr.io/strat-relay/trading-platform-runtime@sha256:'+'a'*64
        self.write()

    def write(self):
        cm={'kind':'ConfigMap','metadata':{'name':'counterpart-test-manifest'},'data':{'run.json':json.dumps(self.config)}}
        files={'run.json':self.config,'readiness.json':{'image':self.image},'workload.json':{'items':[cm]}}
        for name,data in files.items():(self.bundle/name).write_text(json.dumps(data))
        for name,text in {'registration.sql':'INSERT INTO example VALUES (1);','activation.sql':'BEGIN; SELECT 1; COMMIT;','quiesce.sql':'SELECT 1;'}.items():
            (self.bundle/name).write_text(text)

    def test_atomic_job_and_collector_patch_preserve_existing_resources(self):
        dest=stage(self.bundle,self.root,now=1999999000)
        job=json.loads((dest/'activation-job.json').read_text())
        container=job['spec']['template']['spec']['containers'][0]
        self.assertEqual(container['image'],self.image)
        self.assertIn("activation.replace('BEGIN;'",container['command'][2])
        self.assertTrue(all('secretKeyRef' in item['valueFrom'] for item in container['env']))
        text=(self.root/'apps/trading-platform/kustomization.yaml').read_text()
        self.assertIn('existing.yaml',text)
        self.assertLess(text.index('existing.patch.json'),text.index('collector-event-scope.patch.json'))
        with self.assertRaises(FileExistsError):stage(self.bundle,self.root,now=1999999000)

    def test_expired_t0_or_execution_eligibility_rejected(self):
        with self.assertRaisesRegex(ValueError,'fresh T0'):stage(self.bundle,self.root,now=2000000000)
        self.config['execution_eligible']=True;self.write()
        with self.assertRaisesRegex(ValueError,'research-only'):stage(self.bundle,self.root,now=1999999000)


if __name__=='__main__':unittest.main()
