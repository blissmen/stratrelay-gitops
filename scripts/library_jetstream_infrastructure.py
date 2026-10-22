"""Read-only prerequisite inventory. No raw config, credentials or errors emitted."""
import argparse
from datetime import datetime, timezone
import json
import re
import subprocess

SQL = """SELECT json_build_object(
 'database', current_database(), 'primary', NOT pg_is_in_recovery(),
 'role', current_user,
 'role_attributes', (SELECT json_build_object('superuser',rolsuper,'create_role',rolcreaterole)
                    FROM pg_roles WHERE rolname=current_user),
 'event_tables', (SELECT json_object_agg(t,to_regclass('research.'||t) IS NOT NULL)
    FROM unnest(ARRAY['market_event_snapshot','market_event_head','library_stream_outbox',
                     'library_stream_cursor','library_stream_inbox']) t),
 'owners', (SELECT json_agg(json_build_object('schema',schemaname,'table',tablename,'owner',tableowner))
    FROM pg_tables WHERE (schemaname='research' AND (tablename LIKE 'library_stream_%'
        OR tablename LIKE 'market_event_%' OR tablename LIKE 'strategy_library_forward_%'))),
 'original_instances', (SELECT json_agg(json_build_object('id',id,'online',online,
       'execution_mode',execution_mode,'execution_eligible',execution_eligible,
       'run_id',attributes->>'orchestrator_run_id','publication_transport',attributes->>'publication_transport'))
    FROM strategy_mgmt.strategy_instance_v2 WHERE id IN (
       'fea3769c-c3f2-52f5-8ae2-f0be9d12abeb'::uuid,
       '1c6d1d50-8850-5b2a-b500-d9df0c51fe78'::uuid,
       'f8dcf44a-9b37-5f97-b766-318f71e9848f'::uuid))
);"""


def diagnostic(stderr):
    text=stderr.lower()
    for needle,reason in [('no password supplied','POSTGRES_PASSWORD_REQUIRED'),
        ('password authentication failed','POSTGRES_AUTHENTICATION_FAILED'),
        ('peer authentication failed','POSTGRES_PEER_AUTHENTICATION_FAILED'),
        ('database "','POSTGRES_DATABASE_ERROR'),('role "','POSTGRES_ROLE_ERROR'),
        ('permission denied','POSTGRES_PERMISSION_DENIED'),('forbidden','KUBERNETES_RBAC_FORBIDDEN'),
        ('connection refused','CONNECTION_REFUSED'),('no such file or directory','SOCKET_OR_BINARY_NOT_FOUND'),
        ('could not translate host name','DNS_FAILED')]:
        if needle in text:return reason
    return 'COMMAND_FAILED_REDACTED'


