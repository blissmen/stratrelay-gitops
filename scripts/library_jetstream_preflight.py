"""Read-only Kubernetes evidence. Never output Secret values or pod env values."""
import argparse
import datetime
import json
import subprocess

SECRETS={
    'library-event-collector':['LIBRARY_EVENT_DSN'],
    **{'library-event-'+r:['LIBRARY_EVENT_DSN','NATS_URL','P2_NATS_USER','P2_NATS_PASSWORD']
       for r in ['relay','observer','orchestrator']},
    'library-event-provision-streams':['NATS_URL','P2_NATS_USER','P2_NATS_PASSWORD'],
    'library-event-activate':['LIBRARY_EVENT_DSN'],
    'trading-signal-cutoff':['SIGNAL_CUTOFF_ID','SIGNAL_CUTOFF_UTC']}


def report(kubeconfig):
    base=['kubectl','--kubeconfig',kubeconfig,'--context','local','-n','trading']
    def get(kind,name=None):
        p=subprocess.run(base+['get',kind]+([name] if name else [])+['-o','json'],
            capture_output=True,text=True,timeout=30)
        if p.returncode:return None
        return json.loads(p.stdout)
    output={'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'context':'local','namespace':'trading','read_only':True,'secrets':{},'deployments':[]}
    for name,keys in SECRETS.items():
        secret=get('secret',name)
        present=set((secret or {}).get('data',{}))
        output['secrets'][name]={'accessible':secret is not None,
                                'missing_keys':sorted(set(keys)-present)}
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
    p=subprocess.run(base[:-2]+['-n','default','get','service','kubernetes','-o','json'],
        capture_output=True,text=True,timeout=30)
    if p.returncode==0:
        s=json.loads(p.stdout)
        output['apiserver_service']={'cluster_ip':s['spec'].get('clusterIP'),'ports':s['spec'].get('ports')}
    p=subprocess.run(base[:-2]+['-n','default','get','endpoints','kubernetes','-o','json'],
        capture_output=True,text=True,timeout=30)
    if p.returncode==0:
        output['apiserver_backend_subsets']=json.loads(p.stdout).get('subsets',[])
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
    a=p.parse_args()
    try:print(json.dumps(report(a.kubeconfig),indent=2))
    except Exception as exc:
        print(json.dumps({'status':'BLOCKED','error_type':type(exc).__name__,'read_only':True,'broker_writes':0}))
        raise SystemExit(1)
