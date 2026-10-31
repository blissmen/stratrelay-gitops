from dataclasses import asdict,dataclass
from copy import deepcopy
from types import SimpleNamespace
from pathlib import Path
import json
import unittest
from donchian_binding_preflight import inspect_binding


@dataclass
class Config:
    run_id:str='donchian-test'
    keys:tuple=('donchian_breakout@V2',)


class BindingTests(unittest.TestCase):
    def test_mounted_guard_matches_reviewed_source(self):
        root=Path(__file__).resolve().parents[1]
        cm=json.loads((root/'apps/trading-platform/donchian-binding-preflight-20261031-r1.json').read_text())
        self.assertEqual(cm['data']['preflight.py'],(root/'scripts/donchian_binding_preflight.py').read_text())

    def test_complete_match_and_json_array_config_are_accepted_without_mutation(self):
        cfg=Config();stored={'binding':'binding','streams':{'one':{}},
            'manifest':{'artifact':'artifact','config':{'run_id':cfg.run_id,'keys':list(cfg.keys)}}}
        before=deepcopy(stored)
        result=inspect_binding(cfg,stored,SimpleNamespace(binding='binding',artifact='artifact'))
        self.assertEqual(result['status'],'BINDING_MATCH')
        self.assertEqual(stored,before)
        self.assertEqual(result['database_writes'],0)

    def test_rewritten_binding_does_not_hide_artifact_or_config_drift(self):
        cfg=Config();observer=SimpleNamespace(binding='new',artifact='new-artifact')
        stored={'binding':'new','manifest':{'artifact':'old-artifact','config':asdict(cfg)}}
        self.assertEqual(inspect_binding(cfg,stored,observer)['status'],'BINDING_MISMATCH')
        stored['manifest']['artifact']='new-artifact'
        stored['manifest']['config']['run_id']='different'
        self.assertEqual(inspect_binding(cfg,stored,observer)['status'],'BINDING_MISMATCH')
        self.assertEqual(inspect_binding(cfg,None,observer)['status'],'RUN_MISSING')


if __name__=='__main__':unittest.main()