def inventory(kubeconfig,context):
    base=['kubectl','--kubeconfig',kubeconfig,'--context',context,'-n','trading']
    result={'observed_at':datetime.now(timezone.utc).isoformat(),'context':context,
            'namespace':'trading','read_only':True,'broker_writes':0,'errors':[]}
    def run(args,label,timeout=30):
        try:
            p=subprocess.run(base+args,capture_output=True,text=True,timeout=timeout)
        except (FileNotFoundError,subprocess.TimeoutExpired) as exc:
            result['errors'].append({'operation':label,'reason':type(exc).__name__})
            return None
        if p.returncode:
            result['errors'].append({'operation':label,'exit_code':p.returncode,
                                     'reason':diagnostic(p.stderr)})
            return None
        return p.stdout
    data=run(['get','pods','-o','json'],'pod inventory')
    pods=json.loads(data)['items'] if data else []
    def target(name):
        return [p for p in pods if p['metadata'].get('labels',{}).get('app.kubernetes.io/name')==name
                and p.get('status',{}).get('phase')=='Running' and not p['metadata'].get('deletionTimestamp')]
    pg=target('trading-postgres')
    if len(pg)==1:
        name=pg[0]['metadata']['name']
        containers=pg[0]['spec']['containers']
        if len(containers)==1:
            c=containers[0]['name']
            # Match only the existing POSTGRES_USER/POSTGRES_PASSWORD pair. Never
            # substitute a password from another application/role or copy it out.
            shell=('if [ -n "${POSTGRES_USER:-}" ] && [ -n "${POSTGRES_PASSWORD:-}" ]; then '
                   'PGUSER="$POSTGRES_USER"; PGPASSWORD="$POSTGRES_PASSWORD"; export PGUSER PGPASSWORD; '
                   'elif [ -z "${PGUSER:-}" ]; then PGUSER=postgres; export PGUSER; fi; '
                   'exec env PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=10000" '
                   'psql -X -qAt -w -v ON_ERROR_STOP=1 -d trading -c "$1"')
            result['postgres_pod']={'name':name,'container':c,
                'environment_names':sorted(e['name'] for e in containers[0].get('env',[])),
                'credential_secret_refs':sorted({e['valueFrom']['secretKeyRef']['name']
                    for e in containers[0].get('env',[]) if 'secretKeyRef' in e.get('valueFrom',{})})}
            value=run(['exec',name,'-c',c,'--','sh','-c',shell,'--',SQL],
                      'read-only PostgreSQL prerequisites')
            if value:result['postgres']=json.loads(value)
        else:result['errors'].append({'operation':'PostgreSQL selection','reason':'SELECT_CONTAINER_EXPLICITLY'})
    else:result['errors'].append({'operation':'PostgreSQL selection','reason':'EXPECTED_ONE_RUNNING_PRIMARY_POD'})
    nats=target('trading-nats')
    result['nats_pods']=[]
    for pod in nats:
        spec=pod['spec']
        item={'name':pod['metadata']['name'],
              'owners':[{'kind':o['kind'],'name':o['name']} for o in pod['metadata'].get('ownerReferences',[])],
              'containers':[{'name':c['name'],'image':c['image'],
                  'env_secret_refs':sorted({e['valueFrom']['secretKeyRef']['name'] for e in c.get('env',[])
                    if 'secretKeyRef' in e.get('valueFrom',{})}),
                  'env_from_secret_refs':[e['secretRef']['name'] for e in c.get('envFrom',[]) if 'secretRef' in e]}
                  for c in spec['containers']],
              'volume_sources':[{'name':v['name'],'config_map':v.get('configMap',{}).get('name'),
                                 'secret':v.get('secret',{}).get('secretName')}
                                for v in spec.get('volumes',[]) if 'secret' in v or 'configMap' in v]}
        server=next((c for c in spec['containers'] if 'nats' in c['image'].lower()),None)
        if server:
            tokens=server.get('command',[])+server.get('args',[])
            # Boolean flags only. Arguments can contain passwords/tokens.
            item['configuration_flags']={
                'config_flag':any(x in ['-c','--config'] or x.startswith('--config=') for x in tokens),
                'user_flag':any(x=='--user' or x.startswith('--user=') for x in tokens),
                'password_flag':any(x=='--pass' or x.startswith('--pass=') for x in tokens),
                'token_flag':any(x=='--auth' or x.startswith('--auth=') for x in tokens)}
            version=run(['exec',pod['metadata']['name'],'-c',server['name'],'--','nats-server','--version'],
                        'NATS server version')
            match=re.search(r'v?(\d+\.\d+\.\d+)',version or '')
            item['server_version']=match.group(1) if match else None
        result['nats_pods'].append(item)
    result['status']='INVENTORIED_NOT_ACTIVATED' if result.get('postgres') and nats else 'INCOMPLETE'
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kubeconfig',required=True);parser.add_argument('--context',default='rancher')
    args=parser.parse_args()
    try:print(json.dumps(inventory(args.kubeconfig,args.context),indent=2))
    except Exception as exc:
        print(json.dumps({'status':'BLOCKED','error_type':type(exc).__name__,'read_only':True,'broker_writes':0}))
        raise SystemExit(1)
