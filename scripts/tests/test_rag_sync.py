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
            target = root / 'export'
            target.mkdir()
            sync.export_tree(origin, tip, target)
            self.assertEqual(list(target.iterdir()), [])
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

    def test_interrupted_disconnect_pauses_poll_and_can_resume(self):
        from contextlib import nullcontext
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / 'connection'
            directory.mkdir()
            path = directory / 'state.json'
            sync.save(path, {'scope': 'connected/github/o/r', 'branch': 'main'})
            with patch.object(sync, 'STATE', root), patch.dict(sync.PROVISION, vector_registry_lock=nullcontext), \
                 patch.object(sync, 'remove_index', side_effect=[RuntimeError('offline'), None]), \
                 patch.object(sync, 'git') as git, patch.object(sync, 'loaded', return_value=False), \
                 patch.object(sync, 'PLIST', root / 'job.plist'):
                with self.assertRaises(RuntimeError):
                    sync.disconnect(directory, 'connected/github/o/r', 'main')
                sync.synchronize(directory)
                sync.disconnect(directory, 'connected/github/o/r', 'main')
                sync.synchronize(directory)
                sync.disconnect(directory, 'connected/github/o/r', 'main')
                git.assert_not_called()
                self.assertFalse(path.exists())

    def test_disconnect_preserves_another_branch_and_schedule(self):
        from contextlib import nullcontext
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scope = 'connected/github/o/r'
            for branch in ('main', 'stable'):
                directory = root / branch
                directory.mkdir()
                sync.save(directory / 'state.json', {'scope': scope, 'branch': branch})
            with patch.object(sync, 'STATE', root), patch.dict(sync.PROVISION, vector_registry_lock=nullcontext), \
                 patch.object(sync, 'remove_index') as remove, patch.object(sync, 'loaded') as loaded:
                sync.disconnect(root / 'main', scope, 'main')
                self.assertEqual(remove.call_args.args[0]['branch'], 'main')
                self.assertTrue((root / 'stable/state.json').exists())
                loaded.assert_not_called()
