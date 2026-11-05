"""Stage planner-produced MT5 counterpart runs; no cluster calls or credentials read."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
import time


def stage(bundle, root, *, now=None):
    config=json.loads((bundle/'run.json').read_text())
    ready=json.loads((bundle/'readiness.json').read_text())
    run=config['run_id'];image=ready['image']
    if not re.fullmatch('[a-z0-9][a-z0-9-]{0,53}',run):raise ValueError('DNS-safe fresh run required')
    if config['execution_eligible'] is not False or config['transport']!='JETSTREAM':
        raise ValueError('event-driven research-only run required')
    if type(config['activation_time']) is not int or config['activation_time'] < (time.time() if now is None else now)+600:
        raise ValueError('fresh T0 at least ten minutes ahead required; never rewrite a binding')
    if not re.fullmatch(r'ghcr\.io/strat-relay/trading-platform-runtime@sha256:[0-9a-f]{64}',image):
        raise ValueError('built immutable runtime required')
    dest=root/'apps/trading-platform'/run
    dest.mkdir(parents=True,exist_ok=False)
    workload=json.loads((bundle/'workload.json').read_text())
    cm=next(x for x in workload['items'] if x['kind']=='ConfigMap')
    if json.loads(cm['data']['run.json'])!=config:raise ValueError('manifest differs from workload')
    cm['data'].update({name:(bundle/name).read_text() for name in ('registration.sql','activation.sql')})
    template=root/'apps/trading-platform/swing-position-20261031-r2'
    old='swing-position-20261031-r2'
    job=json.loads((template/'activation-job.json').read_text().replace(old,run))
    container=job['spec']['template']['spec']['containers'][0]
    container['image']=image
    code="""import os
from pathlib import Path
import psycopg
try:
    activation=Path('/config/activation.sql').read_text()
    registration=Path('/config/registration.sql').read_text()
    if activation.count('BEGIN;') != 1:
        raise ValueError('single guarded activation transaction required')
    sql=activation.replace('BEGIN;', 'BEGIN;'+registration, 1)
    with psycopg.connect(host='trading-postgres.trading.svc.cluster.local',port=5432,
            dbname='trading_platform',user=os.environ['PGUSER'],password=os.environ['PGPASSWORD'],
            connect_timeout=5) as conn:
        conn.execute(sql)
    print('MT5_COUNTERPART_REGISTRATION_AND_ACTIVATION_COMMITTED',flush=True)
except Exception as exc:
    print('MT5_COUNTERPART_ACTIVATION_BLOCKED '+type(exc).__name__,flush=True)
    raise SystemExit(1)
"""
    container['command']=['python','-c',code]
    job['spec']['template']['spec']['volumes'][0]['configMap']['name']=cm['metadata']['name']
    for d in workload['items']:
        if d['kind']=='Deployment' and any(c['image']!=image for c in d['spec']['template']['spec']['containers']):
            raise ValueError('workload image differs from binding image')
    policies=json.loads((template/'activation-network.json').read_text().replace(old,run))
    for name,data in [('workload.json',workload),('activation-job.json',job),('activation-network.json',policies),('run.json',config),('readiness.json',ready)]:
        (dest/name).write_text(json.dumps(data,indent=2)+'\n')
    (dest/'quiesce.sql').write_text((bundle/'quiesce.sql').read_text())
    (dest/'kustomization.yaml').write_text('apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources:\n  - workload.json\n  - activation-job.json\n  - activation-network.json\n')
    top=root/'apps/trading-platform/kustomization.yaml'
    text=top.read_text()
    if text.count('resources:\n')!=1:raise ValueError('review top-level Kustomization structure')
    top.write_text(text.replace('resources:\n','resources:\n  - '+run+'\n',1))
    if set(config['symbols'])=={'BTCUSD','XAUUSD','USDJPY'}:
        # Restore the full counterpart event scope after the older BTC-only patch.
        patch={'apiVersion':'apps/v1','kind':'Deployment','metadata':{'name':'market-data-collector','namespace':'trading'},
            'spec':{'template':{'spec':{'containers':[{'name':'collector','env':[
                {'name':'MARKET_DATA_EVENT_SYMBOLS','value':'BTCUSDm,XAUUSDm,USDJPYm'}]}]}}}}
        (dest/'collector-event-scope.patch.json').write_text(json.dumps(patch,indent=2)+'\n')
        lines=top.read_text().splitlines()
        if lines.count('patches:')!=1:raise ValueError('review existing collector patch ordering')
        start=lines.index('patches:')+1
        end=next((i for i in range(start,len(lines)) if lines[i] and not lines[i].startswith((' ','#'))),len(lines))
        lines.insert(end,'  - path: '+run+'/collector-event-scope.patch.json')
        top.write_text('\n'.join(lines)+'\n')
    return dest


if __name__=='__main__':
    p=argparse.ArgumentParser(__doc__)
    p.add_argument('--bundle',type=Path,required=True);p.add_argument('--root',type=Path,required=True)
    args=p.parse_args();print(stage(args.bundle,args.root))
