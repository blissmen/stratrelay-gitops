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
    def runner(self, role_exists=False, existing_secret=None, probe=True, primary=True, migration=True, fail_secret=False):
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
                    {'metadata': {'name': 'relay-0', 'labels': {'app.kubernetes.io/name': 'signal-outbox-relay'}},
                     'status': {'containerStatuses': [{'name': 'relay', 'ready': True}]}}]}
            elif 'secret' in args:
                if existing_secret is None:
                    return SimpleNamespace(returncode=1, stdout='', stderr='Error from server (NotFound)')
                payload = existing_secret
            elif args[-1] == audit.ADMIN_COMMAND:
                if input == audit.INSPECT_SQL:
                    payload = {'database': audit.DATABASE, 'primary': primary, 'outbox_exists': True,
                               'role_exists': role_exists, 'migrations': [
                                   {'version': v, 'filename': f, 'checksum_sha256': h}
                                   for v, (f, h) in MIGRATIONS.items()] if migration else []}
                else:
                    self.assertIn('CREATE ROLE strategy_event_relay', input)
                    return SimpleNamespace(returncode=0, stdout='BEGIN\nCREATE ROLE\nCOMMIT\n', stderr='')
            elif args[-1] == audit.LOGIN_PROBE:
                payload = {'verified': probe}
            elif 'create' in args:
                if fail_secret:
                    return SimpleNamespace(returncode=1, stdout='', stderr='secret-value-never-show')
                obj = json.loads(input)
                self.assertEqual(obj['metadata']['name'], audit.SECRET)
                self.assertEqual(set(obj['data']), {'STRATEGY_EVENT_DSN'})
                return SimpleNamespace(returncode=0, stdout='secret/'+audit.SECRET, stderr='')
            else:
                raise AssertionError('unexpected command')
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr='')
        return run

    def execute(self, apply=False, **kwargs):
        with patch('subprocess.run', side_effect=self.runner(**kwargs)), patch('secrets.token_urlsafe', return_value='test-generated-password'):
            result = audit.bootstrap('/test', 'rancher', apply)
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

    def test_activation_bundle_stays_outside_flux_and_only_starts_relay(self):
        root = Path(__file__).resolve().parents[1]
        active = yaml.safe_load((root/'apps/trading-platform/kustomization.yaml').read_text())
        self.assertFalse(any('strategy-runtime-event' in r for r in active['resources']))
        prepared = root/'staged/strategy-runtime-event-relay-activation'
        obj = yaml.safe_load((prepared/'runtime.yaml').read_text())
        self.assertEqual(obj['spec']['replicas'], 1)
        containers = obj['spec']['template']['spec']['containers']
        self.assertEqual(len(containers), 1)
        self.assertEqual(containers[0]['command'][-1], 'relay')
        self.assertTrue(any(e.get('name') == 'EXECUTION_AUTHORITY_MODE' and e.get('value') == 'DISABLED'
                            for e in containers[0]['env']))


if __name__ == '__main__':
    unittest.main()
