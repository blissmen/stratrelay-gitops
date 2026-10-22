import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import strategy_event_collector_audit as audit


class CollectorAuditTests(unittest.TestCase):
    def fixture(self):
        cache = {'observed_at': 1000, 'process_heartbeat_at': 999, 'mode': 'JETSTREAM',
                 'runtime_id': 'market-data-collector', 'database': 'trading_platform', 'dsn_present': True,
                 'strategy_event_health': {'status': 'RUNNING', 'last_success_at': 998, 'last_error_type': None}}
        hints = {'database': 'trading_platform', 'primary': True, 'published': 10}
        stream = {'status': 'OBSERVED', 'subjects': ['runtime.market.cache.ready.v1.*','runtime.strategy.evidence.ready.v1.*'],
                  'storage': 'file', 'discard': 'new', 'max_age': 300, 'max_bytes': 67108864, 'max_msg_size': 1024,
                  'messages': 0}
        return cache,hints,stream

    def test_zero_retained_messages_does_not_erase_durable_delivery_evidence(self):
        self.assertTrue(audit.delivery_observed(*self.fixture(), 900))

    def test_pod_ready_alone_or_wrong_mode_never_proves_delivery(self):
        for field, value in [('mode','POLL'), ('runtime_id','other'), ('database','other'), ('dsn_present',False),
                             ('process_heartbeat_at',800), ('process_heartbeat_at',1001)]:
            cache,hints,stream = self.fixture()
            cache[field] = value
            self.assertFalse(audit.delivery_observed(cache,hints,stream,900))
        for key, value in [('status','DEGRADED'), ('last_success_at',800), ('last_success_at',float('nan')),
                           ('last_error_type','InsufficientPrivilege')]:
            cache,hints,stream = self.fixture()
            cache['strategy_event_health'][key] = value
            self.assertFalse(audit.delivery_observed(cache,hints,stream,900))
        cache,hints,stream = self.fixture()
        hints['published'] = 0
        self.assertFalse(audit.delivery_observed(cache,hints,stream,900))
        hints['published'] = 10
        stream['max_age'] = 86400
        self.assertFalse(audit.delivery_observed(cache,hints,stream,900))

    def test_hint_query_is_bounded_read_only_and_rejects_invalid_time(self):
        sql = audit.hint_sql(900)
        self.assertIn('BEGIN READ ONLY',sql)
        self.assertIn('LIMIT 50',sql)
        self.assertIn('to_timestamp(900.000000)',sql)
        self.assertNotIn('payload',sql)
        for bad in ('900; DROP TABLE x',float('nan'),float('inf'),0):
            with self.assertRaises(ValueError):
                audit.hint_sql(bad)

    def test_cli_failures_never_expose_raw_errors(self):
        with patch('subprocess.run', return_value=SimpleNamespace(returncode=1,stdout='',stderr='password=private')):
            result = audit.report('/test')
        self.assertEqual(result['status'],'UNVERIFIED')
        self.assertNotIn('private',json.dumps(result))
        self.assertEqual(result['failed_stage'],'cluster identity')

    def test_full_audit_never_publishes_or_changes_workloads(self):
        cache,hints,stream = self.fixture()
        commands = []
        def run(args,input=None,**kwargs):
            commands.append((args,input))
            if 'namespace' in args:
                payload = {'metadata': {'uid': audit.NAMESPACE_UID}}
            elif 'pods' in args:
                items = []
                for label,container in [('market-data-collector','collector'),('strategy-runtime-event-relay','relay'),('trading-postgres','postgres')]:
                    items.append({'metadata': {'name':label+'-pod','labels':{'app.kubernetes.io/name':label}},
                                  'spec': {'containers':[{'name':container}]},
                                  'status': {'phase':'Running','startTime':'1970-01-01T00:15:00Z',
                                             'containerStatuses':[{'name':container,'ready':True,'restartCount':0,'imageID':audit.IMAGE}]}})
                payload = {'items':items}
            elif args[-1] == audit.CACHE_PROBE:
                payload = cache
            elif args[-1] == audit.NATS_SCRIPT:
                payload = stream
            else:
                self.assertIn('default_transaction_read_only=on',args[-1])
                self.assertEqual(input,audit.hint_sql(900))
                payload = hints
            return SimpleNamespace(returncode=0,stdout=json.dumps(payload),stderr='')
        with patch('subprocess.run',side_effect=run):
            result = audit.report('/test')
        self.assertEqual(result['status'],'COLLECTOR_HINT_DELIVERY_OBSERVED')
        self.assertFalse(result['strategy_consumption_verified'])
        self.assertTrue(result['read_only'])
        self.assertFalse(any(any(word in args for word in ('create','apply','delete','patch')) for args,_ in commands))
        self.assertNotIn('.publish(',audit.NATS_SCRIPT)


if __name__ == '__main__':
    unittest.main()
