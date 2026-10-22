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
                self.assertEqual(env['LIBRARY_EVENT_DATABASE'],'trading_platform')
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

    def test_original_run_recovers_only_without_committed_event_cutover(self):
        old=list(yaml.safe_load_all((APP/'strategy-library-canary.yaml').read_text()))
        self.assertEqual(json.loads(old[0]['data']['run.json'])['run_id'],'library-canary-20261016-v1')
        self.assertEqual(old[1]['spec']['replicas'],1)
        gate=old[1]['spec']['template']['spec']['initContainers'][0]
        code=gate['command'][-1]
        self.assertIn('BLOCKED_EVENT_RUN_EXISTS',code)
        self.assertIn('library-js-20261022-0600-v1',code)
        self.assertIn('activation_attestation',code)
        self.assertIn("raise SystemExit(1 if blocked else 0)",code)
        compile(code,'recovery-gate','exec')
        collector=list(yaml.safe_load_all((APP/'market-data-collector.yaml').read_text()))[0]
        self.assertEqual(collector['spec']['replicas'],1)
        env={e['name']:e for e in collector['spec']['template']['spec']['containers'][0]['env']}
        self.assertEqual(env['MARKET_DATA_EVENT_MODE']['value'],'OFF')
        self.assertNotIn('LIBRARY_EVENT_DSN',env)
        self.assertNotIn('library-event-collector',json.dumps(collector))
        for doc in self.docs[1:]:
            if doc['kind']=='Deployment':self.assertEqual(doc['spec']['replicas'],0)
            elif doc['kind']=='Job':self.assertTrue(doc['spec']['suspend'])

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

    def test_cutover_api_policy_includes_verified_backend(self):
        docs=list(yaml.safe_load_all((APP/'library-jetstream-network.yaml').read_text()))
        policy=next(d for d in docs if d['metadata']['name']=='library-event-activate-apiserver')
        routes={(rule['to'][0]['ipBlock']['cidr'],rule['ports'][0]['port'])
                for rule in policy['spec']['egress']}
        self.assertEqual(routes,{('10.43.0.1/32',443),('10.10.10.10/32',6443)})

    def test_infrastructure_inventory_is_readonly_and_redacted(self):
        module=load('library_infrastructure',ROOT/'scripts/library_jetstream_infrastructure.py')
        def pod(name,image):
            return {'metadata':{'name':name+'-0','labels':{'app.kubernetes.io/name':name}},
                'status':{'phase':'Running'},'spec':{'containers':[{'name':name,'image':image,
                    'env':[{'name':'PASSWORD','value':'MUST_NOT_ESCAPE'}]}]}}
        def fake(cmd,**kwargs):
            if 'pods' in cmd:
                data=json.dumps({'items':[pod('trading-postgres','postgres:17'),pod('trading-nats','nats:2.10')]})
            elif 'psql' in ' '.join(cmd):
                self.assertIn('default_transaction_read_only=on',' '.join(cmd))
                self.assertIn('${POSTGRES_DB:?',' '.join(cmd))
                self.assertNotIn('-d trading',' '.join(cmd))
                self.assertTrue(cmd[-1].startswith('SELECT'))
                data='{"database":"trading_platform","primary":true}'
            else:data='nats-server: v2.10.25 MUST_NOT_ESCAPE'
            return SimpleNamespace(returncode=0,stdout=data,stderr='')
        with patch.object(module.subprocess,'run',side_effect=fake):result=module.inventory('/fake','rancher')
        self.assertEqual(result['status'],'INVENTORIED_NOT_ACTIVATED')
        self.assertEqual(result['nats_pods'][0]['server_version'],'2.10.25')
        self.assertNotIn('MUST_NOT_ESCAPE',json.dumps(result))

    def test_target_parser_only_prints_nonsecret_connection_fields(self):
        module=load('library_target_parser',ROOT/'scripts/library_jetstream_infrastructure.py')
        self.assertIn("['dbname','host','port']",module.TARGET_CODE)
        self.assertNotIn('password',module.TARGET_CODE.lower())
        self.assertNotIn('connect(',module.TARGET_CODE)

    def test_postgres_failure_is_classified_without_printing_private_details(self):
        module=load('library_infrastructure_errors',ROOT/'scripts/library_jetstream_infrastructure.py')
        for error,reason in [('fe_sendauth: no password supplied','POSTGRES_PASSWORD_REQUIRED'),
            ('password authentication failed for user "private"','POSTGRES_AUTHENTICATION_FAILED'),
            ('database "private" does not exist','POSTGRES_DATABASE_ERROR'),
            ('permission denied for table private','POSTGRES_PERMISSION_DENIED')]:
            with self.subTest(reason=reason):self.assertEqual(module.diagnostic(error),reason)

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
            if 'config' in command:return SimpleNamespace(returncode=0,stdout='local\n')
            if '--raw=/version' in command:return SimpleNamespace(returncode=0,stdout='{"gitVersion":"test"}')
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

    def test_failed_reads_do_not_assert_missing_secret_keys(self):
        module=load('library_preflight_failed',ROOT/'scripts/library_jetstream_preflight.py')
        error=SimpleNamespace(returncode=1,stdout='',stderr='Error: context was not found: PRIVATE_HELPER_OUTPUT')
        with patch.object(module.subprocess,'run',return_value=error):
            result=module.report('/fake/config')
        self.assertFalse(result['cluster_read_succeeded'])
        self.assertTrue(all(s['missing_keys'] is None for s in result['secrets'].values()))
        self.assertTrue(all(e['reason']=='CONTEXT_NOT_FOUND' for e in result['errors']))
        self.assertNotIn('PRIVATE_HELPER_OUTPUT',json.dumps(result))

    def test_errors_are_classified_without_disclosing_raw_errors(self):
        module=load('library_preflight_errors',ROOT/'scripts/library_jetstream_preflight.py')
        for message,code in [('Unauthorized','AUTHENTICATION_FAILED'),('Forbidden','RBAC_FORBIDDEN'),
            ('connection refused','CONNECTION_REFUSED'),('x509: bad certificate','TLS_VERIFICATION_FAILED'),
            ('Error from server (NotFound): secrets hidden not found','RESOURCE_NOT_FOUND')]:
            with self.subTest(code=code):self.assertEqual(module.failure_reason(message),code)


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
