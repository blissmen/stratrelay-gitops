"""Read-only continuation validation; never resets a saved research run."""
import json
from donchian_binding_preflight import inspect_binding

RUNS={
    'library-canary-20261031-r1':('donchian_breakout','ema_pullback','fvg_retracement'),
    'ict-smc-20261031-r1':('judas_swing@V2','power_of_three@V1','order_block_retracement@V2'),
}


def validate(config,stored,observer):
    if config.transport!='POLL' or RUNS.get(config.run_id)!=config.keys:
        raise ValueError('approved original continuation scope required')
    if config.symbols!={'BTCUSD':'BTCUSDm','USDJPY':'USDJPYm','XAUUSD':'XAUUSDm'}:
        raise ValueError('continuation instrument scope mismatch')
    report=inspect_binding(config,stored,observer)
    if (stored or {}).get('activation_attestation',{}).get('transport')=='JETSTREAM':
        report['status']='BLOCKED_EVENT_CUTOVER'
    return report


def main():
    from postgres.db import connect
    from research.strategy_library_forward import RunConfig,ForwardObserver
    config=RunConfig.load('/config/run.json')
    with connect(readonly=True) as conn:
        with conn.cursor() as cur:
            cur.execute('SET TRANSACTION READ ONLY')
            cur.execute("SET LOCAL statement_timeout='10s'")
            cur.execute("SELECT current_database(),current_setting('transaction_read_only')")
            if tuple(cur.fetchone())!=('trading_platform','on'):
                raise ValueError('verified read-only database required')
            cur.execute('SELECT state FROM research.strategy_library_forward_state WHERE run_id=%s',(config.run_id,))
            row=cur.fetchone()
        conn.rollback()
    report=validate(config,row[0] if row else None,ForwardObserver(config))
    print(json.dumps(report,sort_keys=True),flush=True)
    raise SystemExit(0 if report['status']=='BINDING_MATCH' else 1)


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'status':'PREFLIGHT_ERROR','error_type':type(exc).__name__,
            'sqlstate':getattr(exc,'sqlstate',None),'database_writes':0,'broker_writes':0}),flush=True)
        raise SystemExit(1)
