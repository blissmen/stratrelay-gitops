"""Compose a planner-produced, attested fresh Swing bundle for GitOps review.

No cluster/credential calls. Preserve the original paused Job/ConfigMap and all
historical files. The activation Job uses the established provisioning Secret,
never a copied admin DSN or a runtime event binding.
"""
import argparse
import json
from pathlib import Path
import shutil


def stage(bundle,root):
    cfg=json.loads((bundle/'run.json').read_text())
    release=json.loads((bundle/'release.json').read_text())
    run=cfg['run_id'];image=release['image']
    if cfg['symbols']!={'BTCUSD':'BTCUSDm'} or cfg['execution_eligible'] is not False:
        raise ValueError('isolated BTC research bundle required')
    dest=root/'apps/trading-platform'/run
    dest.mkdir(exist_ok=False)
    for name in ('run.json','release.json','readiness.json','quiesce.sql','activation.sql','collector.patch.json'):
        shutil.copyfile(bundle/name,dest/name)
    workload=json.loads((bundle/'workload.json').read_text())
    cm=next(x for x in workload['items'] if x['kind']=='ConfigMap')
    cm['data']['activation.sql']=(bundle/'activation.sql').read_text()
    cm['data']['release.json']=(bundle/'release.json').read_text()
    cm['immutable']=True
    job_name=run+'-activate';labels={'app.kubernetes.io/name':job_name}
    activation_code='''import os
from pathlib import Path
import psycopg
try:
    with psycopg.connect(host='trading-postgres.trading.svc.cluster.local',port=5432,
            dbname='trading_platform',user=os.environ['PGUSER'],password=os.environ['PGPASSWORD'],
            connect_timeout=5) as conn:
        conn.execute(Path('/config/activation.sql').read_text())
    print('SWING_GUARDED_ACTIVATION_COMMITTED',flush=True)
except Exception as exc:
    print('SWING_ACTIVATION_BLOCKED '+type(exc).__name__,flush=True)
    raise SystemExit(1)
'''
    env=[{'name':name,'valueFrom':{'secretKeyRef':{'name':'trading-postgres-auth','key':key}}}
         for name,key in [('PGUSER','POSTGRES_ADMIN_USER'),('PGPASSWORD','POSTGRES_ADMIN_PASSWORD')]]
    security={'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL']}}
    job={'apiVersion':'batch/v1','kind':'Job','metadata':{'name':job_name,'namespace':'trading'},
         'spec':{'backoffLimit':0,'activeDeadlineSeconds':120,'template':{'metadata':{'labels':labels},'spec':{
             'restartPolicy':'Never','automountServiceAccountToken':False,'imagePullSecrets':[{'name':'ghcr-pull'}],
             'securityContext':{'runAsNonRoot':True,'runAsUser':65532,'runAsGroup':65532,'seccompProfile':{'type':'RuntimeDefault'}},
             'containers':[{'name':'activate','image':image,'command':['python','-c',activation_code],
                'env':env,'securityContext':security,'resources':{'requests':{'cpu':'25m','memory':'64Mi'},'limits':{'cpu':'200m','memory':'128Mi'}},
                'volumeMounts':[{'name':'config','mountPath':'/config','readOnly':True}]}],
             'volumes':[{'name':'config','configMap':{'name':cm['metadata']['name']}}]}}}}
    pg={'matchLabels':{'app.kubernetes.io/name':'trading-postgres'}}
    policies=[{'apiVersion':'networking.k8s.io/v1','kind':'NetworkPolicy',
        'metadata':{'name':job_name+'-egress','namespace':'trading'},'spec':{'podSelector':{'matchLabels':labels},
        'policyTypes':['Ingress','Egress'],'ingress':[],'egress':[
            {'to':[{'podSelector':pg}],'ports':[{'port':5432,'protocol':'TCP'}]},
            {'to':[{'namespaceSelector':{'matchLabels':{'kubernetes.io/metadata.name':'kube-system'}}}],
             'ports':[{'port':53,'protocol':'TCP'},{'port':53,'protocol':'UDP'}]}]}},
        {'apiVersion':'networking.k8s.io/v1','kind':'NetworkPolicy','metadata':{'name':job_name+'-postgres','namespace':'trading'},
         'spec':{'podSelector':pg,'policyTypes':['Ingress'],'ingress':[{'from':[{'podSelector':{'matchLabels':labels}}],
         'ports':[{'port':5432,'protocol':'TCP'}]}]}}]
    old=json.loads((root/'apps/trading-platform/swing-position-20261028/workload.json').read_text())
    archived=[x for x in old['items'] if x['kind'] in ('ConfigMap','Job')]
    assert all(x['spec'].get('suspend') is True for x in archived if x['kind']=='Job')
    relay={'apiVersion':'apps/v1','kind':'Deployment','metadata':{'name':'library-event-relay','namespace':'trading'},
        'spec':{'replicas':1,'template':{'spec':{'containers':[{'name':'relay','image':image,
            'env':[{'name':'EXECUTION_AUTHORITY_MODE','value':'DISABLED'}]}]}}}}
    def write(name,value):
        (dest/name).write_text(json.dumps(value,indent=2)+'\n')
    write('workload.json',workload);write('activation-job.json',job)
    write('activation-network.json',{'apiVersion':'v1','kind':'List','items':policies})
    write('historical-resources.json',{'apiVersion':'v1','kind':'List','items':archived})
    write('relay.patch.json',relay)
    (dest/'kustomization.yaml').write_text('apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources:\n  - workload.json\n  - activation-job.json\n  - activation-network.json\n  - historical-resources.json\n')
    print(json.dumps({'staged':str(dest),'run_id':run,'activation_time':cfg['activation_time'],'execution_eligible':False}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bundle',type=Path,required=True)
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[2]);a=p.parse_args()
    stage(a.bundle,a.root)
