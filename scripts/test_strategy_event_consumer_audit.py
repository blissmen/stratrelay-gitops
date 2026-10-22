import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml
import strategy_event_consumer_audit as audit


class ConsumerActivationTests(unittest.TestCase):
    def test_patches_only_change_transport_on_the_three_intended_containers(self):
        root = Path(__file__).resolve().parents[1]/'apps/trading-platform'
        active = yaml.safe_load((root/'kustomization.yaml').read_text())
        names = [p['path'] for p in active['patches']]
        cases = [('authority','mt5-native-bridge-main-runtime','orchestrator-shadow','signal-orchestrator'),
                 ('liquidity','liquidity-live-runtime','runtime','liquidity-live-runtime'),
                 ('canonical-relay','signal-outbox-relay','relay',None)]
        for key,deployment,container,runtime in cases:
            filename = 'strategy-event-'+key+'.patch.yaml'
            self.assertIn(filename,names)
            obj = yaml.safe_load((root/filename).read_text())
            self.assertEqual(obj['metadata'],{'name':deployment,'namespace':'trading'})
            self.assertEqual(set(obj['spec']),{'template'})
            spec = obj['spec']['template']['spec']
            self.assertEqual(set(spec),{'containers'})
            self.assertEqual(len(spec['containers']),1)
            c = spec['containers'][0]
            self.assertEqual(set(c),{'name','env'})
            self.assertEqual(c['name'],container)
            env = {e['name']:e for e in c['env']}
            expected = {'STRATEGY_EVENT_MODE','NATS_URL','P2_NATS_USER','P2_NATS_PASSWORD'}
            if runtime:expected |= {'STRATEGY_EVENT_RUNTIME_ID','STRATEGY_EVENT_RECONCILE_SECONDS'}
            self.assertEqual(set(env),expected)
            self.assertEqual(env['STRATEGY_EVENT_MODE']['value'],'JETSTREAM')
            if runtime:self.assertEqual(env['STRATEGY_EVENT_RUNTIME_ID']['value'],runtime)
            for name,key_name in [('P2_NATS_USER','NATS_USER'),('P2_NATS_PASSWORD','NATS_PASSWORD')]:
                self.assertEqual(env[name]['valueFrom']['secretKeyRef'],{'name':'trading-nats-auth','key':key_name})

    def test_context_activation_only_changes_scheduling_and_reuses_hint_writer(self):
        root=Path(__file__).resolve().parents[1]/'apps/trading-platform'
        active=yaml.safe_load((root/'kustomization.yaml').read_text())
        self.assertIn({'path':'strategy-event-context.patch.yaml'},active['patches'])
        obj=yaml.safe_load((root/'strategy-event-context.patch.yaml').read_text())
        self.assertEqual(set(obj['spec']),{'template'})
        spec=obj['spec']['template']['spec']
        self.assertEqual(set(spec),{'containers'})
        self.assertEqual({c['name'] for c in spec['containers']},{'context-paper','context-v2-research'})
        for c in spec['containers']:
            self.assertEqual(set(c),{'name','env'})
            env={e['name']:e for e in c['env']}
            self.assertEqual(env['STRATEGY_EVENT_MODE']['value'],'JETSTREAM')
            self.assertEqual(env['STRATEGY_EVENT_DATABASE']['value'],'trading_platform')
            self.assertEqual(env['STRATEGY_EVENT_DSN']['valueFrom']['secretKeyRef'],
                             {'name':'strategy-event-collector-db','key':'STRATEGY_EVENT_DSN'})
            self.assertFalse(any('EXECUTION' in name or 'CUTOFF' in name for name in env))

    def fixture(self):
        runtime = 'signal-orchestrator'
        probe = {'mode':'JETSTREAM','runtime_id':runtime,'market_data_source':'REDIS',
                 'consumer':{'durable':'wakeup-'+runtime+'-strategy_runtime',
                    'filters':['runtime.market.cache.ready.v1.*','runtime.strategy.evidence.ready.v1.*'],
                    'ack_policy':'explicit','deliver_policy':'new','max_ack_pending':64,'delivered':10,'acknowledged':9}}
        row = {'event':'strategy_cycle_woken','runtime_id':runtime,'reason':'EVENT','event_id':'a'*64,'event_received_at':950}
        return runtime,probe,row

    def test_only_current_real_event_wakeup_and_valid_durable_prove_consumption(self):
        runtime,probe,row = self.fixture()
        self.assertTrue(audit.consumer_observed(probe,[row],runtime,900,True))
        for key,value in [('reason','RECONCILIATION'),('event_id','bad'),('event_received_at',800)]:
            changed = {**row,key:value}
            self.assertFalse(audit.consumer_observed(probe,[changed],runtime,900,True))
        self.assertFalse(audit.consumer_observed(probe,[],runtime,900,True))
        probe['mode'] = 'POLL'
        self.assertFalse(audit.consumer_observed(probe,[row],runtime,900,True))
        probe['mode'] = 'JETSTREAM'
        probe['consumer']['filters'] = ['>']
        self.assertFalse(audit.consumer_observed(probe,[row],runtime,900,True))

    def test_log_redaction_and_post_restart_boundary(self):
        runtime,_,row = self.fixture()
        row['password'] = 'never-output'
        raw = '\n'.join([
            '1970-01-01T00:16:00Z '+json.dumps(row),
            '1970-01-01T00:14:00Z '+json.dumps(row),
            '1970-01-01T00:16:00Z '+json.dumps({'event':'unknown','password':'never-output'}),
            'password=never-output'])
        rows = audit.wakeup_rows(raw,runtime,900)
        self.assertEqual(len(rows),1)
        self.assertNotIn('password',json.dumps(rows))
        self.assertNotIn('never-output',json.dumps(rows))

    def test_prefixed_cycle_diagnostics_keep_known_fields_and_do_not_invent_wakeups(self):
        runtime,_,row=self.fixture()
        cycle={'event':'orchestrator_cycle_scheduled','runner':'signal-orchestrator','cycle_elapsed_ms':9000,'password':'private'}
        raw='\n'.join(['1970-01-01T00:16:00Z INFO:stratrelay.strategy_audit:'+json.dumps(cycle),
                       '1970-01-01T00:16:00Z '+json.dumps(row),'unexpected private line'])
        diag=audit.log_diagnostics(raw,900)
        self.assertEqual(diag['capture']['lines'],3)
        self.assertEqual(diag['capture']['known_cycle_events'],1)
        self.assertEqual(diag['capture']['wakeup_events'],1)
        self.assertEqual(diag['capture']['unparsed'],1)
        self.assertEqual(diag['cycle_evidence'][0]['cycle_elapsed_ms'],9000)
        self.assertNotIn('private',json.dumps(diag))
        self.assertEqual(len(audit.wakeup_rows(raw,runtime,900)),1)
        prefixed='1970-01-01T00:16:00Z INFO:stratrelay.strategy_audit:'+json.dumps(row)
        self.assertEqual(len(audit.wakeup_rows(prefixed,runtime,900)),1)

    def test_nanosecond_timestamps_are_normalized_for_older_python(self):
        real_datetime=audit.datetime.datetime
        class LegacyDatetime:
            @staticmethod
            def fromisoformat(value):
                import re
                fraction=re.search(r"\.(\d+)",value)
                if fraction and len(fraction.group(1)) not in (3,6):
                    raise ValueError('legacy precision limit')
                return real_datetime.fromisoformat(value)
        runtime,probe,row=self.fixture()
        stamp='1970-01-01T00:16:00.123456789Z'
        with patch.object(audit.datetime,'datetime',LegacyDatetime):
            self.assertAlmostEqual(audit.parse_timestamp(stamp),960.123456)
            self.assertAlmostEqual(audit.parse_timestamp('1970-01-01T01:16:00.1+01:00'),960.1)
            rows=audit.wakeup_rows(stamp+' '+json.dumps(row),runtime,900)
        self.assertEqual(rows[0]['log_timestamp'],stamp)
        self.assertTrue(audit.consumer_observed(probe,rows,runtime,900,True))
        row['reason']='RECONCILIATION'
        rows=audit.wakeup_rows(stamp+' '+json.dumps(row),runtime,900)
        self.assertFalse(audit.consumer_observed(probe,rows,runtime,900,True))
        for invalid in ('not-a-date','1970-01-01T00:16:00','1970-01-01T00:16:00.1234567890Z'):
            with self.assertRaises(ValueError):audit.parse_timestamp(invalid)

    def test_listener_query_is_read_only_and_ip_is_not_sql(self):
        sql = audit.listener_sql('10.42.0.3')
        self.assertIn('BEGIN READ ONLY',sql)
        self.assertIn("client_addr=inet '10.42.0.3'",sql)
        self.assertNotIn('query)',sql)
        with self.assertRaises(ValueError):audit.listener_sql("10.42.0.3'; DROP TABLE x;--")
        compile(audit.PROBE,'<consumer-probe>','exec')
        self.assertNotIn('.publish(',audit.PROBE)
        self.assertNotIn('pull_subscribe',audit.PROBE)

    def test_full_report_proves_only_activated_wakeups_and_listener(self):
        commands = []
        def run(args,input=None,**kwargs):
            commands.append((args,input))
            if 'namespace' in args:
                value = {'metadata':{'uid':audit.NAMESPACE_UID}}
            elif 'pods' in args:
                items = []
                for i,(label,container,runtime) in enumerate(audit.TARGETS.values(),1):
                    items.append({'metadata':{'name':label+'-pod','labels':{'app.kubernetes.io/name':label}},
                        'status':{'phase':'Running','podIP':'10.42.0.'+str(i),
                            'containerStatuses':[{'name':container,'ready':True,'restartCount':0,'imageID':audit.IMAGE,
                                'state':{'running':{'startedAt':'1970-01-01T00:15:00Z'}}}]}})
                combined={}
                for pod in items:
                    label=pod['metadata']['labels']['app.kubernetes.io/name']
                    if label in combined:
                        combined[label]['status']['containerStatuses'].extend(pod['status']['containerStatuses'])
                    else:combined[label]=pod
                items=list(combined.values())
                items.append({'metadata':{'name':'postgres-0','labels':{'app.kubernetes.io/name':'trading-postgres'}},
                              'status':{'phase':'Running'},'spec':{'containers':[{'name':'postgres'}]}})
                value = {'items':items}
            elif args[-1] == audit.PROBE:
                runtime = json.loads(input)['runtime']
                if runtime:
                    _,value,_ = self.fixture()
                    value['runtime_id'] = runtime
                    value['consumer']['durable'] = 'wakeup-'+runtime+'-strategy_runtime'
                    if runtime != 'signal-orchestrator':value['consumer']['filters']=['runtime.market.cache.ready.v1.*']
                else:value={'mode':'JETSTREAM'}
            elif 'logs' in args:
                runtime = next(runtime for _,container,runtime in audit.TARGETS.values() if container in args)
                _,_,row = self.fixture()
                row['runtime_id'] = runtime
                return SimpleNamespace(returncode=0,stdout='1970-01-01T00:16:00Z '+json.dumps(row),stderr='')
            else:
                self.assertIn('default_transaction_read_only=on',args[-1])
                self.assertEqual(input,audit.listener_sql('10.42.0.3'))
                value={'database':'trading_platform','primary':True,'listener_backends':1}
            return SimpleNamespace(returncode=0,stdout=json.dumps(value),stderr='')
        with patch('subprocess.run',side_effect=run):result = audit.report('/test')
        self.assertEqual(result['status'],'CONSUMER_WAKEUPS_AND_CANONICAL_LISTENER_OBSERVED')
        self.assertFalse(result['all_strategy_consumption_verified'])
        self.assertFalse(result['canonical_signal_latency_verified'])
        self.assertFalse(any(any(word in args for word in ('create','apply','delete','patch')) for args,_ in commands))

    def test_external_errors_are_redacted(self):
        with patch('subprocess.run',return_value=SimpleNamespace(returncode=1,stdout='',stderr='password=private')):
            result = audit.report('/test')
        self.assertEqual(result['status'],'UNVERIFIED')
        self.assertNotIn('private',json.dumps(result))
        self.assertFalse(result['all_strategy_consumption_verified'])
        self.assertFalse(result['canonical_signal_latency_verified'])


if __name__ == '__main__':unittest.main()
