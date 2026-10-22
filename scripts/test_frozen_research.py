import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('guard', Path(__file__).with_name('check_frozen_research.py'))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
ROOT = Path(__file__).resolve().parents[1]


class FrozenPinTest(unittest.TestCase):
    def test_current_approved_pins(self):
        self.assertEqual(guard.check(ROOT), [])

    def test_each_locked_yaml_and_json_repin_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(ROOT / 'frozen-research-images.json', root / 'frozen-research-images.json')
            lock = json.loads((root / 'frozen-research-images.json').read_text())
            for entry in lock['workloads']:
                target = root / entry['path']
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / entry['path'], target)
            for entry in lock['workloads']:
                target = root / entry['path']
                original = target.read_text()
                target.write_text(original.replace(entry['images'][0], entry['images'][0].split('@')[0] + '@sha256:' + '0' * 64))
                self.assertEqual(len(guard.check(root)), 1, entry['path'])
                target.write_text(original)


if __name__ == '__main__':
    unittest.main()
