"""Read-only persisted readiness, frozen binding and cache metadata diagnostics."""
import argparse
import datetime
import json
from pathlib import Path
import subprocess

from strategy_event_relay_bootstrap import NAMESPACE_UID

TARGETS = ('ict-smc-forward-v1-observer','strategy-library-canary-v1-observer','donchian-breakout-v2-observer')
PROBE = r'''import json,time
def reason(message):
    text=str(message or '').lower()
    for needles,code in [
        (('run identity/configuration/artifact changed',),'FROZEN_BINDING_CONFLICT'),
        (('stale canonical cache',),'STALE_CANONICAL_CACHE'),
        (('unhealthy canonical cache',),'UNHEALTHY_CANONICAL_CACHE'),
        (('canonical market data','stale'),'STALE_CANONICAL_MARKET_DATA'),
        (('symbol metadata','stale'),'SYMBOL_METADATA_MISSING_OR_STALE'),
        (('canonical market data','missing'),'MISSING_CANONICAL_MARKET_DATA'),
        (('cached quote','stale'),'STALE_CACHED_QUOTE'),
        (('no cached quote',),'MISSING_CACHED_QUOTE'),
        (('conflicting completed candle revision',),'COMPLETED_CANDLE_REVISION'),
        (('gap',),'DATA_GAP'),
        (('bars','needs'),'INSUFFICIENT_BARS'),
        (('tick_size',),'TICK_METADATA_INVALID'),
        (('context',),'CONTEXT_UNAVAILABLE')]:
        if all(n in text for n in needles):return code
    return 'ERROR_MESSAGE_REDACTED' if text else None
def age(now,value):
    try:return now-float(value)
    except (TypeError,ValueError):return None
result={'read_only':True,'broker_writes':0,'observed_at':time.time()}
try:
    from research.strategy_library_forward import RunConfig,ForwardObserver
    config=RunConfig.load('/config/run.json')
    result.update(run_id=config.run_id,max_age_seconds=config.max_age_seconds)
    try:
        from postgres.db import connect
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SET TRANSACTION READ ONLY')
                cur.execute("SET LOCAL statement_timeout='5s'")
                cur.execute("SELECT state->'health',state->>'binding' FROM research.strategy_library_forward_state WHERE run_id=%s",(config.run_id,))
                row=cur.fetchone()
        health=(row[0] or {}) if row else {}
        result['persisted']={'state_exists':row is not None,'binding_matches':bool(row and row[1]==ForwardObserver(config).binding),
            'health_status':health.get('status'),'health_observed_at':health.get('observed_at'),
            'health_age_seconds':age(time.time(),health.get('observed_at')),'streams':[]}
        streams=health.get('streams') or []
        result['persisted']['stream_count']=len(streams)
        for stream in streams[:500]:
            item={k:stream.get(k) for k in ('key','instrument','status','error_type','records','last_finalized_close')}
            item['reason_code']=reason(stream.get('error_message'))
            result['persisted']['streams'].append(item)
        result['persisted']['streams_truncated']=len(streams)>500
    except Exception as exc:result['database_error_type']=type(exc).__name__
    try:
        from market_data_cache.reader import default_store
        store=default_store()
        health=store.health()
        result['collector_health']={k:health.get(k) for k in ('status','process_heartbeat_at','last_progress_at','last_completed_pass_at')}
        result['symbols']=[]
        for instrument,provider in config.symbols.items():
            state=store.state(provider)
            snapshot=store.snapshot(provider) or {}
            quote=store.quote(provider) or {}
            item={'instrument':instrument,'provider_symbol':provider,
                'state':{k:state.get(k) for k in ('healthy','health_state','fetched_at','last_attempt_at','backfill_required','consecutive_failures')},
                'snapshot_age_seconds':age(time.time(),snapshot.get('fetched_at')),
                'quote_observed_age_seconds':age(time.time(),quote.get('observed_at')),
                'provider_quote_age_seconds':age(time.time(),(quote.get('quote') or {}).get('time')),'bars':{}}
            for tf in ('M5','M15','H1'):
                bars=store.bars(provider,tf)
                item['bars'][tf]={'count':len(bars),'last_open_time':bars[-1].get('time') if bars else None}
            result['symbols'].append(item)
    except Exception as exc:result['cache_error_type']=type(exc).__name__
except Exception as exc:result['configuration_error_type']=type(exc).__name__
print(json.dumps(result))
'''


def report(kubeconfig,context='rancher'):
    base=['kubectl','--kubeconfig',kubeconfig,'--context',context,'-n','trading','--request-timeout=15s']
    result={'status':'READ_ONLY_DIAGNOSTICS','read_only':True,'broker_writes':0,
            'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'observers':[]}
    def call(args):
        p=subprocess.run(base+args,capture_output=True,text=True,timeout=30)
        if p.returncode:raise RuntimeError('KUBECTL_FAILED_REDACTED')
        return json.loads(p.stdout)
    try:
        if call(['get','namespace','trading','-o','json'])['metadata']['uid']!=NAMESPACE_UID:
            raise ValueError('CLUSTER_IDENTITY_MISMATCH')
        pods=call(['get','pods','-o','json'])['items']
        for label in TARGETS:
            entry={'deployment':label}
            result['observers'].append(entry)
            try:
                matches=[p for p in pods if p['metadata'].get('labels',{}).get('app.kubernetes.io/name')==label
                         and not p['metadata'].get('deletionTimestamp')]
                if len(matches)!=1:raise ValueError('UNIQUE_OBSERVER_POD_REQUIRED')
                pod=matches[0]
                entry.update(pod=pod['metadata']['name'],phase=pod.get('status',{}).get('phase'),containers=[])
                for c in pod.get('status',{}).get('containerStatuses',[])+pod.get('status',{}).get('initContainerStatuses',[]):
                    terminated=c.get('lastState',{}).get('terminated',{})
                    entry['containers'].append({'name':c['name'],'ready':c.get('ready'),'restarts':c.get('restartCount'),
                        'image_id':c.get('imageID'),'waiting_reason':c.get('state',{}).get('waiting',{}).get('reason'),
                        'last_termination_reason':terminated.get('reason'),'last_exit_code':terminated.get('exitCode')})
                entry['diagnostics']=call(['exec',pod['metadata']['name'],'-c','observer','--','python','-c',PROBE])
            except Exception as exc:entry['error_type']=type(exc).__name__
    except Exception as exc:
        result['status']='UNVERIFIED'
        result['error_type']=type(exc).__name__
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kubeconfig',required=True)
    parser.add_argument('--context',default='rancher')
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    result=report(args.kubeconfig,args.context)
    Path(args.output).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'read_only':True,'report':str(Path(args.output).resolve())}))


if __name__=='__main__':main()
