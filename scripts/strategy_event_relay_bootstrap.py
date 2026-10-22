"""Provision a scheduling relay/collector DB role and Secret; never activate workloads."""
import argparse
import base64
import json
from pathlib import Path
import secrets
import subprocess
from urllib.parse import quote, urlsplit, parse_qs, unquote

from strategy_event_preflight import migration_checks

NAMESPACE_UID = '6245760c-f16e-41bf-a820-f0a465a90d2e'
ROLE = 'strategy_event_relay'
SECRET = 'strategy-event-relay'
HOST = 'trading-postgres.trading.svc.cluster.local'
DATABASE = 'trading_platform'
PROFILES = {
    'relay': {'role': ROLE, 'secret': SECRET, 'label': 'signal-outbox-relay',
              'container': 'relay', 'annotation': 'stratrelay.app/relay-bootstrap'},
    'collector': {'role': 'strategy_event_collector', 'secret': 'strategy-event-collector-db',
                  'label': 'market-data-collector', 'container': 'collector',
                  'annotation': 'stratrelay.app/collector-bootstrap'},
}
ADMIN_COMMAND = '''set +x
test -n "${POSTGRES_USER:-}" && test -n "${POSTGRES_PASSWORD:-}" || exit 4
export PGPASSWORD="$POSTGRES_PASSWORD" PGCONNECT_TIMEOUT=5
export PGOPTIONS='-c statement_timeout=5000 -c lock_timeout=2000'
exec psql -X -qAt -w -h 127.0.0.1 -p 5432 -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d trading_platform'''
INSPECT_SQL = """BEGIN READ ONLY;
SELECT json_build_object('database',current_database(),'primary',NOT pg_is_in_recovery(),
 'migrations',COALESCE((SELECT json_agg(json_build_object('version',version,'filename',filename,
 'checksum_sha256',checksum_sha256)) FROM platform.schema_migrations
 WHERE version IN ('072','073','074')),'[]'::json),
 'role_exists',EXISTS(SELECT 1 FROM pg_roles WHERE rolname='strategy_event_relay'),
 'outbox_exists',to_regclass('platform.strategy_runtime_outbox') IS NOT NULL);
COMMIT;"""
LOGIN_PROBE = r'''import json,sys
import psycopg
try:
    request=json.load(sys.stdin)
    dsn=request['dsn']
    component=request.get('component','relay')
    if component not in ('relay','collector'):
        raise ValueError('unknown component')
    with psycopg.connect(dsn,connect_timeout=5,options='-c default_transaction_read_only=on -c statement_timeout=5000') as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(),current_user,pg_is_in_recovery(),"
                "has_table_privilege(current_user,'platform.strategy_runtime_outbox','SELECT'),"
                "has_column_privilege(current_user,'platform.strategy_runtime_outbox','published_at','UPDATE'),"
                "has_table_privilege(current_user,'platform.strategy_runtime_outbox','DELETE'),"
                "has_table_privilege(current_user,'platform.strategy_runtime_outbox','INSERT'),"
                "(SELECT rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls FROM pg_roles WHERE rolname=current_user),"
                "EXISTS(SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=current_user)),"
                "has_column_privilege(current_user,'platform.strategy_runtime_outbox','payload','UPDATE'),"
                "has_schema_privilege(current_user,'platform','CREATE'),"
                "has_column_privilege(current_user,'platform.strategy_runtime_outbox','event_id','SELECT'),"
                "has_column_privilege(current_user,'platform.strategy_runtime_outbox','event_id','INSERT'),"
                "has_column_privilege(current_user,'platform.strategy_runtime_outbox','runtime_id','INSERT'),"
                "has_column_privilege(current_user,'platform.strategy_runtime_outbox','payload','INSERT')")
            row=cur.fetchone()
    expected={'relay':('trading_platform','strategy_event_relay',False,True,True,True,False,False,False,False,False,True,False,False,False),
              'collector':('trading_platform','strategy_event_collector',False,False,False,False,False,False,False,False,False,True,True,True,True)}
    ok=row==expected[component]
    print(json.dumps({'verified':ok}))
except Exception as exc:
    print(json.dumps({'verified':False,'error_type':type(exc).__name__}))
'''


