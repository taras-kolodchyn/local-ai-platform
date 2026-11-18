"""Pinned query embeddings must use the resolved runtime artifact identity."""
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class RuntimeIdentityTests(unittest.TestCase):
    def test_shutdown_compose_can_parse_without_generated_runtime_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(ROOT/'compose.yaml',root/'compose.yaml')
            result = subprocess.run(['docker','compose','--file',str(root/'compose.yaml'),
                '--env-file',str(ROOT/'.env.example'),'config','--quiet'],cwd=root,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertFalse((root/'.local').exists())

    def test_identity_file_uses_resolved_embedding_and_rejects_bad_metadata(self):
        module = runpy.run_path(str(ROOT / 'scripts/model-config.py'))
        self.assertIn('write_runtime_identity', module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env.example').write_text('QWEN_MODEL=ai/coder:v1\nEMBEDDING_MODEL=ai/embed:v2\n'
                                             'QWEN_CONTEXT_SIZE=65536\nEMBEDDING_DIMENSIONS=1024\n')
            calls = []
            def inspect(reference):
                calls.append(reference)
                return {'id': 'sha256:' + 'a' * 64}
            path = module['write_runtime_identity'](root, inspect=inspect)
            self.assertEqual(calls, ['ai/embed:v2'])
            self.assertEqual(path.read_text(), 'EMBEDDING_ARTIFACT_ID=sha256:' + 'a' * 64 + '\n')
            original = path.read_bytes()
            with self.assertRaises(ValueError):
                module['write_runtime_identity'](root, inspect=lambda _: {'id': 'not-an-artifact'})
            self.assertEqual(path.read_bytes(), original)
