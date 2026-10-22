import base64
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml
import strategy_event_relay_bootstrap as audit
from strategy_event_preflight import MIGRATIONS


class RelayBootstrapTests(unittest.TestCase):
    def runner(self, role_exists=False, existing_secret=None, probe=True, primary=True, migration=True, fail_secret=False, component='relay'):
        profile = audit.PROFILES[component]
        self.commands = []
        self.password = 'test-generated-password'
        def run(args, input=None, **kwargs):
            self.commands.append((args, input))
            if 'namespace' in args:
                payload = {'metadata': {'uid': audit.NAMESPACE_UID}}
            elif 'pods' in args:
                payload = {'items': [
                    {'metadata': {'name': 'postgres-0', 'labels': {'app.kubernetes.io/name': 'trading-postgres'}},
                     'spec': {'containers': [{'name': 'postgres'}]}, 'status': {'phase': 'Running'}},
                    {'metadata': {'name': 'probe-0', 'labels': {'app.kubernetes.io/name': profile['label']}},
                     'status': {'containerStatuses': [{'name': profile['container'], 'ready': True}]}}]}
            elif 'secret' in args:
                if existing_secret is None:
                    return SimpleNamespace(returncode=1, stdout='', stderr='Error from server (NotFound)')
                payload = existing_secret
            elif args[-1] == audit.ADMIN_COMMAND:
                if input == audit.INSPECT_SQL.replace(audit.ROLE, profile['role']):
                    payload = {'database': audit.DATABASE, 'primary': primary, 'outbox_exists': True,
                               'role_exists': role_exists, 'migrations': [
                                   {'version': v, 'filename': f, 'checksum_sha256': h}
                                   for v, (f, h) in MIGRATIONS.items()] if migration else []}
                else:
                    self.assertIn('CREATE ROLE '+profile['role'], input)
                    return SimpleNamespace(returncode=0, stdout='BEGIN\nCREATE ROLE\nCOMMIT\n', stderr='')
            elif args[-1] == audit.LOGIN_PROBE:
                payload = {'verified': probe}
            elif 'create' in args:
                if fail_secret:
                    return SimpleNamespace(returncode=1, stdout='', stderr='secret-value-never-show')
                obj = json.loads(input)
                self.assertEqual(obj['metadata']['name'], profile['secret'])
                self.assertEqual(set(obj['data']), {'STRATEGY_EVENT_DSN'})
                return SimpleNamespace(returncode=0, stdout='secret/'+audit.SECRET, stderr='')
            else:
                raise AssertionError('unexpected command')
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr='')
        return run

    def execute(self, apply=False, component='relay', **kwargs):
        with patch('subprocess.run', side_effect=self.runner(component=component, **kwargs)), patch('secrets.token_urlsafe', return_value='test-generated-password'):
            result = audit.bootstrap('/test', 'rancher', apply, component)
        self.assertNotIn('test-generated-password', json.dumps(result))
        self.assertNotIn('secret-value-never-show', json.dumps(result))
        self.assertFalse(result['workloads_changed'])
        return result

    def test_plan_makes_no_mutations(self):
        result = self.execute()
        self.assertEqual(result['status'], 'PLAN_ONLY_RELAY_DB_CREATION_REQUIRED')
        self.assertEqual(sum('exec' in args for args, _ in self.commands), 1)
        self.assertFalse(any('create' in args for args, _ in self.commands))

    def test_apply_persists_credential_then_creates_role_and_audits(self):
        result = self.execute(apply=True)
        self.assertEqual(result['status'], 'RELAY_DB_READY')
        self.assertTrue(result['role_created'])
        self.assertTrue(result['secret_created'])
        probe = next(i for i, (args, _) in enumerate(self.commands) if args[-1] == audit.LOGIN_PROBE)
        create = next(i for i, (args, _) in enumerate(self.commands) if 'create' in args)
        self.assertLess(create, probe)
        for args, _ in self.commands:
            self.assertNotIn('test-generated-password', ' '.join(args))

    def test_primary_or_migration_failure_prevents_writes(self):
        for kwargs in ({'primary': False}, {'migration': False}):
            result = self.execute(apply=True, **kwargs)
            self.assertEqual(result['status'], 'BLOCKED')
            self.assertFalse(result['role_created'])
            self.assertFalse(any('create' in args for args, _ in self.commands))

    def test_unpaired_role_does_not_rotate_or_adopt(self):
        result = self.execute(apply=True, role_exists=True)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertFalse(result['role_created'])
        self.assertEqual(sum('exec' in args for args, _ in self.commands), 1)

    def test_existing_binding_is_verified_without_password_rotation(self):
        dsn = 'postgresql://'+audit.ROLE+':existing-password@'+audit.HOST+':5432/'+audit.DATABASE+'?connect_timeout=5'
        secret = {'data': {'STRATEGY_EVENT_DSN': base64.b64encode(dsn.encode()).decode()}}
        result = self.execute(apply=True, role_exists=True, existing_secret=secret)
        self.assertEqual(result['status'], 'RELAY_DB_READY')
        self.assertFalse(result['role_created'])
        self.assertFalse(result['secret_created'])
        self.assertNotIn('existing-password', json.dumps(result))

    def test_owned_secret_without_role_can_resume_without_rotation(self):
        dsn = 'postgresql://'+audit.ROLE+':retained-password@'+audit.HOST+':5432/'+audit.DATABASE+'?connect_timeout=5'
        secret = {'metadata': {'annotations': {'stratrelay.app/relay-bootstrap': 'v1'}},
                  'data': {'STRATEGY_EVENT_DSN': base64.b64encode(dsn.encode()).decode()}}
        result = self.execute(apply=True, existing_secret=secret)
        self.assertEqual(result['status'], 'RELAY_DB_READY')
        self.assertTrue(result['role_created'])
        self.assertFalse(result['secret_created'])
        sql = next(payload for args, payload in self.commands if args[-1] == audit.ADMIN_COMMAND and payload != audit.INSPECT_SQL)
        self.assertIn('retained-password', sql)
        self.assertNotIn('retained-password', json.dumps(result))
        self.assertFalse(any('create' in args for args, _ in self.commands))

    def test_partial_failure_is_explicit_and_never_ready(self):
        for kwargs in ({'probe': False}, {'fail_secret': True}):
            result = self.execute(apply=True, **kwargs)
            self.assertEqual(result['status'], 'BLOCKED')
            self.assertEqual(result['role_created'], not kwargs.get('fail_secret', False))
            self.assertEqual(result['secret_created'], not kwargs.get('fail_secret', False))
            if not kwargs.get('fail_secret'):
                self.assertIn('recovery_required', result)

    def test_grants_only_outbox_delivery_and_no_injection(self):
        sql = audit.role_sql('safe-password')
        self.assertIn('GRANT UPDATE (published_at)', sql)
        self.assertNotIn('GRANT ALL', sql)
        self.assertNotIn('GRANT INSERT', sql)
        self.assertNotIn('ALTER ROLE', sql)
        with self.assertRaises(ValueError):
            audit.role_sql("password'; DROP TABLE strategy.entry_signals;--")

    def test_collector_profile_only_inserts_hints_and_checks_from_collector(self):
        result = self.execute(apply=True, component='collector')
        self.assertEqual(result['status'], 'COLLECTOR_DB_READY')
        sql = audit.role_sql('safe-password', 'collector')
        self.assertIn('INSERT (event_id,runtime_id,payload), SELECT (event_id)', sql)
        self.assertNotIn('GRANT SELECT, DELETE', sql)
        self.assertNotIn('GRANT UPDATE', sql)
        self.assertNotIn('GRANT ALL', sql)
        probe_args, payload = next((args, payload) for args, payload in self.commands if args[-1] == audit.LOGIN_PROBE)
        self.assertEqual(probe_args[probe_args.index('-c')+1], 'collector')
        self.assertEqual(json.loads(payload)['component'], 'collector')
        secret = next(json.loads(payload) for args, payload in self.commands if 'create' in args)
        self.assertEqual(secret['metadata']['name'], 'strategy-event-collector-db')
        self.assertIn('stratrelay.app/collector-bootstrap', secret['metadata']['annotations'])
        self.assertNotIn('P2_NATS_USER', json.dumps(secret))

    def test_active_bundle_only_starts_the_verified_relay(self):
        root = Path(__file__).resolve().parents[1]
        active = yaml.safe_load((root/'apps/trading-platform/kustomization.yaml').read_text())
        self.assertIn('strategy-runtime-event-relay', active['resources'])
        self.assertFalse(any('staged/strategy-runtime-events' in r for r in active['resources']))
        prepared = root/'apps/trading-platform/strategy-runtime-event-relay'
        obj = yaml.safe_load((prepared/'runtime.yaml').read_text())
        self.assertEqual(obj['spec']['replicas'], 1)
        containers = obj['spec']['template']['spec']['containers']
        init = obj['spec']['template']['spec']['initContainers']
        self.assertEqual(len(init), 1)
        self.assertEqual(init[0]['name'], 'verify-relay-prerequisites')
        self.assertIn('RELAY_PREREQUISITES_VERIFIED', init[0]['command'][-1])
        compile(init[0]['command'][-1], '<relay-prerequisites>', 'exec')
        self.assertNotIn('serve(\'provision\')', init[0]['command'][-1])
        self.assertEqual(init[0]['envFrom'], containers[0]['envFrom'])
        self.assertEqual(init[0]['env'], containers[0]['env'])
        self.assertEqual(len(containers), 1)
        self.assertEqual(containers[0]['command'][-1], 'relay')
        self.assertTrue(any(e.get('name') == 'EXECUTION_AUTHORITY_MODE' and e.get('value') == 'DISABLED'
                            for e in containers[0]['env']))

    def test_collector_patch_only_enables_the_verified_hint_writer(self):
        root = Path(__file__).resolve().parents[1]
        active = yaml.safe_load((root/'apps/trading-platform/kustomization.yaml').read_text())
        self.assertFalse(any('strategy-event-collector' in r for r in active['resources']))
        self.assertIn({'path': 'strategy-event-collector.patch.yaml'}, active['patches'])
        current = next(o for o in yaml.safe_load_all((root/'apps/trading-platform/market-data-collector.yaml').read_text()) if o['kind'] == 'Deployment')
        env = {e['name']: e for e in current['spec']['template']['spec']['containers'][0]['env']}
        self.assertNotIn('STRATEGY_EVENT_MODE', env)
        self.assertEqual(env['MARKET_DATA_EVENT_MODE']['value'], 'OFF')
        patch = yaml.safe_load((root/'apps/trading-platform/strategy-event-collector.patch.yaml').read_text())
        spec = patch['spec']['template']['spec']
        self.assertEqual(set(spec), {'containers'})
        container = spec['containers'][0]
        self.assertEqual(set(container), {'name', 'env'})
        self.assertEqual(container['name'], 'collector')
        env = {e['name']: e for e in container['env']}
        self.assertEqual(set(env), {'STRATEGY_EVENT_MODE','STRATEGY_EVENT_RUNTIME_ID','STRATEGY_EVENT_DATABASE','STRATEGY_EVENT_DSN'})
        self.assertEqual(env['STRATEGY_EVENT_MODE']['value'], 'JETSTREAM')
        self.assertEqual(env['STRATEGY_EVENT_DSN']['valueFrom']['secretKeyRef']['name'], 'strategy-event-collector-db')

    def test_active_resource_graph_enrolls_relay_once_and_no_stream_provision_job(self):
        root = Path(__file__).resolve().parents[1]
        def resources(directory):
            config = yaml.safe_load((directory/'kustomization.yaml').read_text())
            for name in config['resources']:
                path = directory/name
                if path.is_dir():
                    yield from resources(path)
                else:
                    yield from (d for d in yaml.safe_load_all(path.read_text()) if d)
        objects = list(resources(root/'apps/trading-platform'))
        relays = [o for o in objects if o['kind'] == 'Deployment' and o['metadata']['name'] == 'strategy-runtime-event-relay']
        self.assertEqual(len(relays), 1)
        self.assertFalse(any(o['kind'] == 'Job' and o['metadata']['name'].startswith('strategy-runtime-events-provision') for o in objects))
        policies = [o for o in objects if o['kind'] == 'NetworkPolicy' and o['metadata']['name'].startswith('strategy-runtime-events-')]
        self.assertEqual(len(policies), 3)
        ports = {p['port'] for o in policies for rule in o['spec'].get('egress', [])+o['spec'].get('ingress', []) for p in rule.get('ports', [])}
        self.assertEqual(ports, {53, 5432, 4222})


if __name__ == '__main__':
    unittest.main()
