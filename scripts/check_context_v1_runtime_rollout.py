"""Validate rendered storage cutover wiring; this does not certify image contents."""
import argparse
import json
import re
from pathlib import Path
import yaml

MAIN='mt5-native-bridge-main-runtime'


def validate(documents,release=None):
    resources={(d['kind'],d['metadata']['name']):d for d in documents if d}
    main=resources[('Deployment',MAIN)]
    assert main['spec']['strategy']['type']=='Recreate','old source containers must stop before import'
    spec=main['spec']['template']['spec']
    containers={c['name']:c for c in spec['containers']}
    image=containers['context-paper']['image']
    assert re.fullmatch(r'ghcr.io/strat-relay/trading-platform-runtime@sha256:[a-f0-9]{64}',image),'immutable main runtime digest required'
    if release:
        refs={entry['name']:entry['reference'] for entry in release['images']}
        assert image==refs['trading-platform-runtime'],'runtime must match promoted release manifest'
    init=spec['initContainers']
    assert [c['name'] for c in init[:2]]==['context-v1-schema','context-v1-import'],'schema must precede import'
    for container in init[:2]:
        assert container['image']==image,'init containers must inherit the promoted runtime digest'
        assert container['securityContext']['readOnlyRootFilesystem'] is True
        assert container['securityContext']['allowPrivilegeEscalation'] is False
    schema=' '.join(init[0]['args'])
    assert 'context_structure_retrace.runtime_store import RuntimeStore' in schema,'new runtime capability gate missing'
    assert "version='098'" in schema,'migration 098 gate missing'
    assert init[1]['command']==['python','-m','context_structure_retrace.bootstrap_runtime']
    assert init[1]['args']==['--offline']
    assert any(v['name']=='runtime' and v['mountPath']=='/work' and v.get('readOnly') is True
               for v in init[1]['volumeMounts']),'source PVC must be read-only during import'
    for name in ('context-paper','orchestrator-shadow','context-post-exit-observer'):
        env={item['name']:item for item in containers[name]['env']}
        assert env['CONTEXT_V1_STORAGE_MODE']['value']=='POSTGRES',name+' storage mode mismatch'
        assert env['CONTEXT_V1_RUNTIME_STREAM']['value']=='context-v1:phase6',name+' stream mismatch'
        assert env['CONTEXT_V1_RUNTIME_INSTANCE']['value']=='phase6',name+' instance mismatch'
    env={e['name']:e for e in containers['orchestrator-shadow']['env']}
    assert env['SIGNAL_CONFIDENCE_CAPTURE']['value']=='true'
    cron=resources[('CronJob','signal-confidence-reconcile')]['spec']
    assert cron['concurrencyPolicy']=='Forbid'
    assert cron['schedule']=='* * * * *'
    job=cron['jobTemplate']['spec']
    assert job['activeDeadlineSeconds']<=300
    pod=job['template']['spec']
    assert pod['automountServiceAccountToken'] is False
    worker=pod['containers'][0]
    assert worker['image']==image,'confidence reconciliation must use the promoted runtime digest'
    assert worker['command']==['python','-m','platform_api.signal_quality.backfill_confidence']
    assert worker['args']==['--limit','100']
    for name in ('signal-confidence-reconcile-egress','signal-confidence-reconcile-postgres-ingress'):
        assert ('NetworkPolicy',name) in resources,'missing confidence database network path'
    return {'configuration_valid':True,'runtime_image':image,
            'backend_release_prerequisite':'platform-tools PR #344 main release and image promotion must be verified before merge'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('rendered',type=Path)
    parser.add_argument('--release',type=Path)
    args=parser.parse_args()
    documents=list(yaml.safe_load_all(args.rendered.read_text()))
    release=json.loads(args.release.read_text()) if args.release else None
    print(json.dumps(validate(documents,release),indent=2))


if __name__=='__main__':main()
