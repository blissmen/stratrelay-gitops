"""Readiness: durable subscriptions and activation, never broker connectivity."""
import asyncio
import os
import sys
import time
import nats
from research.library_event_service import check_streams
from research.library_event_store import open_connection
from research.strategy_library_forward import RunConfig


async def health(role):
    cfg=RunConfig.load('/config/run.json')
    nc=await nats.connect(os.environ['NATS_URL'],user=os.environ.get('P2_NATS_USER'),
        password=os.environ.get('P2_NATS_PASSWORD'),connect_timeout=3,max_reconnect_attempts=0)
    try:
        js=nc.jetstream()
        await check_streams(js)
        if role in ['observer','orchestrator']:
            stream='MARKET_EVIDENCE' if role=='observer' else 'LIBRARY_RESEARCH'
            info=await js.consumer_info(stream,role+'-'+cfg.run_id)
            if info.num_ack_pending>1:raise ValueError('consumer configuration drift')
        with open_connection() as conn:
            with conn.cursor() as cur:
                if role=='observer':
                    cur.execute('SELECT state FROM research.strategy_library_forward_state WHERE run_id=%s',(cfg.run_id,))
                    state=cur.fetchone()[0]
                    providers=state.get('health',{}).get('providers',{})
                    # BTC is the always-open canary heartbeat; closed FX/metal feeds
                    # do not make the process unready just because the market is shut.
                    btc=providers.get('BTCUSDm',{})
                    if not btc or not 0<=time.time()-btc.get('observed_at',0)<180:
                        raise ValueError('no recent BTC market event persisted')
                elif role=='relay':
                    cur.execute('SELECT extract(epoch from clock_timestamp()-min(created_at)) FROM research.library_stream_outbox WHERE published_at IS NULL')
                    age=cur.fetchone()[0]
                    if age is not None and age>30:raise ValueError('outbox relay backlog')
    finally:await nc.close()


if __name__=='__main__':
    try:asyncio.run(health(sys.argv[1]))
    except Exception:raise SystemExit(1)
