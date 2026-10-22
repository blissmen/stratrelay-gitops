"""Read-only event rollout audit. Prints metadata only, never Secret/DSN values."""
import argparse
import datetime
import json
from pathlib import Path
import re
import subprocess

from library_jetstream_preflight import failure_reason

SOURCE = '410e55f4453fb7bb06cb951e143aa6040fb3c478'
IMAGE = 'ghcr.io/strat-relay/trading-platform-runtime@sha256:f7b4ea91af4e5c6866bdceaa0e1f10ade9c340d7e070ea110c65fa11865ee872'
MIGRATIONS = {
    '072': ('072_strategy_runtime_events.sql', 'de8fcf5e25a364b83626a142b371dd4d22276c09fef678aadf453f3081dcf1ea'),
    '073': ('073_kojo_v3_execution_routing.sql', 'd3096e402f8391acbf9d7fd2d6ccb4442cc5b97bf3d3157a2ddf7cc66762a0b7'),
    '074': ('074_canonical_outbox_notifications.sql', '330e4c3cd8194eeffad3998bba661cb5132ae8aff9addad352a8df175c09fcf6'),
}
NATS_KEYS = ['NATS_URL', 'P2_NATS_USER', 'P2_NATS_PASSWORD']
SECRETS = {
    'strategy-event-collector-db': ['STRATEGY_EVENT_DSN'],
    'strategy-event-relay': ['STRATEGY_EVENT_DSN', *NATS_KEYS],
    'strategy-event-provision-streams': NATS_KEYS,
    **{'strategy-event-'+name: [*NATS_KEYS, *(['STRATEGY_EVENT_DSN'] if name.startswith('context-') else [])]
       for name in ('authority', 'context-v1', 'context-v2', 'liquidity', 'pa-sr', 'pa-engulf',
                    'library-canary', 'ict-smc', 'donchian-v2')},
}
SAFE_ENV = {'STRATEGY_EVENT_MODE', 'STRATEGY_EVENT_RUNTIME_ID', 'STRATEGY_EVENT_DATABASE',
            'STRATEGY_EVENT_RECONCILE_SECONDS', 'MARKET_DATA_SOURCE', 'MARKET_DATA_EVENT_MODE',
            'EXECUTION_AUTHORITY_MODE', 'LIQUIDITY_LIVE_RUNTIME_MODE'}

DATABASE_SCRIPT = r'''set +x
test -n "${POSTGRES_USER:-}" && test -n "${POSTGRES_PASSWORD:-}" || exit 4
export PGPASSWORD="$POSTGRES_PASSWORD"
export PGOPTIONS='-c default_transaction_read_only=on -c statement_timeout=10000'
exec psql -X -qAt -w -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d trading_platform <<'SQL'
SELECT json_build_object(
 'database',current_database(),'role',current_user,'primary',NOT pg_is_in_recovery(),
 'migrations',(SELECT json_agg(json_build_object('version',version,'filename',filename,'checksum_sha256',checksum_sha256))
               FROM platform.schema_migrations WHERE version IN ('072','073','074')),
 'ready_outbox',to_regclass('platform.strategy_runtime_outbox') IS NOT NULL,
 'canonical_notify_trigger',EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='canonical_outbox_notify' AND NOT tgisinternal),
 'event_roles',(SELECT json_agg(json_build_object('role',rolname,'superuser',rolsuper,'can_login',rolcanlogin,'bypass_rls',rolbypassrls))
               FROM pg_roles WHERE rolname LIKE 'strategy_event_%'),
 'signal',(SELECT json_build_object('signal_id',e.signal_id,'strategy_id',e.strategy_id,'instrument',e.instrument,
     'provider_symbol',COALESCE(e.source_provenance->>'provider_symbol',
       (SELECT provider_symbol FROM platform.instrument_provider_mapping m
        WHERE m.provider='MT5' AND m.state='ACTIVE' AND m.canonical_instrument=e.instrument LIMIT 1)),
     'decision_time',e.decision_time,'signal_emitted_at',e.signal_emitted_at,'ingested_at',e.ingested_at,
     'decision_to_emission_seconds',extract(epoch from e.signal_emitted_at-e.decision_time),
     'source_process',e.source_provenance->>'source_process',
     'source_market_data_timestamp',e.source_provenance->>'source_market_data_timestamp',
     'source_data_age',e.source_provenance->'source_data_age')
     FROM strategy.entry_signals e WHERE e.signal_id='__SIGNAL_ID__'),
 'signal_outbox',(SELECT json_agg(json_build_object('event_type',event_type,'created_at',created_at,
     'published_at',published_at,'publish_status',publish_status)) FROM platform.outbox_events
     WHERE aggregate_id='__SIGNAL_ID__'));
SQL
'''

