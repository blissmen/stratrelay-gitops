"""Read-only binding report and original Donchian activation verification."""
from dataclasses import asdict
import json


def inspect_binding(config, stored, observer):
    saved=(stored or {}).get('manifest',{})
    encode=lambda value:json.dumps(value,sort_keys=True,separators=(',',':'))
    config_matches=encode(saved.get('config'))==encode(asdict(config))
    artifact_matches=saved.get('artifact')==observer.artifact
    binding_matches=bool(stored and stored.get('binding')==observer.binding)
    matches=binding_matches and config_matches and artifact_matches
    return {'status':'BINDING_MATCH' if matches else 'BINDING_MISMATCH' if stored else 'RUN_MISSING',
            'run_id':config.run_id,'stored_binding':(stored or {}).get('binding'),
            'expected_binding':observer.binding,'stored_artifact':saved.get('artifact'),
            'expected_artifact':observer.artifact,
            'config_matches':config_matches,'artifact_matches':artifact_matches,'binding_matches':binding_matches,
            'stream_count':len((stored or {}).get('streams',{})),
            'attestation_image':(stored or {}).get('activation_attestation',{}).get('image'),
            'read_only':True,'broker_writes':0,'database_writes':0}


def main():
    from postgres.db import connect
    from research.strategy_library_forward import RunConfig,ForwardObserver
    from research.donchian_v2_forward import verify
    cfg=RunConfig.load('/config/run.json')
    with connect(readonly=True) as conn:
        with conn.cursor() as cur:
            cur.execute('SET TRANSACTION READ ONLY')
            cur.execute("SET LOCAL statement_timeout='10s'")
            cur.execute('SELECT state FROM research.strategy_library_forward_state WHERE run_id=%s',(cfg.run_id,))
            row=cur.fetchone()
        conn.rollback()
    result=inspect_binding(cfg,row[0] if row else None,ForwardObserver(cfg))
    print(json.dumps(result,sort_keys=True),flush=True)
    if result['status']!='BINDING_MATCH':raise SystemExit(1)
    # Binding match never bypasses the original activation/parameter checks.
    try:
        with connect(readonly=True) as conn:verified=verify(conn,cfg)
    except Exception as exc:
        print(json.dumps({'status':'ACTIVATION_VERIFICATION_FAILED','error_type':type(exc).__name__,
                          'read_only':True,'database_writes':0,'broker_writes':0}),flush=True)
        raise SystemExit(1)
    print(json.dumps(verified,sort_keys=True),flush=True)


if __name__=='__main__':main()
