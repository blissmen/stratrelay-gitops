"""Read-only init gate shared by the three event workloads."""
import json
from pathlib import Path
from research.library_event_store import open_connection, SCHEMA
from research.strategy_library_forward import RunConfig, ForwardObserver


def gate():
    cfg=RunConfig.load('/config/run.json')
    with open_connection() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT state FROM research.strategy_library_forward_state WHERE run_id=%s',(cfg.run_id,))
            row=cur.fetchone()
        if not row:
            raise ValueError('activation has not committed')
        state=row[0]
        observer=ForwardObserver(cfg)
        observer.restore(state)
        expected={'schema':SCHEMA,'run_id':cfg.run_id,'binding':observer.binding,
            'activation_time':cfg.activation_time,'transport':'JETSTREAM','execution_eligible':False,
            'image':json.loads(Path('/config/release.json').read_text())['image']}
        if any(state.get('activation_attestation',{}).get(k)!=v for k,v in expected.items()):
            raise ValueError('activation attestation mismatch')
    print(json.dumps({'status':'RESEARCH_ACTIVATION_VERIFIED','run_id':cfg.run_id,'broker_writes':0}))


if __name__=='__main__':
    try:gate()
    except Exception as exc:
        print(json.dumps({'status':'BLOCKED','error_type':type(exc).__name__,'broker_writes':0}))
        raise SystemExit(1)
