import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('rag_sync', Path(__file__).resolve().parents[1] / 'rag-sync.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class BranchSyncTests(unittest.TestCase):
    def test_github_source_accepts_only_repository_identifiers(self):
        self.assertEqual(sync.source('Owner/Project'), sync.source('https://github.com/owner/project.git'))
        for value in ('../project', 'https://user:token@github.com/owner/project', 'ext::example', ''):
            with self.assertRaises(ValueError):
                sync.source(value)

    def test_resume_after_failure_and_skip_unchanged_tip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            origin = root / 'repo'
            subprocess.run(['git', 'init', '-qb', 'main', str(origin)], check=True)
            git = ['git', '-C', str(origin), '-c', 'user.name=Test', '-c', 'user.email=test@localhost']
            subprocess.run([*git, 'commit', '-qm', 'Initial', '--allow-empty'], check=True)
            tip = subprocess.check_output([*git, 'rev-parse', 'HEAD'], text=True).strip()
            with patch.object(sync, 'publish', side_effect=RuntimeError('ingestion failed')):
                with self.assertRaises(RuntimeError):
                    sync.synchronize(root, {'branch': 'main', 'scope': 'test', 'origin': str(origin)})
            state = json.loads((root / 'state.json').read_text())
            self.assertEqual(state['observed'], tip)
            self.assertNotIn('indexed', state)
            with patch.object(sync, 'publish', return_value={'indexed': tip}) as publish:
                sync.synchronize(root)
                sync.synchronize(root)
                publish.assert_called_once()
            self.assertNotIn('error', json.loads((root / 'state.json').read_text()))
