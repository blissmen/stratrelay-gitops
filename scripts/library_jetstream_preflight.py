"""Read-only Kubernetes evidence. Never output Secret values or pod env values."""
import argparse
import datetime
import json
import subprocess
from pathlib import Path

SECRETS={
    'library-event-collector':['LIBRARY_EVENT_DSN'],
    **{'library-event-'+r:['LIBRARY_EVENT_DSN','NATS_URL','P2_NATS_USER','P2_NATS_PASSWORD']
       for r in ['relay','observer','orchestrator']},
    'library-event-provision-streams':['NATS_URL','P2_NATS_USER','P2_NATS_PASSWORD'],
    'library-event-activate':['LIBRARY_EVENT_DSN'],
    'trading-signal-cutoff':['SIGNAL_CUTOFF_ID','SIGNAL_CUTOFF_UTC']}


def failure_reason(stderr):
    text=stderr.lower()
    # Classification only: raw CLI errors may contain credential-helper output.
    for needles,code in [
        (['context was not found','context does not exist','no context exists'],'CONTEXT_NOT_FOUND'),
        (['no such file or directory'],'CONFIG_OR_HELPER_NOT_FOUND'),
        (['unauthorized','must be logged in','provide credentials'],'AUTHENTICATION_FAILED'),
        (['forbidden'],'RBAC_FORBIDDEN'),
        (['certificate','x509:'],'TLS_VERIFICATION_FAILED'),
        (['connection refused'],'CONNECTION_REFUSED'),
        (['timeout','timed out','deadline exceeded'],'CONNECTION_TIMEOUT'),
        (['no such host','name resolution'],'DNS_FAILED'),
        (['notfound','not found'],'RESOURCE_NOT_FOUND')]:
        if any(needle in text for needle in needles):return code
    return 'KUBECTL_FAILED_REDACTED'


def report(kubeconfig,context='local'):
    base=['kubectl','--kubeconfig',kubeconfig,'--context',context,'-n','trading']
    output={'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'context':context,'namespace':'trading','read_only':True,'secrets':{},'deployments':[],
            'kubeconfig_exists':Path(kubeconfig).is_file(),'errors':[]}
    def run(command,label):
        try:p=subprocess.run(command,capture_output=True,text=True,timeout=30)
        except subprocess.TimeoutExpired:
            output['errors'].append({'operation':label,'reason':'COMMAND_TIMEOUT'})
            return None
        except FileNotFoundError:
            output['errors'].append({'operation':label,'reason':'KUBECTL_NOT_FOUND'})
            return None
        if p.returncode:
            output['errors'].append({'operation':label,'exit_code':p.returncode,'reason':failure_reason(p.stderr)})
            return None
        return p.stdout
    def get(kind,name=None):
        data=run(base+['get',kind]+([name] if name else [])+['-o','json'],
                 'get '+kind+(' '+name if name else ''))
        return json.loads(data) if data is not None else None
    contexts=run(base[:3]+['config','get-contexts','-o','name'],'list context names')
    output['available_contexts']=contexts.splitlines() if contexts else []
    version=run(base[:-2]+['get','--raw=/version'],'API version')
    output['api_reachable']=version is not None
    if version:
        output['kubernetes_version']=json.loads(version).get('gitVersion')
    for name,keys in SECRETS.items():
        secret=get('secret',name)
        present=set((secret or {}).get('data',{}))
        output['secrets'][name]={'accessible':secret is not None,
                                'missing_keys':sorted(set(keys)-present) if secret is not None else None}
        del secret
    ds=get('deployments')
    output['cluster_read_succeeded']=ds is not None
    for d in (ds or {}).get('items',[]):
        name=d['metadata']['name']
        if name not in ['market-data-collector','mt5-native-bridge-main-runtime',
                        'strategy-library-canary-v1-observer'] and not name.startswith('library-event-'):
            continue
        output['deployments'].append({'name':name,'desired':d['spec'].get('replicas'),
            'ready':d.get('status',{}).get('readyReplicas',0),
            'images':{c['name']:c['image'] for c in d['spec']['template']['spec']['containers']}})
    result=run(base[:-2]+['-n','default','get','service','kubernetes','-o','json'],'get API service')
    if result is not None:
        s=json.loads(result)
        output['apiserver_service']={'cluster_ip':s['spec'].get('clusterIP'),'ports':s['spec'].get('ports')}
    result=run(base[:-2]+['-n','default','get','endpoints','kubernetes','-o','json'],'get API endpoints')
    if result is not None:
        output['apiserver_backend_subsets']=json.loads(result).get('subsets',[])
    jobs=get('jobs')
    output['jobs']=[{'name':j['metadata']['name'],'succeeded':j.get('status',{}).get('succeeded',0),
                    'failed':j.get('status',{}).get('failed',0)}
                   for j in (jobs or {}).get('items',[]) if j['metadata']['name'].startswith('library-event-')]
    output['credential_contract_present']=output['cluster_read_succeeded'] and all(
        x['accessible'] and not x['missing_keys'] for x in output['secrets'].values())
    output['status']='REQUIRES_DB_ACL_AND_NATS_ACL_VERIFICATION' if output['credential_contract_present'] else 'BLOCKED'
    output['broker_writes']=0
    return output


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kubeconfig',required=True)
    p.add_argument('--context',default='local')
    a=p.parse_args()
    try:print(json.dumps(report(a.kubeconfig,a.context),indent=2))
    except Exception as exc:
        print(json.dumps({'status':'BLOCKED','error_type':type(exc).__name__,'read_only':True,'broker_writes':0}))
        raise SystemExit(1)
