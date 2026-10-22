"""Local manifest and cutover safety checks; no cluster/network/DB calls."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock
import yaml

ROOT=Path(__file__).resolve().parents[1]
APP=ROOT/'apps/trading-platform'


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.docs=list(yaml.safe_load_all((APP/'library-jetstream.yaml').read_text()))
        self.cm=self.docs[0]
        self.cfg=json.loads(self.cm['data']['run.json'])

    def test_run_is_fresh_next_open_research(self):
        self.assertNotEqual(self.cfg['run_id'],'library-canary-20261016-v1')
        self.assertEqual(self.cfg['transport'],'JETSTREAM')
        self.assertEqual(self.cfg['entry_mode'],'CONTEMPORANEOUS_CANDLE_OPEN')
        self.assertEqual(self.cfg['entry_quote_max_age_seconds'],5)
        self.assertIs(self.cfg['execution_eligible'],False)
        self.assertEqual(set(self.cfg['keys']),{'donchian_breakout','ema_pullback','fvg_retracement'})

    def test_jobs_and_workloads_pinned_and_isolated(self):
        image=json.loads(self.cm['data']['release.json'])['image']
        for obj in self.docs[1:]:
            pod=obj['spec']['template']['spec']
            self.assertFalse(pod['automountServiceAccountToken'] if obj['kind']=='Deployment' else False)
            self.assertNotIn('hostNetwork',pod)
            for c in pod['containers']+pod.get('initContainers',[]):
                self.assertEqual(c['image'],image)
                env={v['name']:v.get('value') for v in c['env']}
                self.assertEqual(env['EXECUTION_AUTHORITY_MODE'],'DISABLED')
                self.assertEqual(env['PYTHONPATH'],'/app')
                self.assertTrue(c['securityContext']['readOnlyRootFilesystem'])
                self.assertFalse(c['securityContext']['allowPrivilegeEscalation'])
                self.assertNotIn('trading-runtime-endpoints',json.dumps(c))
            if obj['kind']=='Deployment':
                self.assertEqual(pod['initContainers'][0]['command'],['python','/config/gate.py'])
                self.assertIn('readinessProbe',pod['containers'][0])

    def test_scripts_are_exact_and_compile(self):
        for name in ['activate.py','gate.py','health.py']:
            data=(ROOT/'docs/library-jetstream'/name).read_text()
            self.assertEqual(self.cm['data'][name],data)
            compile(data,name,'exec')

    def test_old_run_untouched_and_only_observer_stopped(self):
        old=list(yaml.safe_load_all((APP/'strategy-library-canary.yaml').read_text()))
        self.assertEqual(json.loads(old[0]['data']['run.json'])['run_id'],'library-canary-20261016-v1')
        self.assertEqual(old[1]['spec']['replicas'],0)
        collector=list(yaml.safe_load_all((APP/'market-data-collector.yaml').read_text()))[0]
        self.assertEqual(collector['spec']['replicas'],1)
        env={e['name']:e for e in collector['spec']['template']['spec']['containers'][0]['env']}
        self.assertEqual(env['MARKET_DATA_EVENT_MODE']['value'],'JETSTREAM_OUTBOX')
        self.assertEqual(env['LIBRARY_EVENT_DSN']['valueFrom']['secretKeyRef']['name'],'library-event-collector')

    def test_no_credentials_in_resources(self):
        for f in ['library-jetstream.yaml','library-jetstream-network.yaml','library-jetstream-rbac.yaml']:
            for obj in yaml.safe_load_all((APP/f).read_text()):
                self.assertNotEqual(obj['kind'],'Secret')
        text=(APP/'library-jetstream.yaml').read_text()
        self.assertNotIn("password='",text)
        self.assertNotIn('password="',text)
        self.assertNotIn('postgresql://',text)

    def test_network_does_not_admit_execution_or_broker(self):
        text=(APP/'library-jetstream-network.yaml').read_text()
        self.assertNotIn('0.0.0.0/0',text)
        self.assertNotIn('22347',text)
        self.assertNotIn('22348',text)
        self.assertNotIn('trading-redis',text)

    def test_cutover_sql_is_additive_and_bounded(self):
        sql=self.cm['data']['activation.sql']
        self.assertTrue(sql.startswith('BEGIN;'))
        self.assertTrue(sql.rstrip().endswith('COMMIT;'))
        self.assertIn('Run already exists',sql)
        self.assertIn('T0 elapsed',sql)
        for command in ['DROP TABLE','TRUNCATE','DELETE FROM','execution_mode=\'LIVE\'']:
            self.assertNotIn(command,sql)

    def test_readonly_report_redacts_secret_values(self):
        module=load('library_preflight',ROOT/'scripts/library_jetstream_preflight.py')
        marker='SECRET_VALUE_MUST_NOT_ESCAPE'
        def fake(command,**kwargs):
            if 'secret' in command:
                data={k:marker for keys in module.SECRETS.values() for k in keys}
                result={'data':data}
            elif 'deployments' in command or 'jobs' in command:result={'items':[]}
            else:result={'spec':{'clusterIP':'10.43.0.1','ports':[]}}
            self.assertEqual(command[command.index('get')],'get')
            self.assertNotIn('apply',command)
            return SimpleNamespace(returncode=0,stdout=json.dumps(result))
        with patch.object(module.subprocess,'run',side_effect=fake):
            result=module.report('/fake/config')
        self.assertNotIn(marker,json.dumps(result))
        self.assertTrue(result['credential_contract_present'])
        self.assertNotEqual(result['status'],'ACTIVATED')


class ActivationTests(unittest.TestCase):
    def setUp(self):
        try:self.module=load('library_activate',ROOT/'docs/library-jetstream/activate.py')
        except ModuleNotFoundError:self.skipTest('released platform-tools checkout required on PYTHONPATH')
        self.cfg=SimpleNamespace(activation_time=9999999999,run_id='test',keys=['fvg_retracement'],
                                 symbols={'BTCUSD':'BTCUSDm','XAUUSD':'XAUUSDm','USDJPY':'USDJPYm'})
        self.config=patch.object(self.module.RunConfig,'load',return_value=self.cfg)
        self.config.start();self.addCleanup(self.config.stop)

    def test_old_observer_alive_blocks_before_database_access(self):
        with patch.object(self.module,'kube',return_value={'spec':{'replicas':1},'status':{'replicas':1}}), \
             patch.object(self.module,'open_connection') as connect:
            with self.assertRaisesRegex(ValueError,'not stopped'):self.module.activate()
        connect.assert_not_called()

    def test_terminating_old_pod_blocks_before_database_access(self):
        with patch.object(self.module,'kube',side_effect=[{'spec':{'replicas':0},'status':{}},
                {'items':[{'metadata':{'deletionTimestamp':'now'},'status':{'phase':'Running'}}]}]), \
             patch.object(self.module,'open_connection') as connect:
            with self.assertRaisesRegex(ValueError,'old polling pod'):self.module.activate()
        connect.assert_not_called()

    def test_elapsed_t0_blocks_before_any_cluster_or_database_access(self):
        self.cfg.activation_time=0
        with patch.object(self.module,'kube') as kube,patch.object(self.module,'open_connection') as conn:
            with self.assertRaisesRegex(ValueError,'T0'):self.module.activate()
        kube.assert_not_called();conn.assert_not_called()


if __name__=='__main__':unittest.main()
