import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest


class SnapshotTests(unittest.TestCase):
    def test_private_paths_links_and_credentials_are_excluded(self):
        spec = importlib.util.spec_from_file_location('snapshot', Path(__file__).parents[1] / 'source-snapshot.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'source'; root.mkdir()
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            (root / 'app.py').write_text('print(1)')
            (root / '.env').write_text('PASSWORD=hidden')
            (root / '.local').mkdir()
            (root / '.local/key.json').write_text('hidden')
            (root / 'linked.py').symlink_to(root / '.env')
            (root / 'settings.py').write_text('api_key = "sk-' + 'x' * 30 + '"')
            target = Path(d) / 'snapshot'
            module.snapshot(root, target)
            self.assertEqual([p.name for p in target.rglob('*') if p.is_file()], ['app.py'])
            (root / 'app.py').unlink()
            module.snapshot(root, target)
            self.assertFalse((target / 'app.py').exists())