def role_sql(password, component='relay'):
    # Only a freshly generated password enters SQL; never accept arbitrary SQL text.
    if not password or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in password):
        raise ValueError('INVALID_GENERATED_PASSWORD')
    role = PROFILES[component]['role']
    if component == 'collector':
        return """BEGIN;
CREATE ROLE strategy_event_collector LOGIN NOINHERIT NOSUPERUSER NOCREATEDB
 NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '%s';
GRANT CONNECT ON DATABASE trading_platform TO strategy_event_collector;
GRANT USAGE ON SCHEMA platform TO strategy_event_collector;
GRANT INSERT (event_id,runtime_id,payload), SELECT (event_id)
 ON platform.strategy_runtime_outbox TO strategy_event_collector;
COMMIT;
""" % password
    return """BEGIN;
CREATE ROLE strategy_event_relay LOGIN NOINHERIT NOSUPERUSER NOCREATEDB
 NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '%s';
GRANT CONNECT ON DATABASE trading_platform TO strategy_event_relay;
GRANT USAGE ON SCHEMA platform TO strategy_event_relay;
GRANT SELECT, DELETE ON platform.strategy_runtime_outbox TO strategy_event_relay;
GRANT UPDATE (published_at) ON platform.strategy_runtime_outbox TO strategy_event_relay;
COMMIT;
""" % password


def validate_dsn(dsn, role=ROLE):
    parsed = urlsplit(dsn)
    if (parsed.scheme != 'postgresql' or parsed.hostname != HOST or parsed.port != 5432
            or parsed.username != role or parsed.path != '/'+DATABASE or not parsed.password
            or parsed.fragment or parse_qs(parsed.query) != {'connect_timeout': ['5']}):
        raise ValueError('EXISTING_SECRET_BINDING_CONFLICT')


