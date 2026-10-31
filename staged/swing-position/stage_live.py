"""Compose a planner-produced, attested fresh Swing bundle for GitOps review.

No cluster/credential calls. Preserve the original paused Job/ConfigMap and all
historical files. The activation Job uses the established provisioning Secret,
never a copied admin DSN or a runtime event binding.
"""
import argparse
import json
from pathlib import Path
import shutil


def stage(bundle,root,prior_bundles=()):
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
    for dep in workload['items']:
        if dep['kind']!='Deployment' or dep['metadata']['name']!='swing-position-v1-orchestrator':continue
        pod=dep['spec']['template']['spec']
        # Existing DB_PRIMARY authority requires BOTH canonical-primary flags.
        # These are signal transport flags, never broker execution authority.
        for c in pod['containers']+pod['initContainers']:
            for e in c['env']:
                if e['name']=='SIGNAL_JETSTREAM_PRIMARY_ENABLED':e['value']='true'
        guard='''import os
from migration.flags import SignalAuthorityFlags,SignalAuthorityMode
from orchestration.canonical_signal_publisher import CanonicalSignalPublisher
from research.library_event_store import open_connection
from research.swing_position_forward import main
if SignalAuthorityFlags.mode_from_env() is not SignalAuthorityMode.DB_PRIMARY:
    raise ValueError('canonical research orchestrator requires DB_PRIMARY')
if os.environ.get('EXECUTION_AUTHORITY_MODE')!='DISABLED':
    raise ValueError('research execution authority must remain disabled')
with open_connection() as conn:
    CanonicalSignalPublisher(conn,cutoff_id=os.environ['SIGNAL_CUTOFF_ID'],
        cutoff_utc=os.environ['SIGNAL_CUTOFF_UTC'],restricted_research=True).require_schema()
main()
'''
        pod['initContainers'][0]['command']=['python','-c',guard,'verify','--manifest','/config/run.json']
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
    for prior in prior_bundles:
        previous=json.loads((prior/'workload.json').read_text())
        archived.extend(x for x in previous['items'] if x['kind']=='ConfigMap')
        archived.append(json.loads((prior/'activation-job.json').read_text()))
    identities=[(x['kind'],x['metadata']['name']) for x in archived]
    if len(identities)!=len(set(identities)):raise ValueError('duplicate historical resource identity')
    # A shared relay transports immutable outbox IDs, not one strategy run.
    # Never retain the legacy October-22 activation gate or source image.
    relay_gate='''import asyncio,json,os,sys
import nats
from research.library_event_store import open_connection
from research.library_event_service import check_streams
async def verify():
    if os.environ.get('EXECUTION_AUTHORITY_MODE')!='DISABLED':
        raise ValueError('research authority must remain disabled')
    with open_connection() as conn:
        row=conn.execute("SELECT has_table_privilege(current_user,'research.library_stream_outbox','SELECT'), has_column_privilege(current_user,'research.library_stream_outbox','published_at','UPDATE'),extract(epoch FROM clock_timestamp()-min(created_at)) FROM research.library_stream_outbox WHERE published_at IS NULL").fetchone()
        if not all(row[:2]):raise ValueError('relay privileges unavailable')
        if len(sys.argv)>1 and sys.argv[1]=='health' and row[2] is not None and row[2]>30:
            raise ValueError('relay backlog')
    nc=await nats.connect(os.environ['NATS_URL'],user=os.environ['P2_NATS_USER'],password=os.environ['P2_NATS_PASSWORD'],connect_timeout=3,max_reconnect_attempts=0)
    try:await check_streams(nc.jetstream())
    finally:await nc.close()
try:
    asyncio.run(verify());print(json.dumps({'status':'RELAY_PREREQUISITES_VERIFIED','broker_writes':0}))
except Exception as exc:
    print(json.dumps({'status':'BLOCKED','error_type':type(exc).__name__}));raise SystemExit(1)
'''
    relay={'apiVersion':'apps/v1','kind':'Deployment','metadata':{'name':'library-event-relay','namespace':'trading'},
        'spec':{'replicas':1,'template':{'spec':{
            'initContainers':[{'name':'verify-activation','image':image,'command':['python','-c',relay_gate]}],
            'containers':[{'name':'relay','image':image,
                'readinessProbe':{'exec':{'command':['python','-c',relay_gate,'health']},'periodSeconds':30,'timeoutSeconds':15,'failureThreshold':3},
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
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[2])
    p.add_argument('--prior-bundle',type=Path,action='append',default=[]);a=p.parse_args()
    stage(a.bundle,a.root,a.prior_bundle)
