from copy import deepcopy
from dataclasses import asdict,dataclass
from pathlib import Path
from types import SimpleNamespace
import json
import unittest
from continuation_binding_preflight import RUNS,validate


@dataclass
class Config:
    run_id:str
    keys:tuple
    symbols:dict
    transport:str='POLL'


class ContinuationTests(unittest.TestCase):
    def fixture(self,run):
        config=Config(run,RUNS[run],{'BTCUSD':'BTCUSDm','XAUUSD':'XAUUSDm','USDJPY':'USDJPYm'})
        state={'binding':'binding','manifest':{'config':asdict(config),'artifact':'artifact'},'streams':{'history':{}}}
        return config,state,SimpleNamespace(binding='binding',artifact='artifact')

    def test_both_valid_continuations_preserve_state(self):
        for run in RUNS:
            cfg,state,observer=self.fixture(run);before=deepcopy(state)
            self.assertEqual(validate(cfg,state,observer)['status'],'BINDING_MATCH')
            self.assertEqual(state,before)

    def test_mismatch_missing_state_and_event_cutover_stay_blocked(self):
        cfg,state,observer=self.fixture(next(iter(RUNS)))
        state['binding']='different'
        self.assertEqual(validate(cfg,state,observer)['status'],'BINDING_MISMATCH')
        self.assertEqual(validate(cfg,None,observer)['status'],'RUN_MISSING')
        state['activation_attestation']={'transport':'JETSTREAM'}
        self.assertEqual(validate(cfg,state,observer)['status'],'BLOCKED_EVENT_CUTOVER')
        cfg.transport='JETSTREAM'
        with self.assertRaises(ValueError):validate(cfg,state,observer)

    def test_configmap_matches_reviewed_code(self):
        root=Path(__file__).resolve().parents[1]
        cm=json.loads((root/'apps/trading-platform/continuation-binding-preflight-20261031-r2.json').read_text())
        for name,content in cm['data'].items():self.assertEqual(content,(root/'scripts'/name).read_text())


if __name__=='__main__':unittest.main()
