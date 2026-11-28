"""Stage a concrete four-variant run on the shared library orchestrator. No cluster calls."""
import argparse
import json
from pathlib import Path
from stage_mt5_counterparts import stage as stage_bundle


def stage(bundle, root, *, now=None):
    cfg = json.loads((bundle/'run.json').read_text())
    ready = json.loads((bundle/'readiness.json').read_text())
    expected = {'sr_retest_'+k+'@V2' for k in ('structural','volatility','trend','fvg')}
    if set(cfg['keys']) != expected or len(cfg['keys']) != 4:
        raise ValueError('four explicit SR hypothesis selectors required')
    if cfg['symbols'] != {'BTCUSD':'BTCUSDm','XAUUSD':'XAUUSDm','USDJPY':'USDJPYm'}:
        raise ValueError('reviewed SR instrument scope required')
    workload = json.loads((bundle/'workload.json').read_text())
    deps = [d for d in workload['items'] if d['kind']=='Deployment']
    if len(deps)!=1 or deps[0]['metadata']['name']!='sr-hypotheses-v2-observer':
        raise ValueError('one observer and no per-strategy orchestrator required')
    shared_path = root/'apps/trading-platform/library-orchestrator-unified/workload.json'
    shared = json.loads(shared_path.read_text())
    dep = next(d for d in shared['items'] if d['kind']=='Deployment' and d['metadata']['name']=='library-orchestrator-unified')
    container = dep['spec']['template']['spec']['containers'][0]
    if container['name'] != 'orchestrator' or 'multi-orchestrator' not in container['command']:
        raise ValueError('review current shared publication worker before attachment')
    if '/config/sr-hypotheses/run.json' in container['command']:
        raise ValueError('SR attachment already exists; never overwrite a run')
    patch = json.loads((bundle/'shared_orchestrator_patch.json').read_text())
    expected_patch = [
        {'op':'replace','path':'/spec/template/spec/containers/0/image','value':ready['image']},
        {'op':'add','path':'/spec/template/spec/containers/0/command/-','value':'--manifest'},
        {'op':'add','path':'/spec/template/spec/containers/0/command/-','value':'/config/sr-hypotheses/run.json'},
        {'op':'add','path':'/spec/template/spec/containers/0/volumeMounts/-','value':{'name':'sr-hypotheses-manifest','mountPath':'/config/sr-hypotheses','readOnly':True}},
        {'op':'add','path':'/spec/template/spec/volumes/-','value':{'name':'sr-hypotheses-manifest','configMap':{'name':cfg['run_id']+'-manifest'}}}]
    if patch != expected_patch:
        raise ValueError('shared attachment must append one manifest and preserve other consumers')
    lock_path = root/'frozen-research-images.json'
    lock = json.loads(lock_path.read_text())
    observer_path = 'apps/trading-platform/'+cfg['run_id']+'/workload.json'
    if any(w['path']==observer_path for w in lock['workloads']):
        raise ValueError('run already locked')
    # Existing guarded stager handles activation Job, atomic registration,
    # network policy and collector evidence scope using Secret references only.
    dest = stage_bundle(bundle,root,now=now)
    (dest/'shared-orchestrator.patch.json').write_text(json.dumps(patch,indent=2)+'\n')
    top = root/'apps/trading-platform/kustomization.yaml'
    text = top.read_text()
    if text.count('patches:\n') != 1:raise ValueError('review Kustomize patch structure')
    relative = cfg['run_id']+'/shared-orchestrator.patch.json'
    attachment = ('  - target:\n      group: apps\n      version: v1\n      kind: Deployment\n'
                  '      name: library-orchestrator-unified\n    path: '+relative+'\n')
    top.write_text(text.replace('patches:\n','patches:\n'+attachment,1))
    lock['workloads'].append({'path':observer_path,'run_id':cfg['run_id'],
        'binding':ready['binding'],'status':'FRESH_RUN_RESEARCH_ONLY','images':[ready['image']]})
    lock_path.write_text(json.dumps(lock,indent=2)+'\n')
    return dest


if __name__=='__main__':
    p=argparse.ArgumentParser(__doc__)
    p.add_argument('--bundle',required=True,type=Path);p.add_argument('--root',required=True,type=Path)
    a=p.parse_args();print(stage(a.bundle,a.root))
