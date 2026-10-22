import base64
import datetime
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml
import strategy_event_preflight as audit


class StrategyEventRolloutTests(unittest.TestCase):
    def test_staged_bundle_cannot_activate_a_pod_or_existing_workload(self):
        root = Path(__file__).resolve().parents[1]
        active = yaml.safe_load((root/'apps/trading-platform/kustomization.yaml').read_text())
        self.assertFalse(any('strategy-runtime-events' in name for name in active['resources']))
        docs = list(yaml.safe_load_all((root/'staged/strategy-runtime-events/runtime.yaml').read_text()))
        self.assertEqual([d['kind'] for d in docs], ['Deployment', 'Job'])
        self.assertEqual(docs[0]['spec']['replicas'], 0)
        self.assertIs(docs[1]['spec']['suspend'], True)
        for obj in docs:
            pod = obj['spec']['template']['spec']
            self.assertIs(pod['automountServiceAccountToken'], False)
            self.assertNotIn('serviceAccountName', pod)
            self.assertNotIn('volumes', pod)
            for c in pod['containers']:
                self.assertEqual(c['image'], audit.IMAGE)
                self.assertEqual(c['command'][:3], ['python', '-m', 'infrastructure.messaging.runtime_event_service'])
                self.assertIn(c['command'][-1], ('relay', 'provision'))

    def test_network_has_no_bridge_execution_or_broad_ip_permission(self):
        root = Path(__file__).resolve().parents[1]
        docs = list(yaml.safe_load_all((root/'staged/strategy-runtime-events/network.yaml').read_text()))
        ports = {p['port'] for d in docs for rule in d['spec'].get('egress', [])+d['spec'].get('ingress', [])
                 for p in rule.get('ports', [])}
        self.assertEqual(ports, {53, 5432, 4222})
        self.assertNotIn('ipBlock', (root/'staged/strategy-runtime-events/network.yaml').read_text())

    def test_missing_kubectl_is_redacted_and_never_ready(self):
        with patch('subprocess.run', side_effect=FileNotFoundError('private/path')):
            report = audit.report('/not/here', 'rancher')
        self.assertEqual(report['status'], 'BLOCKED_CLUSTER_ACCESS')
        self.assertNotIn('private/path', json.dumps(report))

    def test_preflight_is_read_only_and_secret_values_never_escape(self):
        secret = base64.b64encode(b'do-not-print-this-password').decode()
        commands = []
        def run(args, **kwargs):
            commands.append(args)
            if '--raw=/version' in args:
                payload = {'gitVersion': 'v1.test'}
            elif 'config' in args:
                return SimpleNamespace(returncode=0, stdout='test-cluster\nhttps://example.test:6443', stderr='')
            elif 'namespace' in args:
                payload = {'metadata': {'uid': 'test-namespace'}}
            elif 'secret' in args:
                name = args[args.index('secret')+1]
                payload = {'data': {key: secret for key in audit.SECRETS[name]}}
            elif 'deployments' in args or 'pods' in args:
                payload = {'items': [{'metadata': {'name': 'unrelated'}}]}
            else:
                raise AssertionError('unexpected command')
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr='')
        with patch('subprocess.run', side_effect=run):
            report = audit.report('/test', 'rancher')
        rendered = json.dumps(report)
        self.assertNotIn(secret, rendered)
        self.assertNotIn('do-not-print', rendered)
        self.assertEqual(report['broker_writes'], 0)
        self.assertTrue(report['read_only'])
        self.assertNotEqual(report['status'], 'READY')
        self.assertTrue(report['unverified'])
        self.assertTrue(all('get' in cmd or 'config' in cmd for cmd in commands))

    def test_missing_database_evidence_is_unverified(self):
        for db in (None, {}, {'migrations': None}):
            for check in audit.migration_checks(db).values():
                self.assertIsNone(check['matches'])
                self.assertEqual(check['state'], 'UNVERIFIED')
        self.assertTrue(all(c['matches'] is False for c in audit.migration_checks({'migrations': []}).values()))
        rows = [{'version': v, 'filename': f, 'checksum_sha256': h} for v, (f, h) in audit.MIGRATIONS.items()]
        self.assertTrue(all(c['matches'] is True for c in audit.migration_checks({'migrations': rows}).values()))

    def test_empty_namespace_stops_before_credentials_and_collects_locations(self):
        commands = []
        def run(args, **kwargs):
            commands.append(args)
            if 'config' in args:
                payload = 'test-cluster\nhttps://username:password@example.test:6443/private?token=secret'
            elif 'deployments,statefulsets' in args:
                payload = 'actual-namespace\tStatefulSet\ttrading-postgres\n'
            else:
                payload = json.dumps({'items': [], 'metadata': {'uid': 'test-uid'}})
            return SimpleNamespace(returncode=0, stdout=payload, stderr='')
        with patch('subprocess.run', side_effect=run):
            report = audit.report('/test')
        self.assertEqual(report['status'], 'BLOCKED_EMPTY_TARGET_NAMESPACE')
        self.assertEqual(report['cluster_identity']['server_hostname'], 'example.test')
        self.assertEqual(report['namespace_uid'], 'test-uid')
        self.assertEqual(report['workload_locations'], ['actual-namespace\tStatefulSet\ttrading-postgres'])
        self.assertTrue(all(c['matches'] is None for c in report['migration_checks'].values()))
        self.assertFalse(any('secret' in cmd or 'exec' in cmd for cmd in commands))
        self.assertNotIn('password', json.dumps(report))
        self.assertNotIn('token=secret', json.dumps(report))

    def test_signal_id_cannot_inject_sql(self):
        with self.assertRaises(ValueError):
            audit.report('/test', signal_id="SIG_x';SELECT password FROM secrets;--")
        self.assertIn('default_transaction_read_only=on', audit.DATABASE_SCRIPT)
        self.assertNotIn('.publish(', audit.NATS_SCRIPT)
        for mutation in ('INSERT ', 'UPDATE ', 'DELETE ', 'GRANT ', 'ALTER ', 'CREATE '):
            self.assertNotIn(mutation, audit.DATABASE_SCRIPT.upper())

    def test_diagnostic_logs_keep_only_timing_fields_in_the_signal_window(self):
        start = datetime.datetime.fromisoformat('2026-10-22T11:54:00+00:00')
        end = datetime.datetime.fromisoformat('2026-10-22T11:57:00+00:00')
        text = '\n'.join([
            '2026-10-22T11:55:25Z '+json.dumps({'event': 'context_symbol_poll_completed', 'symbol': 'AUDCADm',
                'elapsed_ms': 1200, 'password': 'never-output-this'}),
            '2026-10-22T11:55:25Z '+json.dumps({'event': 'unknown_error', 'password': 'never-output-this'}),
            '2026-10-22T11:55:25Z '+json.dumps({'event': 'market_quote_observed', 'provider_symbol': 'BTCUSDm'}),
            '2026-10-22T12:55:25Z '+json.dumps({'event': 'context_poll_completed'}),
        ])
        rows = audit.diagnostic_rows(text, 'AUDCADm', start, end)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['elapsed_ms'], 1200)
        self.assertNotIn('password', rows[0])
        self.assertNotIn('never-output-this', json.dumps(rows))


if __name__ == '__main__':
    unittest.main()
