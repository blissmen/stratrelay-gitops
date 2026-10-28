#!/usr/bin/env python3
"""Generate a review-only Swing rollout from a clean, exact runtime source checkout.

No Kubernetes, database, broker, NATS, image pull, merge or reconciliation calls.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess


def main():
    p=argparse.ArgumentParser(__doc__)
    p.add_argument('--platform-root',required=True,type=Path)
    p.add_argument('--source-commit',required=True)
    p.add_argument('--image',required=True)
    p.add_argument('--image-labels',required=True,type=Path,
        help='Saved docker image inspect JSON or OCI labels for this digest')
    p.add_argument('--run-id',required=True)
    p.add_argument('--activation-time',required=True,type=int)
    p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();root=a.platform_root.expanduser().resolve()
    if not re.fullmatch('[0-9a-f]{40}',a.source_commit):p.error('full source commit required')
    if not re.fullmatch(r'ghcr\.io/strat-relay/trading-platform-runtime@sha256:[0-9a-f]{64}',a.image):p.error('immutable runtime digest required')
    def git(*args):return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
    if git('rev-parse','HEAD')!=a.source_commit:p.error('checkout differs from release source')
    if git('status','--porcelain','--untracked-files=normal'):p.error('clean isolated source checkout required')
    labels=json.loads(a.image_labels.expanduser().read_text())
    if isinstance(labels,list):
        if len(labels)!=1:p.error('inspect exactly the reviewed runtime image')
        item=labels[0]
        if a.image not in item.get('RepoDigests',[]):p.error('image inspection does not match requested digest')
        labels=item.get('Config',{}).get('Labels',{})
    if labels.get('org.opencontainers.image.revision')!=a.source_commit:p.error('runtime image source label mismatch')
    env=dict(os.environ);env['PYTHONPATH']=str(root)
    command=['python3','-m','research.swing_position_forward','plan','--run-id',a.run_id,
        '--activation-time',str(a.activation_time),'--source-commit',a.source_commit,
        '--image',a.image,'--output',str(a.output.expanduser().resolve())]
    subprocess.run(command,cwd=root,env=env,check=True)
    print(json.dumps({'status':'PREPARED_NOT_ACTIVATED','output':str(a.output),
        'execution_eligible':False,'broker_writes':0}))

if __name__=='__main__':main()
