"""Fail-closed, one-transaction research run cutover; no broker operations."""
import json
import os
from pathlib import Path
import ssl
import time
import urllib.request
from research.library_event_store import open_connection
from research.strategy_library_forward import RunConfig
from research.strategy_library_registration import registration


def kube(path):
    token=Path('/var/run/secrets/kubernetes.io/serviceaccount/token').read_text().strip()
    host=os.environ['KUBERNETES_SERVICE_HOST']
    port=os.environ['KUBERNETES_SERVICE_PORT_HTTPS']
    req=urllib.request.Request('https://'+host+':'+port+path,
        headers={'Authorization':'Bearer '+token})
    ctx=ssl.create_default_context(cafile='/var/run/secrets/kubernetes.io/serviceaccount/ca.crt')
    with urllib.request.urlopen(req,context=ctx,timeout=10) as response:
        return json.load(response)


def activate():
    cfg=RunConfig.load('/config/run.json')
    if cfg.activation_time-time.time()<120:
        raise ValueError('T0 has insufficient remaining margin; prepare a fresh run')
    prefix='/apis/apps/v1/namespaces/trading/deployments/'
    old=kube(prefix+'strategy-library-canary-v1-observer')
    if old['spec'].get('replicas',1)!=0 or old.get('status',{}).get('replicas',0)!=0:
        raise ValueError('old polling observer has not stopped')
    pods=kube('/api/v1/namespaces/trading/pods?labelSelector=app.kubernetes.io%2Fname%3Dstrategy-library-canary-v1-observer')
    if any(p.get('status',{}).get('phase') not in ['Succeeded','Failed'] for p in pods['items']):
        raise ValueError('old polling pod still present, including terminating pods')
    # Image is bound by the activation attestation embedded in the SQL/manifest bundle.
    image=json.loads(Path('/config/release.json').read_text())['image']
    for deployment,container in [('mt5-native-bridge-main-runtime','orchestrator-shadow'),
                                  ('market-data-collector','collector')]:
        d=kube(prefix+deployment)
        status=d.get('status',{})
        if (status.get('observedGeneration',0)<d['metadata']['generation']
                or status.get('updatedReplicas',0)!=1 or status.get('readyReplicas',0)!=1
                or status.get('replicas',0)!=1):
            raise ValueError('prerequisite deployment has not completed its rollout: '+deployment)
        c=next(c for c in d['spec']['template']['spec']['containers'] if c['name']==container)
        if c['image']!=image:
            raise ValueError('prerequisite image lacks reviewed source: '+deployment)
        if deployment=='market-data-collector':
            env={e['name']:e.get('value') for e in c.get('env',[])}
            if env.get('MARKET_DATA_EVENT_MODE')!='JETSTREAM_OUTBOX':
                raise ValueError('collector emission not configured')
    job=kube('/apis/batch/v1/namespaces/trading/jobs/library-event-provision-streams-'+cfg.run_id)
    if job.get('status',{}).get('succeeded')!=1:
        raise ValueError('bounded stream provisioning has not succeeded')
    with open_connection() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT state FROM research.strategy_library_forward_state WHERE run_id=%s',(cfg.run_id,))
            if cur.fetchone():
                raise ValueError('run already exists; verify it rather than repeat activation')
            conn.rollback()
            sql=Path('/config/activation.sql').read_text()
            quiesce=[]
            for key in cfg.keys:
                r=registration(key)
                cur.execute('''SELECT attributes->>'orchestrator_run_id', execution_mode,execution_eligible
                    FROM strategy_mgmt.strategy_instance_v2 WHERE id=%s''',(r['instance_id'],))
                if cur.fetchone()!=('library-canary-20261016-v1','OFF',False):
                    raise ValueError('expected original OFF/ineligible run binding missing: '+key)
                cur.execute('''SELECT canonical_instrument FROM strategy.instrument_membership
                    WHERE strategy_instance_id=%s AND strategy_id=%s AND state='ACTIVE' ''',
                    (r['instance_id'],r['strategy_id']))
                if {x[0] for x in cur.fetchall()}!=set(cfg.symbols):
                    raise ValueError('active universe differs from frozen manifest: '+key)
                # SQL uses planner-escaped deterministic UUIDs only, no credential interpolation.
                quiesce.append("DO $source$ BEGIN UPDATE strategy_mgmt.strategy_instance_v2 SET online=false,updated_at=now() "
                    "WHERE id='"+r['instance_id']+"'::uuid AND attributes->>'orchestrator_run_id'='library-canary-20261016-v1' "
                    "AND execution_mode='OFF' AND NOT execution_eligible; "
                    "IF NOT FOUND THEN RAISE EXCEPTION 'Original run binding changed: abort cutover'; END IF; END $source$;")
            conn.rollback()
            sql=sql.replace('BEGIN;', 'BEGIN;\n'+'\n'.join(quiesce),1)
            cur.execute(sql)
        conn.commit()
    print(json.dumps({'status':'ACTIVATED_RESEARCH_ONLY','run_id':cfg.run_id,'broker_writes':0}))


if __name__=='__main__':
    try:activate()
    except Exception as exc:
        # Driver errors can contain connection details: never dump raw errors/tracebacks.
        print(json.dumps({'status':'BLOCKED','error_type':type(exc).__name__,
            'reason':str(exc) if isinstance(exc,ValueError) else 'Inspect prerequisite status; credentials redacted',
            'broker_writes':0}),flush=True)
        raise SystemExit(1)
