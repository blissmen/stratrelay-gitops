import ast
import json
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

import research_observer_diagnostics as diagnostics


class Store:
    def snapshot(self, symbol):
        return {'fetched_at': 1793390400, 'symbol_info': {'password': 'DO_NOT_PRINT'}}

    def state(self, symbol):
        return {'health_state': 'STALE', 'healthy': False, 'password': 'DO_NOT_PRINT'}

    def quote(self, symbol):
        return {'observed_at': 1793390400, 'quote': {'time': 1793390400,
                'bid': 12345, 'ask': 12346, 'password': 'DO_NOT_PRINT'}}

    def bars(self, symbol, timeframe):
        return []


class DiagnosticsTests(unittest.TestCase):
    def test_denied_migration_read_does_not_discard_registration_and_checkpoint_reads(self):
        class Denied(Exception):
            sqlstate = '42501'
        conn, cur = MagicMock(), MagicMock()
        conn.__enter__.return_value = conn
        conn.cursor.return_value.__enter__.return_value = cur
        cur.fetchone.return_value = ('trading_platform', False, 'on')
        cur.execute.side_effect = lambda sql, *args: (_ for _ in ()).throw(Denied()) if 'schema_migrations' in sql else None
        cur.fetchall.side_effect = [[('id',False,False,'OFF','daily_ema_pullback','run')],
                                   [('run',{'status':'OK','streams':[]},None)]]
        with patch('postgres.db.connect',return_value=conn):
            result = diagnostics.db_report()
        self.assertIsNone(result['migrations'])
        self.assertEqual(result['instances'][0]['run_id'],'run')
        self.assertEqual(result['checkpoints'][0]['health_status'],'OK')
        self.assertEqual(result['errors'],[{'section':'migration_checksums',
                         'error_type':'Denied','sqlstate':'42501'}])

    def test_real_staleness_is_not_waived_for_weekend(self):
        # Friday 20:00 UTC -> Saturday 10:00 UTC, ahead of 21:00 close.
        report = diagnostics.cache_report(Store(), 1793440800)
        btc = next(x for x in report if x['symbol'] == 'BTCUSDm')
        gold = next(x for x in report if x['symbol'] == 'XAUUSDm')
        self.assertIsNone(btc['closure_window'])
        self.assertTrue(gold['source_stale_before_closure'])
        self.assertGreater(gold['snapshot_age_seconds'], 330)

    def test_diagnostic_output_does_not_include_raw_cache_or_prices(self):
        report = json.dumps(diagnostics.cache_report(Store(), 1793440800))
        self.assertNotIn('DO_NOT_PRINT', report)
        self.assertNotIn('12345', report)
        self.assertNotIn('12346', report)

    def test_error_messages_are_redacted(self):
        output = json.dumps(diagnostics.safe_error(ValueError('password=DO_NOT_PRINT')))
        self.assertNotIn('DO_NOT_PRINT', output)

    def test_every_database_statement_is_literal_read_or_readonly_setting(self):
        tree = ast.parse(Path(diagnostics.__file__).read_text())
        queries = [n.args[0] for n in ast.walk(tree) if isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Attribute) and n.func.attr == 'execute']
        self.assertTrue(queries)
        for query in queries:
            self.assertIsInstance(query, ast.Constant)
            self.assertIsInstance(query.value, str)
            self.assertTrue(query.value.lstrip().upper().startswith(('SELECT ', 'SET ')))


if __name__ == '__main__':
    unittest.main()
