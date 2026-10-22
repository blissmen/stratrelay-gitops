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
    from pathlib import Path
    import hashlib
    module=Path('/app/infrastructure/messaging/runtime_events.py')
    if module.is_file():
        code=module.read_bytes()
        result['wakeup_log_contract']={'sha256':hashlib.sha256(code).hexdigest(),
            'event_literal_present':b'strategy_cycle_woken' in code}
    if runtime=='signal-orchestrator':
        try:
            path=Path(os.environ.get('TRADING_PLATFORM_RUNTIME_DIR','/work/runtime'))/'orchestration'/'heartbeat.json'
            heartbeat=json.loads(path.read_text())
            result['orchestrator_heartbeat']={k:heartbeat.get(k) for k in ('timestamp','status','mode','cycle_elapsed_s','interval_s','signals_seen')}
        except Exception as exc:
            result['heartbeat_error_type']=type(exc).__name__
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


def parsed_log(line):
    stamp,payload=line.split(' ',1)
    timestamp=datetime.datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp()
    payload=payload.lstrip()
    style='JSON'
    for level in ('INFO','WARNING','ERROR','DEBUG'):
        prefix=level+':stratrelay.strategy_audit:'
        if payload.startswith(prefix):
            payload=payload[len(prefix):]
            style='STRATEGY_AUDIT_LOGGER'
            break
    obj=json.loads(payload)
    if not isinstance(obj,dict):raise ValueError('NON_OBJECT_LOG')
    return stamp,timestamp,obj,style


def log_diagnostics(raw, started):
    counts={'lines':len(raw.splitlines()),'parsed_json':0,'unparsed':0,'before_container_start':0,
            'wakeup_events':0,'known_cycle_events':0,'other_events':0,'styles':{}}
    rows=[]
    known={'orchestrator_cycle_scheduled','runner_cycle_started','runner_cycle_scheduled',
           'open_outcomes_backfilled','open_entries_monitored','monitor_only_cycle_completed',
           'runner_heartbeat','tick_failed'}
    fields=('event','runner','status','timestamp','elapsed_ms','cycle_elapsed_ms','configured_interval_ms',
            'next_cycle_delay_ms','count','terminal_count','runner_consecutive_failures')
    for line in raw.splitlines():
        try:stamp,timestamp,obj,style=parsed_log(line)
        except (ValueError,TypeError,AttributeError):
            counts['unparsed']+=1
            continue
        counts['parsed_json']+=1
        counts['styles'][style]=counts['styles'].get(style,0)+1
        if timestamp<started:
            counts['before_container_start']+=1
            continue
        if obj.get('event')=='strategy_cycle_woken':counts['wakeup_events']+=1
        elif obj.get('event') in known:
            counts['known_cycle_events']+=1
            rows.append({'log_timestamp':stamp,**{k:obj[k] for k in fields if k in obj}})
        else:counts['other_events']+=1
    return {'capture':counts,'cycle_evidence':rows[-30:]}


def wakeup_rows(raw, runtime, started):
    rows = []
    for line in raw.splitlines():
        try:
            stamp,timestamp,obj,_=parsed_log(line)
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
                target['log_diagnostics']=log_diagnostics(raw,started)
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
