"""One-shot read-only cache/checkpoint report. No broker or message operations."""
import json
import math
import time

SYMBOLS = ('BTCUSDm', 'XAUUSDm', 'USDJPYm', 'EURUSDm')
KEYS = ('fvg_retracement', 'order_block_retracement', 'h4_structure_pullback',
        'daily_channel_breakout', 'daily_ema_pullback', 'weekly_range_break_retest',
        'h4_momentum_continuation')


def safe_error(exc):
    # Driver exception text and environment values may contain credentials.
    return {'error_type': type(exc).__name__, 'sqlstate': getattr(exc, 'sqlstate', None)}


def numeric(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def cache_report(store, now):
    from market_data_cache.session_calendar import classify_missing, gap_payload
    from platform_api.library_market_schedule import weekend_window
    result = []
    for symbol in SYMBOLS:
        try:
            raw = store.snapshot(symbol) or {}
            state = store.state(symbol) or {}
            hot = store.quote(symbol) or {}
            quote = hot.get('quote') or {}
            stamp = raw.get('fetched_at')
            item = {'symbol': symbol, 'snapshot_fetched_at': stamp if numeric(stamp) else None,
                    'snapshot_age_seconds': now-stamp if numeric(stamp) else None,
                    'state': {k: state.get(k) for k in ('health_state', 'healthy',
                        'consecutive_failures', 'backfill_required', 'last_attempt_at')},
                    'quote_ages': {k: now-v if numeric(v) else None for k, v in (
                        ('provider_seconds', quote.get('time')), ('cache_seconds', hot.get('observed_at')))},
                    'timeframes': {}}
            asset = 'CRYPTO' if symbol == 'BTCUSDm' else 'METAL' if symbol == 'XAUUSDm' else 'FX'
            window = weekend_window(asset, now)
            item['closure_window'] = window
            item['source_stale_before_closure'] = (
                stamp < window['closure_start']-330 if window and numeric(stamp) else None)
            for tf in ('M5', 'M15', 'H1', 'H4'):
                bars = store.bars(symbol, tf)
                expected, gaps = classify_missing(bars, tf, provider_symbol=symbol)
                item['timeframes'][tf] = {
                    'bars': len(bars), 'first_time': bars[0]['time'] if bars else None,
                    'last_time': bars[-1]['time'] if bars else None,
                    'expected_closure_count': len(expected), 'true_gap_count': len(gaps),
                    'recent_true_gaps': [gap_payload(gap, tf) for gap in gaps[-3:]]}
            result.append(item)
        except Exception as exc:
            result.append({'symbol': symbol, **safe_error(exc)})
    return result


def db_report():
    from postgres.db import connect
    errors = []
    with connect(readonly=True) as conn:
        with conn.cursor() as cur:
            cur.execute('SET TRANSACTION READ ONLY')
            cur.execute("SET LOCAL statement_timeout='10s'")
            cur.execute("SELECT current_database(),pg_is_in_recovery(),current_setting('transaction_read_only')")
            database, recovery, read_only = cur.fetchone()
            if database != 'trading_platform' or recovery or read_only != 'on':
                raise ValueError('verified read-only primary required')
            try:
                with conn.transaction():
                    cur.execute("SELECT version,checksum_sha256 FROM platform.schema_migrations WHERE version IN ('070','078') ORDER BY version")
                    migrations = dict(cur.fetchall())
            except Exception as exc:
                migrations = None
                errors.append({'section': 'migration_checksums', **safe_error(exc)})
            try:
                with conn.transaction():
                    cur.execute('''SELECT id::text,online,execution_eligible,execution_mode,
                                  attributes->>'library_key',attributes->>'orchestrator_run_id'
                           FROM strategy_mgmt.strategy_instance_v2
                           WHERE attributes->>'library_key'=ANY(%s) ORDER BY id''', (list(KEYS),))
                    instances = [dict(zip(('instance_id','online','execution_eligible','execution_mode',
                                  'library_key','run_id'), row)) for row in cur.fetchall()]
            except Exception as exc:
                instances = []
                errors.append({'section': 'managed_instances', **safe_error(exc)})
            runs = sorted({i['run_id'] for i in instances if i['run_id']} | {
                'library-canary-20261016-v1','library-canary-20261031-r1',
                'ict-smc-20261031-r1','donchian-v2-20261031-r1','swing-position-20261028-v1'})
            checkpoints = []
            try:
                with conn.transaction():
                    cur.execute('''SELECT run_id,state->'health',state->'activation_attestation'->>'transport'
                                   FROM research.strategy_library_forward_state WHERE run_id=ANY(%s)''', (runs,))
                    for run_id, health, transport in cur.fetchall():
                        health = health or {}
                        checkpoints.append({'run_id': run_id, 'transport': transport,
                            'health_status': health.get('status'), 'observed_at': health.get('observed_at'),
                            'streams': [{k: s.get(k) for k in ('key','instrument','status','error_type',
                                'last_finalized_close')} for s in health.get('streams', [])]})
            except Exception as exc:
                errors.append({'section': 'research_checkpoints', **safe_error(exc)})
        conn.rollback()
    return {'database': database, 'read_only': True, 'migrations': migrations,
            'instances': instances, 'checkpoints': checkpoints, 'errors': errors}


def main():
    report = {'report': 'research-observer-diagnostics', 'read_only': True,
              'broker_writes': 0, 'message_publications': 0, 'errors': []}
    try:
        from market_data_cache.reader import default_store
        store = default_store()
        report['cache_samples'] = []
        for index in range(3):
            now = time.time()
            report['cache_samples'].append({'observed_at': now, 'symbols': cache_report(store, now)})
            if index < 2:
                time.sleep(3)
    except Exception as exc:
        report['errors'].append({'section': 'cache', **safe_error(exc)})
    try:
        report['database'] = db_report()
        report['errors'].extend(report['database']['errors'])
    except Exception as exc:
        report['errors'].append({'section': 'database', **safe_error(exc)})
    print(json.dumps(report, allow_nan=False), flush=True)
    raise SystemExit(1 if report['errors'] else 0)


if __name__ == '__main__':
    main()
