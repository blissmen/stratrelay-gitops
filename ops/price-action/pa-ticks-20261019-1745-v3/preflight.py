"""Read-only startup guard for this concrete PA observation cutover."""
import json
import os
from pathlib import Path
import time

POLICY = 'pa-provider-tick.v1'
ALLOWED = ('research.price_action_observer_state', 'research.price_action_observer_evaluation')


def require(value, reason):
    if not value:
        raise ValueError(reason)


def check(conn, manifest, key, instance, now=None):
    require(manifest['instance_id'] == instance, 'manifest/instance mismatch')
    require(manifest['market_input_policy'] == POLICY, 'input policy mismatch')
    require(manifest['entry_mode'] == 'CONTEMPORANEOUS_CANDLE_OPEN', 'entry mode mismatch')
    t0 = int(__import__('datetime').datetime.fromisoformat(manifest['t0'].replace('Z', '+00:00')).timestamp())
    expected = manifest['strategies'][key]
    with conn.cursor() as cur:
        cur.execute('BEGIN READ ONLY')
        cur.execute('SELECT rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user')
        require(not any(cur.fetchone()), 'observer role has administrative privileges')
        cur.execute("""SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
              AND c.relkind IN ('r','p','v','m','f')
              AND (n.nspname||'.'||c.relname) <> ALL(%s)
              AND has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER')""", (list(ALLOWED),))
        require(cur.fetchone()[0] == 0, 'observer role can write outside the two PA research tables')
        cur.execute("""SELECT bool_and(has_table_privilege(current_user,t,p)) FROM (VALUES
            ('research.price_action_observer_state','SELECT'),
            ('research.price_action_observer_state','INSERT'),
            ('research.price_action_observer_state','UPDATE'),
            ('research.price_action_observer_evaluation','SELECT'),
            ('research.price_action_observer_evaluation','INSERT')) AS required(t,p)""")
        require(cur.fetchone()[0] is True, 'required PA research privileges missing')
        cur.execute("""SELECT v.evaluator_key,v.version_label,v.lifecycle,p.parameter_set_id,p.fingerprint,
            p.values,p.frozen,i.attributes,i.execution_mode,i.execution_eligible,i.instruments
            FROM strategy_mgmt.strategy_instance_v2 i
            JOIN strategy_mgmt.strategy_version v ON v.id=i.strategy_version_id
            JOIN strategy_mgmt.parameter_set p ON p.id=i.parameter_set_id AND p.strategy_version_id=v.id
            WHERE i.id=%s::uuid""", (instance,))
        row = cur.fetchone()
        require(row is not None, 'corrected instance registration missing')
        evaluator, version, lifecycle, parameter, digest, values, frozen, attrs, mode, eligible, instruments = row
        evaluator_keys = {'sr_retest': 'price_action_sr_retest_v1',
                          'engulfing_continuation': 'price_action_engulfing_continuation_v1'}
        require(evaluator == evaluator_keys[key] and version == 'V1' and lifecycle == 'FROZEN', 'version binding mismatch')
        require(frozen and digest == expected['parameter_hash'] and values == expected['parameters'], 'frozen parameter mismatch')
        require(attrs.get('parameter_set_id') == parameter, 'explicit parameter reference missing')
        require(attrs.get('market_input_policy') == POLICY and attrs.get('corrected_run_id') == manifest['run_id'], 'run binding mismatch')
        require(attrs.get('publication_activation_time') == t0 and attrs.get('manifest_hash') == manifest['manifest_hash'], 'T0/manifest binding mismatch')
        require(attrs.get('forward_image') == os.environ['PA_EXPECTED_IMAGE'], 'runtime image binding mismatch')
        require(attrs.get('broker_writes') is False and mode == 'OFF' and eligible is False, 'execution isolation mismatch')
        require(not attrs.get('instance_id') and not attrs.get('legacy_instance_id'), 'legacy identity alias forbidden')
        require(sorted(instruments) == sorted(manifest['universe_requested']), 'instrument scope mismatch')
        cur.execute('SELECT state FROM research.price_action_observer_state WHERE instance_id=%s', (instance,))
        state = cur.fetchone()
        if state:
            require(state[0].get('market_input_policy') == POLICY and state[0].get('t0') == manifest['t0'], 'incompatible checkpoint')
        else:
            require((time.time() if now is None else now) < t0, 'planned T0 passed before bootstrap; generate a new run')
    conn.rollback()


def main():
    from postgres.config import PostgresConfig
    from postgres.db import connect
    config = PostgresConfig.from_env()
    config.require_explicit_target()
    manifest = json.loads(Path(os.environ['PRICE_ACTION_MANIFEST_PATH']).read_text())
    with connect(config) as conn:
        check(conn, manifest, os.environ['PRICE_ACTION_STRATEGY'], os.environ['PRICE_ACTION_INSTANCE_ID'])
    print('Corrected PA registration and research-only database privileges verified')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Do not echo connection strings, credentials or database error details.
        print('PA cutover preflight blocked: ' + (('ModuleNotFoundError: ' + str(exc.name)) if isinstance(exc, ModuleNotFoundError) else (str(exc) if isinstance(exc, ValueError) else type(exc).__name__)))
        raise SystemExit(1)