RUNTIME_SCRIPT = r'''import json,os
keys=('STRATEGY_EVENT_MODE','STRATEGY_EVENT_RUNTIME_ID','STRATEGY_EVENT_DATABASE',
      'MARKET_DATA_SOURCE','MARKET_DATA_EVENT_MODE','EXECUTION_AUTHORITY_MODE')
result={key:os.environ.get(key) for key in keys}
result['effective_event_mode']=os.environ.get('STRATEGY_EVENT_MODE','POLL')
result['credential_keys_present']=all(os.environ.get(key) for key in ('NATS_URL','P2_NATS_USER','P2_NATS_PASSWORD'))
result['transport_module_exists']=os.path.isfile('/app/infrastructure/messaging/runtime_events.py')
print(json.dumps(result))
'''

NATS_SCRIPT = r'''import asyncio,json,os
async def audit():
    import nats
    nc=None
    try:
        nc=await nats.connect(os.environ['NATS_URL'],user=os.environ.get('P2_NATS_USER'),
            password=os.environ.get('P2_NATS_PASSWORD'),connect_timeout=3,max_reconnect_attempts=0)
        info=await nc.jetstream().stream_info('STRATEGY_RUNTIME')
        c=info.config
        print(json.dumps({'status':'OBSERVED','subjects':c.subjects,'storage':c.storage,
            'discard':c.discard,'max_age':c.max_age,'max_bytes':c.max_bytes,'max_msg_size':c.max_msg_size,
            'messages':info.state.messages,'bytes':info.state.bytes}))
    except Exception as exc:
        print(json.dumps({'status':'UNVERIFIED','error_type':type(exc).__name__,
                          'error_code':getattr(exc,'err_code',None)}))
    finally:
        if nc is not None:await nc.close()
asyncio.run(audit())
'''


def relevant(name):
    return any(token in name for token in ('observer', 'pa-tick-', 'main-runtime', 'liquidity-live',
                                          'signal-outbox', 'market-data-collector', 'strategy-runtime-event'))


def diagnostic_rows(text, provider, since, until):
    events = {'context_symbol_poll_completed', 'context_poll_completed', 'context_cycle_scheduled',
              'strategy_scan_completed', 'market_snapshot_cached', 'market_quote_observed',
              'strategy_cycle_woken', 'strategy_evidence_ready_committed', 'strategy_evidence_ready_enqueued',
              'strategy_ready_nats_accepted', 'OUTBOX_PUBLISH_ATTEMPT', 'OUTBOX_PUBLISHED'}
    fields = {'event', 'audit', 'runner', 'strategy_id', 'symbol', 'provider_symbol', 'cycle_elapsed_ms',
              'configured_interval_ms', 'next_cycle_delay_ms', 'elapsed_ms', 'adapter_elapsed_ms',
              'evaluation_timestamp', 'source_market_data_timestamp', 'source_data_age',
              'collector_request_started_at', 'collector_response_received_at',
              'collector_cache_write_completed_at', 'bridge_elapsed_ms', 'request_started_at',
              'observed_at', 'quote_time', 'quote_age_seconds', 'publish_ack_ms', 'event_id',
              'signal_id', 'outbox_age_ms'}
    rows = []
    for line in text.splitlines():
        try:
            stamp = datetime.datetime.fromisoformat(line.split(' ', 1)[0].replace('Z', '+00:00'))
            if not since <= stamp <= until:
                continue
            obj = json.loads(line[line.index('{'):])
            if obj.get('event') not in events or obj.get('provider_symbol', obj.get('symbol', provider)) != provider:
                continue
            rows.append({'log_timestamp': stamp.isoformat(), **{k: v for k, v in obj.items() if k in fields}})
            if len(rows) >= 150:
                break
        except (ValueError, TypeError, AttributeError):
            continue
    return rows


