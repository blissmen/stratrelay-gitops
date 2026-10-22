"""Read-only proof of current consumer wakeups and canonical relay LISTEN backend."""
import argparse
import datetime
import ipaddress
import json
from pathlib import Path
import re
import subprocess

from strategy_event_preflight import IMAGE
from strategy_event_relay_bootstrap import ADMIN_COMMAND, NAMESPACE_UID

TARGETS = {
    'authority': ('mt5-native-bridge','orchestrator-shadow','signal-orchestrator'),
    'liquidity': ('liquidity-live-runtime','runtime','liquidity-live-runtime'),
    'canonical_relay': ('signal-outbox-relay','relay',None),
}
PROBE = r'''import asyncio,json,os,sys
async def probe():
    request=json.load(sys.stdin)
    runtime=request.get('runtime')
    result={'mode':os.environ.get('STRATEGY_EVENT_MODE','POLL'),
            'runtime_id':os.environ.get('STRATEGY_EVENT_RUNTIME_ID'),
            'market_data_source':os.environ.get('MARKET_DATA_SOURCE')}
    if runtime is not None:
        import nats
        nc=None
        try:
            nc=await nats.connect(os.environ['NATS_URL'],user=os.environ['P2_NATS_USER'],password=os.environ['P2_NATS_PASSWORD'],connect_timeout=5,max_reconnect_attempts=0)
            durable='wakeup-'+runtime+'-strategy_runtime'
            info=await nc.jetstream().consumer_info('STRATEGY_RUNTIME',durable)
            c=info.config
            result['consumer']={'durable':durable,'filters':c.filter_subjects or [c.filter_subject],
                'ack_policy':c.ack_policy,'deliver_policy':c.deliver_policy,'max_ack_pending':c.max_ack_pending,
                'delivered':info.delivered.consumer_seq,'acknowledged':info.ack_floor.consumer_seq,
                'pending':info.num_pending,'ack_pending':info.num_ack_pending,'redelivered':info.num_redelivered}
        except Exception as exc:
            result['consumer_error_type']=type(exc).__name__
        finally:
            if nc is not None:await nc.close()
    print(json.dumps(result))
try:asyncio.run(probe())
except Exception as exc:print(json.dumps({'error_type':type(exc).__name__}))
'''


def wakeup_rows(raw, runtime, started):
    rows = []
    for line in raw.splitlines():
        try:
            stamp, payload = line.split(' ',1)
            timestamp = datetime.datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp()
            obj = json.loads(payload)
            if timestamp < started or obj.get('runtime_id') != runtime:
                continue
            if obj.get('event') not in ('strategy_cycle_woken','strategy_event_subscription_failed','strategy_nats_error'):
                continue
            fields = ('event','runtime_id','reason','event_id','event_received_at','dispatch_ms','generation','error_type')
            rows.append({'log_timestamp':stamp, **{k:obj[k] for k in fields if k in obj}})
        except (ValueError,TypeError,AttributeError):
            continue
    return rows[-100:]


def consumer_observed(probe, rows, runtime, started, authority=False):
    c = probe.get('consumer') or {}
    expected = {'runtime.market.cache.ready.v1.*'}
    if authority:expected.add('runtime.strategy.evidence.ready.v1.*')
    events = [r for r in rows if r.get('event') == 'strategy_cycle_woken' and r.get('reason') == 'EVENT'
              and isinstance(r.get('event_id'),str) and re.fullmatch('[0-9a-f]{64}',r['event_id'])
              and isinstance(r.get('event_received_at'),(int,float)) and r['event_received_at'] >= started]
    failed = any(r.get('event') == 'strategy_event_subscription_failed' for r in rows)
    return (probe.get('mode') == 'JETSTREAM' and probe.get('runtime_id') == runtime
            and probe.get('market_data_source') == 'REDIS' and not failed and bool(events)
            and c.get('durable') == 'wakeup-'+runtime+'-strategy_runtime'
            and set(c.get('filters') or []) == expected and c.get('ack_policy') == 'explicit'
            and c.get('deliver_policy') == 'new' and c.get('max_ack_pending') == 64
            and c.get('delivered',0) > 0 and c.get('acknowledged',0) > 0)


def listener_sql(pod_ip):
    address = str(ipaddress.ip_address(pod_ip))
    return """BEGIN READ ONLY;
SELECT json_build_object('database',current_database(),'primary',NOT pg_is_in_recovery(),
 'listener_backends',count(*)) FROM pg_stat_activity
 WHERE client_addr=inet '%s' AND state='idle'
 AND btrim(query,' ;')='LISTEN canonical_outbox_events';
COMMIT;
""" % address


