"""Bounded registration/activation using the existing migration connection.

Never creates credentials, changes grants, or performs broker operations.
Observer credentials are mounted separately and never become the SQL writer.
"""
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path('/cutover')
OLD_RUNS = ['pa-ticks-20261019-1700-v1', 'pa-ticks-20261019-1730-v2']


def require(value, message):
    if not value:
        raise ValueError(message)


def target(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT current_database(),inet_server_addr()::text,inet_server_port(),pg_is_in_recovery()")
        row = cur.fetchone()
    conn.rollback()
    require(row and row[3] is False, 'database connection does not target a primary')
    return row[:3]


def observer_config():
    from postgres.config import PostgresConfig
    root = Path('/observer-connection')
    names = {'dsn': 'TRADING_POSTGRES_DSN', 'host': 'PGHOST', 'port': 'PGPORT',
             'database': 'PGDATABASE', 'user': 'PGUSER', 'password': 'PGPASSWORD'}
    values = {k: (root / v).read_text().strip() for k, v in names.items() if (root / v).is_file()}
    config = PostgresConfig(**values)
    config.require_explicit_target()
    return config


def verify_observer_privileges(conn):
    with conn.cursor() as cur:
        cur.execute('BEGIN READ ONLY')
        cur.execute('SELECT rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user')
        require(not any(cur.fetchone()), 'observer role has administrative privileges')
        cur.execute("""SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
            AND c.relkind IN ('r','p','v','m','f')
            AND (n.nspname||'.'||c.relname) NOT IN
                ('research.price_action_observer_state','research.price_action_observer_evaluation')
            AND has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER')""")
        require(cur.fetchone()[0] == 0, 'observer role has unrelated table writes')
    conn.rollback()


def reject_existing_evidence(conn):
    with conn.cursor() as cur:
        cur.execute("""SELECT count(*) FROM strategy_mgmt.strategy_instance_v2 i
            WHERE i.attributes->>'corrected_run_id'=ANY(%s)
            AND (EXISTS (SELECT 1 FROM research.price_action_observer_state s WHERE s.instance_id=i.id::text)
              OR EXISTS (SELECT 1 FROM research.price_action_observer_evaluation e WHERE e.instance_id=i.id::text))""", (OLD_RUNS,))
        require(cur.fetchone()[0] == 0, 'superseded run has durable evidence; preserve it and inspect before cutover')


def ready(conn, run, now=None):
    now = time.time() if now is None else now
    with conn.cursor() as cur:
        for key, info in run['instances'].items():
            cur.execute('SELECT state,last_observed_at FROM research.price_action_observer_state WHERE instance_id=%s AND strategy_key=%s',
                        (info['instance_id'], key))
            row = cur.fetchone()
            if not row or not row[1] or not 0 <= now-row[1].timestamp() <= 120:
                return False
            state = row[0]
            ticks = state.get('instrument_tick_sizes') or {}
            if (state.get('status') != 'RUNNING' or
                state.get('runtime', {}).get('successful_instrument_count') != 3 or
                set(ticks) != {'XAUUSD', 'BTCUSD', 'USDJPY'} or
                any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in ticks.values())):
                return False
    return True


def main():
    from postgres.config import PostgresConfig
    from postgres.db import connect
    action = sys.argv[1]
    require(action in ('register', 'activate'), 'unsupported cutover action')
    run = json.loads((ROOT / 'run.json').read_text())
    config = PostgresConfig.from_env(); config.require_explicit_target()
    spec = importlib.util.spec_from_file_location('pa_preflight', ROOT / 'preflight.py')
    preflight = importlib.util.module_from_spec(spec); spec.loader.exec_module(preflight)
    with connect(config) as authority, connect(observer_config()) as observer:
        require(target(authority) == target(observer), 'migration and observer connections target different primaries')
        verify_observer_privileges(observer)
        authority.autocommit = True
        with authority.cursor() as cur:
            cur.execute("SET statement_timeout='15s'")
        if action == 'register':
            reject_existing_evidence(authority)
            with authority.cursor() as cur:
                cur.execute((ROOT / 'register.sql').read_text())
        else:
            deadline = time.monotonic() + 1500
            while not ready(authority, run):
                require(time.monotonic() < deadline, 'timed out waiting for both fresh three-instrument checkpoints')
                time.sleep(10)
        for key, info in run['instances'].items():
            manifest = json.loads((ROOT / (key + '.json')).read_text())
            preflight.check(observer, manifest, key, info['instance_id'])
        if action == 'activate':
            reject_existing_evidence(authority)
            with authority.cursor() as cur:
                cur.execute((ROOT / 'activate.sql').read_text())
        print(json.dumps({'status': 'COMPLETED', 'action': action, 'run_id': run['run_id'],
                          'observed_at_epoch': time.time(), 'broker_writes': 0}))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Connection errors can contain sensitive endpoint data; do not echo them.
        print(json.dumps({'status': 'BLOCKED', 'reason': ('ModuleNotFoundError: ' + str(exc.name)) if isinstance(exc, ModuleNotFoundError) else (str(exc) if isinstance(exc, ValueError) else type(exc).__name__), 'broker_writes': 0}))
        raise SystemExit(1)
