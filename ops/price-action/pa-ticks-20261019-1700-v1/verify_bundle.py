"""Offline structural and startup-guard checks; no cluster or database access."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('cutover_preflight', ROOT / 'preflight.py')
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)
RUN = json.loads((ROOT / 'run.json').read_text())
WORKLOAD = json.loads((ROOT / 'workload.json').read_text())


class Cursor:
    def __init__(self, manifest, key):
        e = manifest['strategies'][key]
        self.role = (False,) * 5
        self.outside_writes = 0
        self.privileges = True
        self.record = [
            {'sr_retest': 'price_action_sr_retest_v1', 'engulfing_continuation': 'price_action_engulfing_continuation_v1'}[key],
            'V1', 'FROZEN', 'frozen-parameter', e['parameter_hash'], e['parameters'], True,
            {'parameter_set_id': 'frozen-parameter', 'market_input_policy': preflight.POLICY,
             'corrected_run_id': RUN['run_id'], 'publication_activation_time': RUN['activation_time'],
             'manifest_hash': manifest['manifest_hash'], 'forward_image': RUN['image'], 'broker_writes': False},
            'OFF', False, manifest['universe_requested']]
        self.state = None
        self.sql = ''
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, sql, args=()): self.sql = sql
    def fetchone(self):
        if 'rolsuper' in self.sql: return self.role
        if 'pg_class' in self.sql: return (self.outside_writes,)
        if 'bool_and' in self.sql: return (self.privileges,)
        if 'strategy_instance_v2' in self.sql: return self.record
        if 'SELECT state' in self.sql: return self.state
        raise AssertionError('unexpected query')


class Conn:
    def __init__(self, cursor): self.cur = cursor; self.rolled_back = False
    def cursor(self): return self.cur
    def rollback(self): self.rolled_back = True


class CutoverChecks(unittest.TestCase):
    def test_active_resource_and_review_bundle_identical(self):
        active = ROOT.parents[2] / 'apps/trading-platform/price-action-corrected-20261019.json'
        self.assertEqual(json.loads(active.read_text()), WORKLOAD)
        self.assertEqual(WORKLOAD['items'][0]['data']['preflight.py'], (ROOT / 'preflight.py').read_text())

    def test_both_workloads_are_isolated_and_guarded(self):
        deployments = [x for x in WORKLOAD['items'] if x['kind'] == 'Deployment']
        self.assertEqual(len(deployments), 2)
        ids = set()
        for d in deployments:
            pod = d['spec']['template']['spec']; c = pod['containers'][0]
            values = {e['name']: e.get('value') for e in c['env']}
            self.assertEqual(c['image'], RUN['image'])
            self.assertEqual(pod['initContainers'][0]['image'], RUN['image'])
            self.assertEqual(d['spec']['strategy']['type'], 'Recreate')
            self.assertEqual(d['spec']['replicas'], 1)
            self.assertEqual(values['EXECUTION_AUTHORITY_MODE'], 'DISABLED')
            self.assertEqual(values['PRICE_ACTION_ENTRY_MODE'], 'CONTEMPORANEOUS_CANDLE_OPEN')
            self.assertEqual(values['PRICE_ACTION_POLL_SECONDS'], '10')
            self.assertNotIn('PRICE_ACTION_MCP_URL', values)
            self.assertEqual(c['envFrom'], [{'secretRef': {'name': 'price-action-corrected-observer-db'}}])
            self.assertFalse(pod['automountServiceAccountToken'])
            self.assertIn(values['PRICE_ACTION_INSTANCE_ID'], [i['instance_id'] for i in RUN['instances'].values()])
            ids.add(values['PRICE_ACTION_INSTANCE_ID'])
        self.assertEqual(len(ids), 2)

    def test_database_and_cache_ingress_allow_only_both_replacement_names(self):
        names = sorted(i['workload'] for i in RUN['instances'].values())
        policies = [x for x in WORKLOAD['items'] if x['kind'] == 'NetworkPolicy' and x['spec']['policyTypes'] == ['Ingress']]
        self.assertEqual(len(policies), 2)
        self.assertEqual({p['spec']['podSelector']['matchLabels']['app.kubernetes.io/name'] for p in policies},
                         {'trading-postgres', 'trading-redis'})
        for p in policies:
            rule = p['spec']['ingress'][0]
            self.assertEqual(sorted(rule['from'][0]['podSelector']['matchExpressions'][0]['values']), names)
            self.assertEqual(len(rule['ports']), 1)

    def test_startup_guards_for_both_registered_instances(self):
        for key, info in RUN['instances'].items():
            manifest = json.loads(WORKLOAD['items'][0]['data'][key + '.json'])
            with self.subTest(key=key), patch.dict(os.environ, {'PA_EXPECTED_IMAGE': RUN['image']}):
                cur = Cursor(manifest, key); conn = Conn(cur)
                preflight.check(conn, manifest, key, info['instance_id'], now=RUN['activation_time'] - 60)
                self.assertTrue(conn.rolled_back)
                for mutate in (
                    lambda c: setattr(c, 'role', (True, False, False, False, False)),
                    lambda c: setattr(c, 'outside_writes', 1),
                    lambda c: setattr(c, 'privileges', False),
                    lambda c: setattr(c, 'record', None),
                    lambda c: c.record.__setitem__(8, 'LIVE'),
                    lambda c: c.record.__setitem__(4, 'wrong-fingerprint'),
                    lambda c: c.record[7].update(instance_id='legacy-alias'),
                    lambda c: c.record[7].update(manifest_hash='wrong-manifest'),
                ):
                    cur = Cursor(manifest, key); mutate(cur)
                    with self.assertRaises(ValueError):
                        preflight.check(Conn(cur), manifest, key, info['instance_id'], now=RUN['activation_time'] - 60)
                with self.assertRaisesRegex(ValueError, 'T0 passed'):
                    preflight.check(Conn(Cursor(manifest, key)), manifest, key, info['instance_id'], now=RUN['activation_time'] + 60)

    def test_no_ledger_mutations_or_execution_enablement_in_sql(self):
        for name in ('register.sql', 'activate.sql', 'quiesce.sql'):
            sql = (ROOT / name).read_text()
            for forbidden in ('DELETE ', 'TRUNCATE ', 'UPDATE research.', 'UPDATE strategy.entry', 'execution_eligible=true'):
                self.assertNotIn(forbidden, sql)
        self.assertNotIn('online=true', (ROOT / 'register.sql').read_text())
        self.assertIn('market_input_binding', (ROOT / 'activate.sql').read_text())
        self.assertIn('clock_timestamp()', (ROOT / 'activate.sql').read_text())


if __name__ == '__main__':
    unittest.main(verbosity=2)
