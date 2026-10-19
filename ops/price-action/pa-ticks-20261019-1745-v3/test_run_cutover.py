from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('runner', ROOT / 'run_cutover.py')
runner = importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
RUN = json.loads((ROOT / 'run.json').read_text())
WORKLOAD = json.loads((ROOT / 'workload.json').read_text())


class Cursor:
    def __init__(self, row):self.row=row
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def execute(self,*args):pass
    def fetchone(self):return self.row


class Conn:
    def __init__(self,row):self.row=row
    def cursor(self):return Cursor(self.row)
    def rollback(self):pass


class RunnerTests(unittest.TestCase):
    def test_both_fresh_full_checkpoints_required(self):
        state={'status':'RUNNING','runtime':{'successful_instrument_count':3},
               'instrument_tick_sizes':{'XAUUSD':.001,'BTCUSD':.01,'USDJPY':.001}}
        stamp=datetime.fromtimestamp(1000,timezone.utc)
        self.assertTrue(runner.ready(Conn((state,stamp)),RUN,now=1001))
        self.assertFalse(runner.ready(Conn(None),RUN,now=1001))
        self.assertFalse(runner.ready(Conn((state,stamp)),RUN,now=1201))
        self.assertFalse(runner.ready(Conn((state,stamp)),RUN,now=999))
        for invalid in (True,0,float('nan'),float('inf')):
            state['instrument_tick_sizes']['XAUUSD']=invalid
            self.assertFalse(runner.ready(Conn((state,stamp)),RUN,now=1001))

    def test_primary_verification_and_old_evidence_guard(self):
        self.assertEqual(runner.target(Conn(('db','10.0.0.1',5432,False))),('db','10.0.0.1',5432))
        with self.assertRaises(ValueError):runner.target(Conn(('db','10.0.0.2',5432,True)))
        runner.reject_existing_evidence(Conn((0,)))
        with self.assertRaises(ValueError):runner.reject_existing_evidence(Conn((1,)))

    def test_jobs_are_bounded_and_use_migration_credential_only(self):
        jobs=[x for x in WORKLOAD['items'] if x['kind']=='Job' and x['metadata']['name'].endswith('-r2')]
        self.assertEqual(len(jobs),2)
        for j in jobs:
            self.assertEqual(j['spec']['backoffLimit'],0)
            self.assertLessEqual(j['spec']['activeDeadlineSeconds'],1800)
            pod=j['spec']['template']['spec'];c=pod['containers'][0]
            self.assertFalse(pod['automountServiceAccountToken'])
            self.assertEqual(c['envFrom'],[{'secretRef':{'name':'trading-runtime-endpoints'}}])
            self.assertEqual(c['image'],RUN['image'])
            self.assertIn({'name':'PYTHONPATH','value':'/app'},c['env'])
            self.assertNotIn('ttlSecondsAfterFinished',j['spec'])
        data=next(x['data'] for x in WORKLOAD['items'] if x['kind']=='ConfigMap' and 'run_cutover.py' in x['data'] and x['metadata']['name'].endswith('-r2'))
        for f in ('run_cutover.py','preflight.py','register.sql','activate.sql','run.json'):
            self.assertEqual(data[f],(ROOT/f).read_text())

    def test_observer_and_init_mounted_scripts_have_app_import_path(self):
        for d in WORKLOAD['items']:
            if d['kind'] != 'Deployment':
                continue
            pod = d['spec']['template']['spec']
            for c in pod['containers'] + pod['initContainers']:
                self.assertIn({'name':'PYTHONPATH','value':'/app'}, c['env'])
            names = {v.get('configMap',{}).get('name') for v in pod['volumes']}
            self.assertIn('pa-ticks-20261019-1745-v3-manifests-r2',names)

    def test_module_diagnostics_expose_names_without_raw_connection_errors(self):
        for f in ('run_cutover.py','preflight.py'):
            source = (ROOT/f).read_text()
            self.assertIn('str(exc.name)',source)
            self.assertIn('isinstance(exc, ModuleNotFoundError)',source)


if __name__=='__main__':unittest.main(verbosity=2)
