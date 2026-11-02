import json
from pathlib import Path
import re
import unittest

ROOT=Path(__file__).resolve().parents[1]


class DonchianReaderImageTests(unittest.TestCase):
    def test_observer_and_binding_preflight_use_the_same_locked_image(self):
        path='apps/trading-platform/research-continuation-2.patch.yaml'
        text=(ROOT/path).read_text()
        images=re.findall(r'^\s*image:\s*"([^"]+)"',text,re.MULTILINE)
        self.assertEqual(len(images),2)
        self.assertEqual(images[0],images[1])
        record=next(r for r in json.loads((ROOT/'frozen-research-images.json').read_text())['workloads'] if r['path']==path)
        self.assertEqual(set(images),set(record['images']))
        self.assertFalse(record['reader_maintenance']['checkpoint_reset'])
        self.assertIn('python, /recovery/preflight.py',text)
        self.assertIn('default_transaction_read_only=on',text)
