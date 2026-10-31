"""Secret-first library-event credentials. No workload activation or password rotation.

Run only after reviewed migrations are present. Errors and subprocess output are
redacted; credentials travel through stdin, never command arguments or reports.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import secrets
import subprocess
from urllib.parse import quote, unquote, urlsplit

from strategy_event_relay_bootstrap import ADMIN_COMMAND, DATABASE, HOST, NAMESPACE_UID, validate_dsn

OWNER='stratrelay.app/library-event-bootstrap'
COMPONENTS=('collector','relay','observer','orchestrator')
COMMON={'research.library_stream_outbox': {'SELECT'}}
CATALOG={t:{'SELECT'} for t in (
    'strategy_mgmt.strategy_instance_v2','strategy_mgmt.parameter_set','strategy_mgmt.strategy_version',
    'platform.strategy_definition','strategy.instrument_membership')}
PROFILES={
 'collector':{**COMMON,'research.market_event_head':{'SELECT','INSERT','UPDATE'},
              'research.market_event_snapshot':{'SELECT','INSERT'},
              'research.library_stream_outbox':{'SELECT','INSERT'}},
 'relay':{**COMMON},
 'observer':{**COMMON,**CATALOG,'research.market_event_snapshot':{'SELECT'},
             'research.strategy_library_forward_state':{'SELECT','INSERT','UPDATE'},
             'research.strategy_library_forward_event':{'SELECT','INSERT'},
             'research.library_stream_cursor':{'SELECT','INSERT','UPDATE'},
             'research.library_stream_inbox':{'SELECT','INSERT'},
             'research.library_stream_outbox':{'SELECT','INSERT'}},
 'orchestrator':{**COMMON,**CATALOG,'research.strategy_library_forward_state':{'SELECT'},
                 'research.strategy_library_forward_event':{'SELECT'},
                 'research.library_stream_inbox':{'SELECT','INSERT'},
                 'platform.schema_migrations':{'SELECT'},
                 **{t:{'SELECT','INSERT'} for t in (
                    'strategy.candidates','strategy.evaluations','strategy.decision_traces',
                    'strategy.evaluation_reason_codes','strategy.stage_results','platform.reason_codes',
                    'strategy.entry_signals','strategy.signals','strategy.entry_signal_mechanisms',
                    'platform.outbox_events')}}}


def role_name(component):
    if component not in COMPONENTS:raise ValueError('UNKNOWN_COMPONENT')
    return 'library_event_'+component


def role_sql(component,password):
    role=role_name(component)
    if not re.fullmatch(r'[a-zA-Z0-9_-]{32,128}',password):raise ValueError('INVALID_PASSWORD')
    lines=['BEGIN;',f"CREATE ROLE {role} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '{password}';",
           f'GRANT CONNECT ON DATABASE {DATABASE} TO {role};']
    schemas=sorted({t.split('.')[0] for t in PROFILES[component]})
    lines.append(f"GRANT USAGE ON SCHEMA {','.join(schemas)} TO {role};")
    for table,actions in sorted(PROFILES[component].items()):
        lines.append(f"GRANT {','.join(sorted(actions))} ON {table} TO {role};")
    if component=='relay':
        lines.append(f'GRANT UPDATE (published_at) ON research.library_stream_outbox TO {role};')
    if component=='orchestrator':
        lines.append(f'GRANT EXECUTE ON FUNCTION research.lock_library_publication(uuid,text), research.lock_library_signal(text) TO {role};')
    return '\n'.join(lines+['COMMIT;'])


# This probe audits effective privileges, including inherited/PUBLIC grants,
# against the complete allowlist; it rejects unexpected table/column writes,
# schema creation, role memberships or elevated attributes before declaring ready.
LOGIN_PROBE=r'''import json,sys
import psycopg
try:
    r=json.load(sys.stdin); expected={t:set(a) for t,a in r['tables'].items()}
    with psycopg.connect(r['dsn'],connect_timeout=5,options='-c default_transaction_read_only=on -c statement_timeout=10000') as c:
        q=c.cursor();q.execute('SELECT current_database(),current_user,pg_is_in_recovery()');identity=q.fetchone()
        q.execute("SELECT rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls OR rolinherit FROM pg_roles WHERE rolname=current_user")
        elevated=q.fetchone()[0]
        q.execute('SELECT EXISTS(SELECT 1 FROM pg_auth_members WHERE member=current_user::regrole)');memberships=q.fetchone()[0]
        q.execute("SELECT n.nspname,c.relname,p FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']) p WHERE c.relkind IN ('r','p','v','m','S','f') AND n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' AND c.relkind<>'S' AND has_table_privilege(current_user,c.oid,p)")
        actual={}
        for s,t,a in q.fetchall():actual.setdefault(s+'.'+t,set()).add(a)
        q.execute("SELECT nspname FROM pg_namespace WHERE nspname NOT IN ('pg_catalog','information_schema','public') AND nspname NOT LIKE 'pg_%' AND has_schema_privilege(current_user,oid,'CREATE')")
        creates=q.fetchall()
        q.execute("SELECT table_schema||'.'||table_name,column_name,privilege_type FROM information_schema.column_privileges WHERE grantee=current_user AND privilege_type IN ('INSERT','UPDATE')")
        columns=q.fetchall()
        column_ok=all(a in expected.get(t,set()) or (r['component']=='relay' and (t,col,a)==('research.library_stream_outbox','published_at','UPDATE')) for t,col,a in columns)
        functions=True
        if r['component']=='orchestrator':
            q.execute("SELECT has_function_privilege(current_user,'research.lock_library_publication(uuid,text)','EXECUTE') AND has_function_privilege(current_user,'research.lock_library_signal(text)','EXECUTE')")
            functions=q.fetchone()[0]
        ok=identity==('trading_platform',r['role'],False) and not elevated and not memberships and not creates and actual==expected and column_ok and functions
    print(json.dumps({'verified':bool(ok),'identity_verified':identity==('trading_platform',r['role'],False),'effective_table_allowlist_matches':actual==expected,'column_allowlist_matches':column_ok,'no_elevation':not elevated and not memberships and not creates,'locking_capabilities':functions}))
except Exception as e:print(json.dumps({'verified':False,'error_type':type(e).__name__}))
'''


def bootstrap(kubeconfig,context,source,apply=False):
    base=['kubectl','--kubeconfig',kubeconfig,'--context',context,'-n','trading','--request-timeout=20s']
    result={'status':'BLOCKED','broker_writes':0,'workloads_changed':False,'components':{}}
    def call(args,payload=None,missing=False):
        p=subprocess.run(base+args,input=payload,text=True,capture_output=True,timeout=40)
        if p.returncode:
            if missing and 'notfound' in p.stderr.lower():return None
            raise RuntimeError('KUBERNETES_OPERATION_FAILED_REDACTED')
        return p.stdout
    def get(kind,name,missing=False):
        raw=call(['get',kind,name,'-o','json'],missing=missing)
        return json.loads(raw) if raw is not None else None
    try:
        if get('namespace','trading')['metadata']['uid']!=NAMESPACE_UID:raise ValueError('CLUSTER_MISMATCH')
        pg=['exec','-i','trading-postgres-0','--','sh','-c',ADMIN_COMMAND]
        checks=[]
        for version in ('070','078','083'):
            paths=list((Path(source)/'postgres/migrations').glob(version+'_*.sql'))
            if len(paths)!=1:raise ValueError('MIGRATION_SOURCE_REQUIRED')
            checks.append((version,paths[0].name,hashlib.sha256(paths[0].read_bytes()).hexdigest()))
        rows=json.loads(call(pg,"SELECT json_build_object('database',current_database(),'primary',NOT pg_is_in_recovery(),'migrations',(SELECT json_agg(json_build_array(version,filename,checksum_sha256)) FROM platform.schema_migrations WHERE version IN ('070','078','083')));"))
        if rows['database']!=DATABASE or not rows['primary'] or sorted(map(tuple,rows['migrations'] or []))!=checks:
            raise ValueError('PRIMARY_OR_MIGRATION_GATE_FAILED')
        result['migration_checksums_verified']=True
        nats=get('secret','trading-nats-auth')
        nats_data={k:base64.b64decode(nats['data'][v],validate=True).decode() for k,v in [('P2_NATS_USER','NATS_USER'),('P2_NATS_PASSWORD','NATS_PASSWORD')]}
        nats_data['NATS_URL']='nats://trading-nats.trading.svc.cluster.local:4222'
        for component in COMPONENTS:
            role=role_name(component);name='library-event-'+component
            item=result['components'][component]={'role':role,'secret':name,'role_created':False,'secret_created':False}
            exists=json.loads(call(pg,"SELECT to_json(EXISTS(SELECT 1 FROM pg_roles WHERE rolname='"+role+"'));"))
            secret=get('secret',name,True)
            if exists and secret is None:raise ValueError('UNPAIRED_ROLE_REQUIRES_REVIEW')
            expected_keys={'LIBRARY_EVENT_DSN'} | (set(nats_data) if component!='collector' else set())
            if secret:
                if set(secret.get('data',{}))!=expected_keys:raise ValueError('SECRET_KEYS_CONFLICT')
                data={k:base64.b64decode(v,validate=True).decode() for k,v in secret['data'].items()}
                dsn=data['LIBRARY_EVENT_DSN'];validate_dsn(dsn,role)
                if component!='collector' and any(data[k]!=v for k,v in nats_data.items()):raise ValueError('NATS_BINDING_CONFLICT')
                if not exists and secret['metadata'].get('annotations',{}).get(OWNER)!='v1':raise ValueError('UNOWNED_PARTIAL_SECRET')
            elif apply:
                password=secrets.token_urlsafe(48)
                dsn=f'postgresql://{role}:{quote(password,safe="")}@{HOST}:5432/{DATABASE}?connect_timeout=5'
                data={'LIBRARY_EVENT_DSN':dsn,**(nats_data if component!='collector' else {})}
                obj={'apiVersion':'v1','kind':'Secret','type':'Opaque','metadata':{'name':name,'namespace':'trading','annotations':{OWNER:'v1'}},'data':{k:base64.b64encode(v.encode()).decode() for k,v in data.items()}}
                call(['create','-f','-','-o','name'],json.dumps(obj));item['secret_created']=True
            else:
                item['status']='PROVISIONING_REQUIRED';continue
            if not exists:
                if not apply:item['status']='ROLE_CREATION_REQUIRED';continue
                call(pg,role_sql(component,unquote(urlsplit(dsn).password)));item['role_created']=True
            probe=json.loads(call(['exec','-i','deploy/market-data-collector','-c','collector','--','python','-c',LOGIN_PROBE],
                json.dumps({'dsn':dsn,'component':component,'role':role,'tables':{t:sorted(a) for t,a in PROFILES[component].items()}})))
            item['login_audit']=probe
            if not probe.get('verified'):raise ValueError('LOGIN_OR_PRIVILEGE_GATE_FAILED')
            item['status']='VERIFIED'
        result['status']='CREDENTIALS_VERIFIED' if all(x.get('status')=='VERIFIED' for x in result['components'].values()) else 'PLAN_ONLY'
    except Exception as exc:
        result['error_type']=type(exc).__name__
        if isinstance(exc,ValueError) and re.fullmatch('[A-Z_]+',str(exc)):result['reason']=str(exc)
        result['recovery']='Retain owned Secrets; retry reuses passwords. No automatic role adoption, grants repair or rotation.'
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kubeconfig',required=True);p.add_argument('--context',default='local')
    p.add_argument('--platform-source',required=True);p.add_argument('--apply',action='store_true')
    args=p.parse_args()
    result=bootstrap(args.kubeconfig,args.context,args.platform_source,args.apply)
    print(json.dumps(result,indent=2))
    raise SystemExit(0 if result['status'] in ('CREDENTIALS_VERIFIED','PLAN_ONLY') else 1)