def bootstrap(kubeconfig, context, apply=False, component='relay'):
    profile = PROFILES[component]
    role, secret_name = profile['role'], profile['secret']
    base = ['kubectl', '--kubeconfig', kubeconfig, '--context', context,
            '-n', 'trading', '--request-timeout=15s']
    result = {'status': 'BLOCKED', 'component': component, 'apply_requested': apply, 'broker_writes': 0,
              'role_created': False, 'secret_created': False, 'workloads_changed': False}

    def call(args, payload=None, allow_missing=False):
        p = subprocess.run(base+args, input=payload, capture_output=True, text=True, timeout=30)
        if p.returncode:
            if allow_missing and 'notfound' in p.stderr.lower():
                return None
            raise RuntimeError('KUBECTL_OPERATION_FAILED_REDACTED')
        return p.stdout

    def get(kind, name=None, allow_missing=False):
        raw = call(['get', kind]+([name] if name else [])+['-o', 'json'], allow_missing=allow_missing)
        return json.loads(raw) if raw is not None else None

    try:
        if get('namespace', 'trading')['metadata']['uid'] != NAMESPACE_UID:
            raise ValueError('CLUSTER_IDENTITY_MISMATCH')
        pods = get('pods')['items']
        primary = [p for p in pods if p['metadata'].get('labels', {}).get('app.kubernetes.io/name') == 'trading-postgres'
                   and p.get('status', {}).get('phase') == 'Running' and not p['metadata'].get('deletionTimestamp')]
        relay = [p for p in pods if p['metadata'].get('labels', {}).get('app.kubernetes.io/name') == profile['label']
                 and not p['metadata'].get('deletionTimestamp')
                 and any(c['name'] == profile['container'] and c.get('ready') for c in p.get('status', {}).get('containerStatuses', []))]
        if len(primary) != 1 or len(relay) != 1:
            raise ValueError('UNIQUE_PRIMARY_AND_READY_RELAY_REQUIRED')
        pg = ['exec', '-i', primary[0]['metadata']['name'], '-c', primary[0]['spec']['containers'][0]['name'],
              '--', 'sh', '-c', ADMIN_COMMAND]
        evidence = json.loads(call(pg, INSPECT_SQL.replace(ROLE, role)))
        result['migration_checks'] = migration_checks(evidence)
        if (evidence.get('database') != DATABASE or evidence.get('primary') is not True
                or evidence.get('outbox_exists') is not True
                or not all(c['matches'] is True for c in result['migration_checks'].values())):
            raise ValueError('PRIMARY_OR_MIGRATION_GATE_FAILED')
        secret = get('secret', secret_name, allow_missing=True)
        if evidence['role_exists'] and not secret:
            raise ValueError('PARTIAL_PROVISIONING_REQUIRES_REVIEW_NO_AUTOMATIC_ROTATION')
        dsn = None
        if secret:
            if set(secret.get('data', {})) != {'STRATEGY_EVENT_DSN'}:
                raise ValueError('EXISTING_SECRET_KEYS_CONFLICT')
            dsn = base64.b64decode(secret['data']['STRATEGY_EVENT_DSN'], validate=True).decode()
            validate_dsn(dsn, role)
            if (not evidence['role_exists'] and
                    secret.get('metadata', {}).get('annotations', {}).get(profile['annotation']) != 'v1'):
                raise ValueError('UNOWNED_PARTIAL_BINDING_REQUIRES_REVIEW')
        elif apply:
            password = secrets.token_urlsafe(48)
            dsn = 'postgresql://'+role+':'+quote(password, safe='')+'@'+HOST+':5432/'+DATABASE+'?connect_timeout=5'
            # Persist the credential before creating its DB role, so retries never lose it.
            obj = {'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque',
                   'metadata': {'name': secret_name, 'namespace': 'trading',
                                'annotations': {profile['annotation']: 'v1'}},
                   'data': {'STRATEGY_EVENT_DSN': base64.b64encode(dsn.encode()).decode()}}
            call(['create', '-f', '-', '-o', 'name'], json.dumps(obj))
            result['secret_created'] = True
        if dsn and not evidence['role_exists']:
            if apply:
                call(pg, role_sql(unquote(urlsplit(dsn).password), component))
                result['role_created'] = True
            else:
                result['status'] = 'PLAN_ONLY_'+component.upper()+'_DB_CREATION_REQUIRED'
                return result
        if dsn:
            probe = json.loads(call(['exec', '-i', relay[0]['metadata']['name'], '-c', profile['container'], '--', 'python', '-c', LOGIN_PROBE],
                                    json.dumps({'dsn': dsn, 'component': component})))
            if probe.get('verified') is not True:
                raise ValueError('RELAY_DB_LOGIN_OR_PRIVILEGE_AUDIT_FAILED')
            result['db_login_and_privileges_verified'] = True
        result['status'] = component.upper()+'_DB_READY' if dsn else 'PLAN_ONLY_'+component.upper()+'_DB_CREATION_REQUIRED'
    except Exception as exc:
        # Never include CLI output, SQL, passwords, DSNs or exception messages.
        result['error_type'] = type(exc).__name__
        if result['secret_created']:
            result['recovery_required'] = 'Credential retained in Secret; retry reuses it and never rotates an existing role password'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kubeconfig', required=True)
    parser.add_argument('--context', default='rancher')
    parser.add_argument('--output', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--component', choices=sorted(PROFILES), default='relay')
    args = parser.parse_args()
    result = bootstrap(args.kubeconfig, args.context, args.apply, args.component)
    Path(args.output).write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({**result, 'report': str(Path(args.output).resolve())}))


if __name__ == '__main__':
    main()
