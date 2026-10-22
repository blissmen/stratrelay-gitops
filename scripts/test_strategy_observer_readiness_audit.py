import contextlib
import io
import json
import sys
from types import ModuleType,SimpleNamespace
import unittest
from unittest.mock import patch

import strategy_observer_readiness_audit as audit


class ObserverReadinessTests(unittest.TestCase):
    def test_probe_reports_cause_binding_and_metadata_without_evaluating_or_writing(self):
        sql=[]
        class Cursor:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def execute(self,statement,*args):sql.append(statement)
            def fetchone(self):return ({'status':'DEGRADED','observed_at':990,'streams':[
                {'key':'test','instrument':'XAUUSD','status':'BLOCKED','error_type':'MarketDataUnavailable',
                 'error_message':'canonical market data for XAUUSDm is stale (1800s)'},
                {'key':'other','instrument':'BTCUSD','status':'BLOCKED','error_message':'password=private'}]},'expected')
        class Conn:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def cursor(self):return Cursor()
        class Store:
            def health(self):return {'status':'healthy','password':'private'}
            def state(self,symbol):return {'healthy':True,'health_state':'HEALTHY','private':'password=private'}
            def snapshot(self,symbol):return {'fetched_at':900,'password':'private'}
            def quote(self,symbol):return {'observed_at':990,'quote':{'time':989,'bid':123,'ask':124,'password':'private'}}
            def bars(self,*args):return [{'time':1,'close':123},{'time':2,'close':124}]
        modules={name:ModuleType(name) for name in ('research','research.strategy_library_forward','postgres','postgres.db',
                                                   'market_data_cache','market_data_cache.reader')}
        config=SimpleNamespace(run_id='frozen-run',max_age_seconds=330,symbols={'XAUUSD':'XAUUSDm'})
        modules['research.strategy_library_forward'].RunConfig=SimpleNamespace(load=lambda _:config)
        modules['research.strategy_library_forward'].ForwardObserver=lambda _:SimpleNamespace(binding='expected')
        modules['postgres.db'].connect=lambda:Conn()
        modules['market_data_cache.reader'].default_store=lambda:Store()
        output=io.StringIO()
        with patch.dict(sys.modules,modules),patch('time.time',return_value=1000),contextlib.redirect_stdout(output):
            exec(compile(audit.PROBE,'<readiness-probe>','exec'),{})
        result=json.loads(output.getvalue())
        self.assertTrue(result['persisted']['binding_matches'])
        self.assertEqual(result['persisted']['health_age_seconds'],10)
        self.assertEqual(result['persisted']['streams'][0]['reason_code'],'STALE_CANONICAL_MARKET_DATA')
        self.assertEqual(result['persisted']['streams'][1]['reason_code'],'ERROR_MESSAGE_REDACTED')
        self.assertEqual(result['symbols'][0]['snapshot_age_seconds'],100)
        self.assertNotIn('private',json.dumps(result))
        self.assertNotIn('error_message',json.dumps(result))
        self.assertNotIn('bid',json.dumps(result))
        self.assertTrue(sql[0].startswith('SET TRANSACTION READ ONLY'))
        self.assertFalse(any(s.upper().startswith(('INSERT','UPDATE','DELETE','ALTER','GRANT','CREATE')) for s in sql))

    def test_command_failure_is_redacted(self):
        with patch('subprocess.run',return_value=SimpleNamespace(returncode=1,stdout='',stderr='password=private')):
            result=audit.report('/test')
        self.assertEqual(result['status'],'UNVERIFIED')
        self.assertNotIn('private',json.dumps(result))
        self.assertEqual(result['broker_writes'],0)


if __name__=='__main__':unittest.main()