def report(kubeconfig, context='rancher'):
    base = ['kubectl','--kubeconfig',kubeconfig,'--context',context,'-n','trading','--request-timeout=15s']
    result = {'status':'UNVERIFIED','read_only':True,'broker_writes':0,
              'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'targets':{},
              'all_strategy_consumption_verified':False,'canonical_signal_latency_verified':False}
    stage = 'cluster identity'
    def call(args,payload=None,raw=False):
        p = subprocess.run(base+args,input=payload,capture_output=True,text=True,timeout=25)
        if p.returncode:raise RuntimeError('KUBECTL_FAILED_REDACTED')
        return p.stdout if raw else json.loads(p.stdout)
    try:
        if call(['get','namespace','trading','-o','json'])['metadata']['uid'] != NAMESPACE_UID:
            raise ValueError('CLUSTER_IDENTITY_MISMATCH')
        pods = call(['get','pods','-o','json'])['items']
        canonical_ip = None
        for key,(label,container,runtime) in TARGETS.items():
            stage = key+' runtime and wakeups'
            matching = [p for p in pods if p['metadata'].get('labels',{}).get('app.kubernetes.io/name') == label
                        and not p['metadata'].get('deletionTimestamp') and p.get('status',{}).get('phase') == 'Running']
            if len(matching) != 1:raise ValueError('UNIQUE_RUNNING_TARGET_REQUIRED')
            p = matching[0]
            state = next(c for c in p['status']['containerStatuses'] if c['name'] == container)
            target = {'pod':p['metadata']['name'],'container':container,'ready':state.get('ready'),
                      'restarts':state.get('restartCount'),'image_id':state.get('imageID')}
            result['targets'][key] = target
            if not state.get('ready') or state.get('imageID') != IMAGE:
                raise ValueError('TARGET_NOT_READY_OR_IMAGE_MISMATCH')
            started = datetime.datetime.fromisoformat(state['state']['running']['startedAt'].replace('Z','+00:00')).timestamp()
            target['started_at'] = state['state']['running']['startedAt']
            probe = call(['exec','-i',p['metadata']['name'],'-c',container,'--','python','-c',PROBE],json.dumps({'runtime':runtime}))
            target['probe'] = probe
            if runtime is not None:
                raw = call(['logs',p['metadata']['name'],'-c',container,'--timestamps=true','--since=120s','--tail=2000'],raw=True)
                rows = wakeup_rows(raw,runtime,started)
                target['wakeup_evidence'] = rows
                target['event_wakeup_observed'] = consumer_observed(probe,rows,runtime,started,key == 'authority')
            else:
                canonical_ip = p['status']['podIP']
        stage = 'canonical LISTEN backend'
        pg = [p for p in pods if p['metadata'].get('labels',{}).get('app.kubernetes.io/name') == 'trading-postgres'
              and p.get('status',{}).get('phase') == 'Running' and not p['metadata'].get('deletionTimestamp')]
        if len(pg) != 1:raise ValueError('UNIQUE_PRIMARY_POD_REQUIRED')
        command = ADMIN_COMMAND.replace('-c statement_timeout=5000','-c default_transaction_read_only=on -c statement_timeout=5000')
        listener = call(['exec','-i',pg[0]['metadata']['name'],'-c',pg[0]['spec']['containers'][0]['name'],'--','sh','-c',command],listener_sql(canonical_ip))
        result['canonical_listener'] = listener
        listener_ok = (listener.get('database') == 'trading_platform' and listener.get('primary') is True
                       and listener.get('listener_backends',0) > 0
                       and result['targets']['canonical_relay']['probe'].get('mode') == 'JETSTREAM')
        result['canonical_listener_observed'] = listener_ok
        events_ok = all(result['targets'][k].get('event_wakeup_observed') for k in ('authority','liquidity'))
        result['status'] = ('CONSUMER_WAKEUPS_AND_CANONICAL_LISTENER_OBSERVED' if listener_ok and events_ok
                            else 'PENDING_OR_DEGRADED_CONSUMER_EVIDENCE')
    except Exception as exc:
        result['error_type'] = type(exc).__name__
        result['failed_stage'] = stage
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kubeconfig',required=True)
    parser.add_argument('--context',default='rancher')
    parser.add_argument('--output',required=True)
    args = parser.parse_args()
    result = report(args.kubeconfig,args.context)
    Path(args.output).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'read_only':True,'report':str(Path(args.output).resolve())}))


if __name__ == '__main__':main()
