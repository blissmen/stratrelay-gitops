"""Read-only cache-writer -> scheduling outbox -> NATS relay rollout evidence."""
import argparse
import datetime
import json
import math
from pathlib import Path
import subprocess

from strategy_event_preflight import IMAGE, NATS_SCRIPT
from strategy_event_relay_bootstrap import ADMIN_COMMAND, NAMESPACE_UID

CACHE_PROBE = r'''import json,os,time
try:
    import redis
    client=redis.Redis.from_url(os.environ['MARKET_DATA_REDIS_URL'],socket_connect_timeout=5,socket_timeout=5)
    health=json.loads(client.get('md:health') or '{}')
    event=health.get('strategy_event_health') or {}
    print(json.dumps({'observed_at':time.time(),
        'mode':os.environ.get('STRATEGY_EVENT_MODE','POLL'),
        'runtime_id':os.environ.get('STRATEGY_EVENT_RUNTIME_ID'),
        'database':os.environ.get('STRATEGY_EVENT_DATABASE'),
        'dsn_present':bool(os.environ.get('STRATEGY_EVENT_DSN')),
        'process_heartbeat_at':health.get('process_heartbeat_at'),
        'last_progress_at':health.get('last_progress_at'),
        'collector_status':health.get('status'),
        'strategy_event_health':{k:event.get(k) for k in ('status','last_success_at','last_error_type','pending_symbols')}}))
except Exception as exc:
    print(json.dumps({'status':'UNVERIFIED','error_type':type(exc).__name__}))
'''


def hint_sql(start):
    if not isinstance(start, (int, float)) or not math.isfinite(start) or start <= 0:
        raise ValueError('INVALID_POD_START')
    return """BEGIN READ ONLY;
WITH recent AS (
 SELECT event_id,created_at,published_at FROM platform.strategy_runtime_outbox
 WHERE runtime_id='market-data-collector' AND created_at>=to_timestamp(%.6f)
 ORDER BY created_at DESC LIMIT 50)
SELECT json_build_object('database',current_database(),'primary',NOT pg_is_in_recovery(),
 'sample_size',count(*),'published',count(published_at),
 'latest_created_at',max(created_at),'latest_published_at',max(published_at),
 'max_created_to_published_ms',max(extract(epoch FROM published_at-created_at)*1000),
 'samples',COALESCE(json_agg(json_build_object('event_id',event_id,'created_at',created_at,
 'published_at',published_at)),'[]'::json)) FROM recent;
COMMIT;
""" % start


def delivery_observed(cache, hints, stream, start):
    """This is producer-to-relay evidence, never strategy consumption/execution evidence."""
    if not all(isinstance(obj, dict) for obj in (cache, hints, stream)):
        return False
    event = cache.get('strategy_event_health') or {}
    try:
        now = float(cache['observed_at'])
        heartbeat, success = float(cache['process_heartbeat_at']), float(event['last_success_at'])
        fresh = all(math.isfinite(t) and start <= t <= now and now-t <= 60 for t in (heartbeat, success))
    except (TypeError, ValueError, KeyError):
        return False
    return (fresh and cache.get('mode') == 'JETSTREAM'
            and cache.get('runtime_id') == 'market-data-collector'
            and cache.get('database') == 'trading_platform' and cache.get('dsn_present') is True
            and event.get('status') == 'RUNNING' and event.get('last_error_type') is None
            and hints.get('database') == 'trading_platform' and hints.get('primary') is True
            and hints.get('published', 0) > 0 and stream.get('status') == 'OBSERVED'
            and set(stream.get('subjects') or []) == {'runtime.market.cache.ready.v1.*', 'runtime.strategy.evidence.ready.v1.*'}
            and stream.get('storage') == 'file' and stream.get('discard') == 'new'
            and stream.get('max_age') == 300 and stream.get('max_bytes') == 67108864
            and stream.get('max_msg_size') == 1024)


def report(kubeconfig, context='rancher'):
    base = ['kubectl', '--kubeconfig', kubeconfig, '--context', context, '-n', 'trading', '--request-timeout=15s']
    result = {'status': 'UNVERIFIED', 'read_only': True, 'broker_writes': 0,
              'observed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'pods': {},
              'scope': 'COLLECTOR_TO_SCHEDULING_RELAY_ONLY', 'strategy_consumption_verified': False}
    stage = 'cluster identity'
    def call(args, payload=None):
        p = subprocess.run(base+args, input=payload, capture_output=True, text=True, timeout=25)
        if p.returncode:
            raise RuntimeError('KUBECTL_FAILED_REDACTED')
        return json.loads(p.stdout)
    try:
        ns = call(['get', 'namespace', 'trading', '-o', 'json'])
        if ns['metadata']['uid'] != NAMESPACE_UID:
            raise ValueError('CLUSTER_IDENTITY_MISMATCH')
        stage = 'pod inventory'
        pods = call(['get', 'pods', '-o', 'json'])['items']
        selected = {}
        for label, container in [('market-data-collector','collector'),('strategy-runtime-event-relay','relay'),('trading-postgres',None)]:
            matches = [p for p in pods if p['metadata'].get('labels',{}).get('app.kubernetes.io/name') == label
                       and p.get('status',{}).get('phase') == 'Running' and not p['metadata'].get('deletionTimestamp')]
            if len(matches) != 1:
                raise ValueError('UNIQUE_RUNNING_POD_REQUIRED')
            pod = matches[0]
            container = container or pod['spec']['containers'][0]['name']
            state = next(c for c in pod['status']['containerStatuses'] if c['name'] == container)
            result['pods'][label] = {'name': pod['metadata']['name'], 'container': container,
                                     'ready': state.get('ready'), 'restarts': state.get('restartCount'),
                                     'image_id': state.get('imageID'), 'started_at': state.get('state',{}).get('running',{}).get('startedAt') or pod['status'].get('startTime')}
            if not state.get('ready') or (label != 'trading-postgres' and state.get('imageID') != IMAGE):
                raise ValueError('POD_NOT_READY_OR_IMAGE_MISMATCH')
            selected[label] = (pod['metadata']['name'], container)
        stage = 'collector cache health'
        name, container = selected['market-data-collector']
        cache = call(['exec', name, '-c', container, '--', 'python', '-c', CACHE_PROBE])
        result['cache_health'] = cache
        start = datetime.datetime.fromisoformat(result['pods']['market-data-collector']['started_at'].replace('Z','+00:00')).timestamp()
        stage = 'bounded post-start scheduling hints'
        name, container = selected['trading-postgres']
        command = ADMIN_COMMAND.replace('-c statement_timeout=5000', '-c default_transaction_read_only=on -c statement_timeout=5000')
        hints = call(['exec', '-i', name, '-c', container, '--', 'sh', '-c', command], hint_sql(start))
        result['post_start_hints'] = hints
        stage = 'NATS stream INFO'
        name, container = selected['strategy-runtime-event-relay']
        stream = call(['exec', name, '-c', container, '--', 'python', '-c', NATS_SCRIPT])
        result['stream'] = stream
        result['status'] = ('COLLECTOR_HINT_DELIVERY_OBSERVED' if delivery_observed(cache,hints,stream,start)
                            else 'PENDING_OR_DEGRADED_COLLECTOR_HINT_EVIDENCE')
    except Exception as exc:
        result['error_type'] = type(exc).__name__
        result['failed_stage'] = stage
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kubeconfig', required=True)
    parser.add_argument('--context', default='rancher')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = report(args.kubeconfig, args.context)
    Path(args.output).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'read_only':True,'report':str(Path(args.output).resolve())}))


if __name__ == '__main__':
    main()
