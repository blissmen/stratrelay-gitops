"""Validate checkpoint-bound runtime pins without credentials or Kubernetes access."""
import json
from pathlib import Path
import re
import sys


def check(root):
    root = Path(root)
    lock = json.loads((root / 'frozen-research-images.json').read_text())
    failures = []
    for workload in lock['workloads']:
        path = root / workload['path']
        text = path.read_text()
        if path.suffix == '.json':
            images = set()
            def visit(value):
                if isinstance(value, dict):
                    if isinstance(value.get('image'), str):
                        images.add(value['image'])
                    for item in value.values():
                        visit(item)
                elif isinstance(value, list):
                    for item in value:
                        visit(item)
            visit(json.loads(text))
        else:
            images = set(re.findall(r'^\s*image:\s*[\x22\x27]?(ghcr\.io/strat-relay/[^\s\x22\x27]+)', text, re.MULTILINE))
        if images != set(workload['images']):
            failures.append(workload['path'] + ': frozen image differs; prove checkpoint binding before updating the lock')
    return failures


if __name__ == '__main__':
    failures = check(Path(__file__).resolve().parents[1])
    for failure in failures:
        print(failure)
    raise SystemExit(bool(failures))