def report(kubeconfig, context='rancher', signal_id=None):
    if signal_id is not None and not re.fullmatch(r'SIG_[a-zA-Z0-9_-]{1,64}', signal_id):
        raise ValueError('invalid signal ID')
    base = ['kubectl', '--kubeconfig', kubeconfig, '--context', context, '-n', 'trading', '--request-timeout=15s']
    result = {'schema': 'strategy-event-preflight.v1', 'read_only': True, 'broker_writes': 0,
              'observed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'context': context, 'namespace': 'trading', 'target_source': SOURCE, 'target_image': IMAGE,
              'kubeconfig_exists': Path(kubeconfig).is_file(), 'errors': [], 'secrets': {}, 'deployments': [], 'pods': [],
              'unverified': ['scoped NATS allow/deny ACL audit', 'dedicated DB role privilege audit',
                             'frozen observer artifact/checkpoint cutover bindings', 'post-T0 end-to-end latency trace']}

    def command(args, label, *, raw=False):
        try:
            p = subprocess.run(base+args, capture_output=True, text=True, timeout=25)
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            result['errors'].append({'operation': label, 'reason': type(exc).__name__})
            return None
        if p.returncode:
            result['errors'].append({'operation': label, 'reason': failure_reason(p.stderr), 'exit_code': p.returncode})
            return None
        if raw:
            return p.stdout
        try:
            return json.loads(p.stdout)
        except (ValueError, TypeError):
            result['errors'].append({'operation': label, 'reason': 'INVALID_JSON_REDACTED'})
            return None

    api = command(['get', '--raw=/version'], 'API version')
    result['api_reachable'] = api is not None
    if api is None:
        result['status'] = 'BLOCKED_CLUSTER_ACCESS'
        return result
    for name, keys in SECRETS.items():
        obj = command(['get', 'secret', name, '-o', 'json'], 'Secret key metadata: '+name)
        result['secrets'][name] = {'accessible': obj is not None,
                                 'missing_keys': sorted(set(keys)-set(obj.get('data', {}))) if obj else None}
        del obj  # Values are never included in the report or error text.
    ds = command(['get', 'deployments', '-o', 'json'], 'deployment metadata')
    for d in (ds or {}).get('items', []):
        if not relevant(d['metadata']['name']):
            continue
        spec = d['spec']['template']['spec']
        containers = []
        for c in spec.get('containers', []):
            containers.append({'name': c['name'], 'image': c['image'],
                'target_image': c['image'] == IMAGE,
                'env': {e['name']: e['value'] for e in c.get('env', []) if e['name'] in SAFE_ENV and 'value' in e},
                'secret_refs': [e['secretRef']['name'] for e in c.get('envFrom', []) if 'secretRef' in e],
                'configmap_refs': [v['configMap']['name'] for v in spec.get('volumes', []) if 'configMap' in v]})
        result['deployments'].append({'name': d['metadata']['name'], 'replicas': d['spec'].get('replicas', 1),
                                     'ready': d.get('status', {}).get('readyReplicas', 0), 'containers': containers})
    pods = command(['get', 'pods', '-o', 'json'], 'pod metadata')
    for p in (pods or {}).get('items', []):
        if not relevant(p['metadata']['name']):
            continue
        statuses = p.get('status', {}).get('containerStatuses', [])+p.get('status', {}).get('initContainerStatuses', [])
        result['pods'].append({'name': p['metadata']['name'], 'phase': p.get('status', {}).get('phase'),
            'containers': [{'name': c['name'], 'ready': c.get('ready'), 'restarts': c.get('restartCount'),
                            'image_id': c.get('imageID'),
                            'waiting_reason': c.get('state', {}).get('waiting', {}).get('reason')}
                           for c in statuses]})
        if p.get('status', {}).get('phase') == 'Running':
            result['pods'][-1]['runtime_env'] = {}
            for c in p['spec'].get('containers', []):
                if not any(s.get('ready') and s['name'] == c['name'] for s in statuses):
                    continue
                result['pods'][-1]['runtime_env'][c['name']] = command(
                    ['exec', p['metadata']['name'], '-c', c['name'], '--', 'python', '-c', RUNTIME_SCRIPT],
                    'non-secret runtime flags: '+p['metadata']['name']+'/'+c['name'])
    dbs = [p for p in (pods or {}).get('items', [])
           if p['metadata'].get('labels', {}).get('app.kubernetes.io/name') == 'trading-postgres'
           and p.get('status', {}).get('phase') == 'Running']
    if len(dbs) == 1:
        p = dbs[0]
        result['database'] = command(['exec', p['metadata']['name'], '-c', p['spec']['containers'][0]['name'],
                                      '--', 'sh', '-c', DATABASE_SCRIPT.replace('__SIGNAL_ID__', signal_id or '')],
                                     'read-only database audit')
    else:
        result['errors'].append({'operation': 'database discovery', 'reason': 'UNIQUE_RUNNING_PRIMARY_POD_NOT_FOUND'})
    signal = (result.get('database') or {}).get('signal') or {}
    if signal.get('decision_time') and signal.get('provider_symbol'):
        decision = datetime.datetime.fromisoformat(signal['decision_time'].replace('Z', '+00:00'))
        since, until = decision-datetime.timedelta(seconds=60), decision+datetime.timedelta(seconds=120)
        result['signal_log_window'] = {'since': since.isoformat(), 'until': until.isoformat(),
                                       'provider_symbol': signal['provider_symbol'], 'logs': []}
        for pod in (pods or {}).get('items', []):
            if not pod['metadata']['name'].startswith(('mt5-native-bridge-main-runtime-', 'market-data-collector-', 'signal-outbox-relay-')):
                continue
            for container in pod['spec'].get('containers', []):
                if container['name'] not in {'collector', 'relay', 'context-paper', 'context-v2-research', 'orchestrator-shadow'}:
                    continue
                label = pod['metadata']['name']+'/'+container['name']
                raw = command(['logs', pod['metadata']['name'], '-c', container['name'],
                               '--timestamps=true', '--since-time='+since.isoformat(), '--tail=2000'],
                              'filtered timing logs: '+label, raw=True)
                rows = diagnostic_rows(raw or '', signal['provider_symbol'], since, until)
                result['signal_log_window']['logs'].append({'container': label, 'events': rows,
                    'evidence': 'OBSERVED' if rows else 'NO_RETAINED_MATCHING_EVENTS'})
    relays = [p for p in (pods or {}).get('items', []) if p['metadata']['name'].startswith('signal-outbox-relay-')
              and any(c.get('ready') for c in p.get('status', {}).get('containerStatuses', []))]
    if len(relays) == 1:
        p = relays[0]
        result['nats_stream'] = command(['exec', p['metadata']['name'], '-c', p['spec']['containers'][0]['name'],
                                         '--', 'python', '-c', NATS_SCRIPT], 'read-only NATS stream INFO')
    db = result.get('database') or {}
    applied = {m['version']: m for m in db.get('migrations') or []}
    result['migration_checks'] = {v: {'matches': applied.get(v, {}).get('filename') == f
                                                 and applied.get(v, {}).get('checksum_sha256') == digest,
                                    'expected_filename': f, 'expected_checksum': digest}
                                  for v, (f, digest) in MIGRATIONS.items()}
    result['status'] = 'BLOCKED_PENDING_CREDENTIAL_BINDING_AND_ACTIVATION_REVIEW'
    # Key presence, a Ready pod or stream INFO is never sufficient to approve activation.
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kubeconfig', required=True)
    parser.add_argument('--context', default='rancher')
    parser.add_argument('--output', required=True)
    parser.add_argument('--signal-id')
    args = parser.parse_args()
    try:
        result = report(args.kubeconfig, args.context, args.signal_id)
    except Exception as exc:
        result = {'status': 'BLOCKED', 'error_type': type(exc).__name__, 'read_only': True, 'broker_writes': 0}
    Path(args.output).write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({'status': result['status'], 'report': str(Path(args.output).resolve()), 'read_only': True}))


if __name__ == '__main__':
    main()
