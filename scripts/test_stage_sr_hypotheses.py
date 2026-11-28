import json
from pathlib import Path
import shutil
import tempfile
import unittest
from stage_sr_hypotheses import stage


class SRStageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'gitops';self.bundle=Path(self.temp.name)/'bundle'
        source=Path(__file__).resolve().parents[1]
        shutil.copytree(source/'apps/trading-platform/swing-position-20261031-r2',
                        self.root/'apps/trading-platform/swing-position-20261031-r2')
        self.top=self.root/'apps/trading-platform/kustomization.yaml'
        self.top.write_text('resources:\n  - existing\npatches:\n  - path: existing.patch.json\n')
        shared=self.root/'apps/trading-platform/library-orchestrator-unified';shared.mkdir()
        self.command=['python','-m','research.library_event_service','multi-orchestrator','--manifest','/config/old/run.json']
        (shared/'workload.json').write_text(json.dumps({'items':[{'kind':'Deployment',
          'metadata':{'name':'library-orchestrator-unified'},'spec':{'template':{'spec':{
          'containers':[{'name':'orchestrator','command':self.command,'volumeMounts':[]}],'volumes':[]}}}}]}))
        (self.root/'frozen-research-images.json').write_text('{"workloads":[]}')
        self.bundle.mkdir();self.image='ghcr.io/strat-relay/trading-platform-runtime@sha256:'+'a'*64
        self.config={'run_id':'sr-test-run','activation_time':2000000000,'execution_eligible':False,'transport':'JETSTREAM',
          'keys':['sr_retest_'+k+'@V2' for k in ('structural','volatility','trend','fvg')],
          'symbols':{'BTCUSD':'BTCUSDm','XAUUSD':'XAUUSDm','USDJPY':'USDJPYm'}}
        self.write('run.json',self.config);self.write('readiness.json',{'image':self.image,'binding':'test-binding'})
        self.write('workload.json',{'items':[{'kind':'ConfigMap','metadata':{'name':'sr-test-run-manifest'},
          'data':{'run.json':json.dumps(self.config)}},{'kind':'Deployment','metadata':{'name':'sr-hypotheses-v2-observer'},
          'spec':{'template':{'spec':{'containers':[{'image':self.image}]}}}}]})
        self.patch=[{'op':'replace','path':'/spec/template/spec/containers/0/image','value':self.image},
          {'op':'add','path':'/spec/template/spec/containers/0/command/-','value':'--manifest'},
          {'op':'add','path':'/spec/template/spec/containers/0/command/-','value':'/config/sr-hypotheses/run.json'},
          {'op':'add','path':'/spec/template/spec/containers/0/volumeMounts/-','value':{'name':'sr-hypotheses-manifest','mountPath':'/config/sr-hypotheses','readOnly':True}},
          {'op':'add','path':'/spec/template/spec/volumes/-','value':{'name':'sr-hypotheses-manifest','configMap':{'name':'sr-test-run-manifest'}}}]
        self.write('shared_orchestrator_patch.json',self.patch)
        for name,text in {'registration.sql':'SELECT 1;','activation.sql':'BEGIN; SELECT 1; COMMIT;','quiesce.sql':'SELECT 1;'}.items():
            (self.bundle/name).write_text(text)

    def write(self,name,value):
        (self.bundle/name).write_text(json.dumps(value))

    def test_stages_one_observer_and_preserves_shared_manifest_list(self):
        dest=stage(self.bundle,self.root,now=1999999000)
        patch=json.loads((dest/'shared-orchestrator.patch.json').read_text())
        command=self.command[:]
        for op in patch:
            if op['path'].endswith('/command/-'):command.append(op['value'])
        self.assertEqual(command[:len(self.command)],self.command)
        self.assertEqual(command[-2:],['--manifest','/config/sr-hypotheses/run.json'])
        self.assertIn('name: library-orchestrator-unified',self.top.read_text())
        lock=json.loads((self.root/'frozen-research-images.json').read_text())
        self.assertEqual(lock['workloads'][0]['binding'],'test-binding')
        self.assertEqual(lock['workloads'][0]['images'],[self.image])

    def test_wrong_scope_is_rejected_before_staging(self):
        self.config['keys'].pop();self.write('run.json',self.config)
        with self.assertRaisesRegex(ValueError,'four explicit'):stage(self.bundle,self.root,now=1999999000)
        self.assertFalse((self.root/'apps/trading-platform/sr-test-run').exists())

    def test_replacing_shared_command_is_rejected(self):
        self.patch[1]['path']='/spec/template/spec/containers/0/command'
        self.write('shared_orchestrator_patch.json',self.patch)
        with self.assertRaisesRegex(ValueError,'preserve'):stage(self.bundle,self.root,now=1999999000)

    def test_elapsed_activation_never_stages(self):
        with self.assertRaisesRegex(ValueError,'T0'):stage(self.bundle,self.root,now=2000000000)


if __name__=='__main__':unittest.main()
